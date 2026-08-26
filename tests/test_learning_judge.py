"""Epic 140 切片 2 验收测试：L3 LLM-as-judge 调度。

覆盖：
- deterministic 降级评分（无 LLM 配置）：schema 完整（5 子分 + judge_quality + rationale）
- judge_task 回填：judge_json.judge_pending=False + judge_provider + score 按复合公式重算
- LLM judge（mock urllib）：合法 JSON 解析回填（provider=llm）
- LLM 非法 JSON / 网络失败：fallback deterministic，不崩溃不外泄
- 手动触发端点 POST /api/learning/judge/{task_id}
- judge 状态端点 GET /api/learning/judge/status（llm_enabled / daily quota）
- daily quota 用尽后自动降级 deterministic
- 非终态 task judge → None（无 outcome 不评判）
- build_judge_input 聚合评论 / 状态历史 / spec
"""
import json
import os
import sys
import tempfile
import unittest.mock as mock

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_JUDGE_AUTO"] = "0"  # 测试禁用后台线程，同步验证

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

import pytest

from agentboard import service
from agentboard.api import app  # 顶部绑定 app：测试内延迟 import 在批量时会拿到
#                               # 最后收集文件的版本（DB env 错位 → task id 冲突 404）
from agentboard.database import SessionLocal, init_db
from agentboard.core.common.enums import Status, StatusReason
# 预导入 learning.service：批量跑时其他文件 del sys.modules 重载会清掉 learning 包，
# 导致 set_status 内部延迟相对导入失败（被 _record_learning_outcome 吞掉、outcome 不落库）。
# 与 tests/test_learning_outcome.py 顶部预导入模式保持一致。
from agentboard.features.learning import service as ls
from agentboard.features.learning import judge as lj
from agentboard.features.learning import judge_prompt
from agentboard.features.learning.models import TaskOutcome


@pytest.fixture
def session():
    init_db()
    # 执行时预热 learning 模块：批量跑时其他文件收集期 del sys.modules 会清掉
    # learning 包，导致 set_status 内部延迟相对导入失败（被 _record_learning_outcome
    # 吞掉、outcome 不落库）。顶部 import 在收集期执行会被后续文件重载覆盖，须在此处。
    import agentboard.features.learning.service  # noqa: F401
    s = SessionLocal()
    yield s
    s.close()


def _mk(s, name="u1", proj="p1"):
    import uuid
    suffix = uuid.uuid4().hex[:8]
    u = service.register_user(s, username=f"{name}_{suffix}", password="password123")
    p = service.create_project(s, name=f"{proj}_{suffix}")
    e = service.create_epic(s, project_id=p.id, title=f"E-{suffix}")
    st = service.create_story(s, epic_id=e.id, title=f"S-{suffix}")
    return u, p, st


def _mk_task(s, u, p, st, spec=""):
    t = service.create_task(s, project_id=p.id, story_id=st.id, title="T1",
                            description="实现功能，附单元测试与回归验证")
    t.assignee_id = u.id
    t.spec = spec or "spec: 实现核心路径，含测试覆盖与回归验证"
    s.commit()
    s.refresh(t)
    return t


def _done(s, t, u, reason=StatusReason.COMPLETED):
    service.set_status(s, t.id, Status.IN_PROGRESS, changed_by=u.id)
    service.set_status(s, t.id, Status.IN_REVIEW, changed_by=u.id)
    return service.set_status(s, t.id, Status.DONE, changed_by=u.id, status_reason=reason)


def _outcome(s, t):
    return s.query(TaskOutcome).filter(TaskOutcome.task_id == t.id).one()


# ---------- deterministic 降级 ----------

def test_deterministic_judge_schema(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    metrics = lj.build_judge_input(session, t, {})
    # 直接用最小输入验证 schema（judge_task 内部会先算完整 metrics）
    from agentboard.features.learning import service as ls
    full = ls.compute_process_metrics(session, t)
    inp = lj.build_judge_input(session, t, full)
    result = lj.deterministic_judge(inp, full)
    for k in judge_prompt.JUDGE_KEYS:
        assert k in result, f"missing {k}"
        assert 0.0 <= result[k] <= 1.0
    assert 0.0 <= result["judge_quality"] <= 1.0
    assert result["provider"] == "deterministic"
    assert result["rationale"]


def test_judge_task_backfills_and_recomputes_score(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    before = _outcome(session, t)
    assert json.loads(before.judge_json)["judge_pending"] is True

    result = lj.judge_task(session, t.id)
    session.commit()
    assert result is not None
    assert result["provider"] == "deterministic"

    session.refresh(before)
    jj = json.loads(before.judge_json)
    assert jj["judge_pending"] is False
    assert jj["judge_provider"] == "deterministic"
    assert "judge_quality" in jj and "rationale" in jj
    for k in judge_prompt.JUDGE_KEYS:
        assert k in jj

    # 复合公式：score = 0.4*pass + 0.3*judge_quality + 0.2*cycle + 0.1*reason
    expect = round(
        0.4 * jj["pass_first_try"] + 0.3 * jj["judge_quality"]
        + 0.2 * jj["cycle_efficiency"] + 0.1 * jj["reason_quality"], 4,
    )
    assert abs(before.score - expect) < 1e-4


def test_judge_task_idempotent_rerun(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    lj.judge_task(session, t.id)
    session.commit()
    first = _outcome(session, t).score
    lj.judge_task(session, t.id)  # 重复触发不报错、不新增行
    session.commit()
    assert _outcome(session, t).score == first
    assert len(session.query(TaskOutcome).filter(TaskOutcome.task_id == t.id).all()) == 1


def test_judge_task_none_for_non_terminal(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    assert lj.judge_task(session, t.id) is None


def test_judge_task_none_for_unknown_task(session):
    assert lj.judge_task(session, 999999) is None


# ---------- LLM judge（mock urllib） ----------

class _FakeResp:
    """真实 context manager 响应（MagicMock 的 __enter__ 会返回新 mock 导致 read 失效）。"""

    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _llm_json_response(llm_payload: dict) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": json.dumps(llm_payload)}}]}
    ).encode("utf-8")


def test_llm_judge_success(session, monkeypatch):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    monkeypatch.setenv("AGENTBOARD_JUDGE_API_URL", "https://mock-llm.example/v1")
    monkeypatch.setenv("AGENTBOARD_JUDGE_API_KEY", "sk-test")
    monkeypatch.setenv("AGENTBOARD_JUDGE_MODEL", "mock-model")

    llm_payload = {
        "spec_coverage": 0.95, "code_quality": 0.88, "test_coverage": 0.80,
        "spec_drift": 0.92, "reason_quality": 0.90,
        "judge_quality": 0.89, "rationale": "覆盖 spec 全部要点，测试充分",
    }

    def fake_urlopen(req, timeout=None):
        import urllib.request
        body = req.data.decode("utf-8") if isinstance(req.data, bytes) else str(req.data or "")
        assert "mock-model" in body
        assert req.get_header("Authorization") == "Bearer sk-test"
        return _FakeResp(_llm_json_response(llm_payload))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = lj.judge_task(session, t.id)
    session.commit()
    assert result is not None
    assert result["provider"] == "llm"

    jj = json.loads(_outcome(session, t).judge_json)
    assert jj["judge_provider"] == "llm"
    assert jj["spec_coverage"] == 0.95
    assert abs(jj["judge_quality"] - 0.89) < 1e-4
    assert jj["judge_pending"] is False


def test_llm_judge_bad_json_falls_back(session, monkeypatch):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    monkeypatch.setenv("AGENTBOARD_JUDGE_API_URL", "https://mock-llm.example/v1")

    def fake_urlopen(req, timeout=None):
        return _FakeResp(json.dumps(
            {"choices": [{"message": {"content": "不是 JSON！"}}]}
        ).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = lj.judge_task(session, t.id)
    session.commit()
    assert result is not None
    assert result["provider"] == "deterministic"  # 非法 JSON 降级
    jj = json.loads(_outcome(session, t).judge_json)
    assert jj["judge_provider"] == "deterministic"


def test_llm_judge_network_error_falls_back(session, monkeypatch):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    monkeypatch.setenv("AGENTBOARD_JUDGE_API_URL", "https://mock-llm.example/v1")

    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = lj.judge_task(session, t.id)  # 网络失败不崩溃
    session.commit()
    assert result is not None
    assert result["provider"] == "deterministic"


def test_llm_judge_missing_dimensions_are_filled(session, monkeypatch):
    """LLM 返回缺维度时用其余维度均值补全，不报错。"""
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    monkeypatch.setenv("AGENTBOARD_JUDGE_API_URL", "https://mock-llm.example/v1")

    partial = {"spec_coverage": 0.9, "code_quality": 0.8}

    def fake_urlopen(req, timeout=None):
        return _FakeResp(_llm_json_response(partial))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = lj.judge_task(session, t.id)
    session.commit()
    assert result is not None
    assert result["provider"] == "llm"
    jj = json.loads(_outcome(session, t).judge_json)
    for k in judge_prompt.JUDGE_KEYS:
        assert k in jj  # 缺失维度已用均值补全


# ---------- daily quota ----------

def test_daily_quota_exhausted_falls_back(session, monkeypatch):
    """今日 llm quota 用尽（含其他 task 占用）→ 自动降级 deterministic。

    自含基线：人为把另一条 outcome 标记为 judge_provider=llm，
    不依赖本文件其他测试留下的数据（共享同一临时 DB）。
    """
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    monkeypatch.setenv("AGENTBOARD_JUDGE_API_URL", "https://mock-llm.example/v1")
    monkeypatch.setenv("AGENTBOARD_JUDGE_DAILY_QUOTA", "1")

    # 人为制造一条今日 llm outcome，占满 quota
    t0 = _mk_task(session, u, p, st)
    _done(session, t0, u)
    out0 = _outcome(session, t0)
    jj0 = json.loads(out0.judge_json)
    jj0["judge_provider"] = "llm"
    out0.judge_json = json.dumps(jj0)
    session.commit()

    llm_payload = {k: 0.9 for k in judge_prompt.JUDGE_KEYS}
    llm_payload["judge_quality"] = 0.9
    llm_payload["rationale"] = "x"

    def fake_urlopen(req, timeout=None):
        raise AssertionError("quota 已用尽，不应再调用 LLM")
        return _FakeResp(_llm_json_response(llm_payload))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert lj._llm_daily_used(session) >= 1  # quota 已占用
    result = lj.judge_task(session, t.id)    # → deterministic，不再调用 LLM
    session.commit()
    assert result["provider"] == "deterministic"
    jj = json.loads(_outcome(session, t).judge_json)
    assert jj["judge_provider"] == "deterministic"


def test_judge_status_endpoint(session, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_JUDGE_API_URL", "")
    assert lj.is_judge_llm_enabled() is False
    assert lj.daily_llm_quota() == 200
    monkeypatch.setenv("AGENTBOARD_JUDGE_DAILY_QUOTA", "abc")  # 非法 → 默认
    assert lj.daily_llm_quota() == 200


# ---------- build_judge_input ----------

def test_build_judge_input_aggregates_comments_and_history(session):
    from agentboard.features.learning import service as ls

    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    # 加一条评审评论
    service.create_comment(session, task_id=t.id, author="reviewer",
                           content="代码结构清晰，测试覆盖充分，建议通过")
    metrics = ls.compute_process_metrics(session, t)
    inp = lj.build_judge_input(session, t, metrics)
    assert inp["title"] == "T1"
    assert inp["status"] == Status.DONE
    assert inp["status_reason"] == StatusReason.COMPLETED
    assert len(inp["transitions"]) >= 2  # in_progress → done
    assert len(inp["comments"]) == 1
    assert inp["comments"][0]["author"] == "reviewer"
    assert "测试" in inp["spec"]


# ---------- 手动触发端点 ----------

def test_judge_api_manual_trigger(session):
    from fastapi.testclient import TestClient

    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)

    with TestClient(app) as client:
        # 无 token 本地模式宽容放行（REQUIRE_AUTH=0）
        resp = client.post(f"/api/learning/judge/{t.id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["task_id"] == t.id
        assert data["provider"] == "deterministic"
        assert 0.0 <= data["judge_quality"] <= 1.0

        # 非终态 → 404
        t2 = _mk_task(session, u, p, st)
        service.set_status(session, t2.id, Status.IN_PROGRESS, changed_by=u.id)
        resp2 = client.post(f"/api/learning/judge/{t2.id}")
        assert resp2.status_code == 404

        # 状态端点
        resp3 = client.get("/api/learning/judge/status")
        assert resp3.status_code == 200
        status_data = resp3.json()
        assert "llm_enabled" in status_data
        assert status_data["provider"] in ("llm", "deterministic")


def test_judge_updates_leaderboard(session):
    from agentboard.features.learning import service as ls

    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    lj.judge_task(session, t.id)
    session.commit()
    rows = ls.agent_leaderboard(session, project_id=p.id)
    row = [r for r in rows if r["agent_id"] == u.id][0]
    assert 0.0 <= row["avg_score"] <= 1.0
    # judge 后 score 反映真实 judge_quality（不再是 0.75 中性占位）
    jj = json.loads(_outcome(session, t).judge_json)
    expect = round(
        0.4 * jj["pass_first_try"] + 0.3 * jj["judge_quality"]
        + 0.2 * jj["cycle_efficiency"] + 0.1 * jj["reason_quality"], 4,
    )
    assert abs(row["avg_score"] - expect) < 1e-4
