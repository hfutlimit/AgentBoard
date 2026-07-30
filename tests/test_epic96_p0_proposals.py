"""Epic 96 P0 — Proposal 澄清回路：模型 / 状态机 / REST API 全链路测试。

覆盖：
1. 三表模型注册与 facade 导出、状态机表自洽
2. Proposal CRUD
3. 状态机合法链路（draft→queued→analyzing→awaiting→answered→converged→story_created）
   与非法迁移拒绝（400）
4. 轮次问答：仅 analyzing 可提问；作答后自动 awaiting→answered；unsure 标记
5. (proposal_id, round_no) 唯一约束 → at-least-once 重投幂等
6. 失败态回退重投（analyzing→failed→queued）、Worker 轮询端点、删除级联

运行：
    PYTHONPATH=. python -m pytest tests/test_epic96_p0_proposals.py -q

注意：本模块用**真实 uvicorn 子进程 + httpx** 而非进程内 TestClient——
api.py 的 audit_log_middleware 基于 BaseHTTPMiddleware 且会 `await request.body()`，
在 TestClient 下会与下游端点争抢 receive 通道导致请求挂死（与 test_backend_flow.py 同因）。
自带独立临时 SQLite，避免与其它测试共享 engine。
"""
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 独立临时数据库（与其它测试隔离）
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import models  # noqa: E402
from agentboard.database import init_db  # noqa: E402
from agentboard.domains.proposals.models import (  # noqa: E402
    PROPOSAL_TRANSITIONS, Proposal, ProposalQuestion, ProposalRound, ProposalStatus,
)

init_db()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_ready(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(base + "/api/meta", timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"API 服务在 {base} 启动超时")


@pytest.fixture(scope="module")
def ctx():
    """真实拉起 API，建 admin 用户 + 项目 + epic + story，返回带鉴权头的上下文。"""
    port = _free_port()
    proc = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        c = httpx.Client(base_url=base, timeout=30)
        r = c.post("/api/auth/register",
                   json={"username": "p96admin", "password": "p96admin123"})
        assert r.status_code in (201, 409), r.text
        r = c.post("/api/auth/login",
                   json={"username": "p96admin", "password": "p96admin123"})
        assert r.status_code == 200, r.text
        c.headers.update({"Authorization": f"Bearer {r.json()['token']}"})

        r = c.post("/api/projects", json={"name": "Epic96 P0 项目"})
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]
        r = c.post(f"/api/projects/{pid}/epics", json={"title": "澄清回路"})
        assert r.status_code in (200, 201), r.text
        eid = r.json()["id"]
        r = c.post(f"/api/epics/{eid}/stories", json={"title": "P0 Story"})
        assert r.status_code in (200, 201), r.text
        sid = r.json()["id"]

        yield {"c": c, "project_id": pid, "epic_id": eid, "story_id": sid}
        c.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _new_proposal(ctx, title="澄清一个模糊需求"):
    r = ctx["c"].post("/api/proposals", json={
        "project_id": ctx["project_id"], "title": title,
        "content": "希望做一个能自动整理周报的东西",
    })
    assert r.status_code == 201, r.text
    return r.json()


def _set_status(ctx, pid, status, error=None):
    body = {"status": status}
    if error is not None:
        body["error"] = error
    return ctx["c"].put(f"/api/proposals/{pid}/status", json=body)


# ---------------- 1. 模型与状态机表（纯单元，无需服务） ----------------

def test_models_registered_and_exported():
    tables = models.Base.metadata.tables
    for name in ("proposals", "proposal_rounds", "proposal_questions"):
        assert name in tables, f"{name} 未注册到 metadata"
    assert models.Proposal is Proposal
    assert models.ProposalRound is ProposalRound
    assert models.ProposalQuestion is ProposalQuestion
    assert models.ProposalStatus is ProposalStatus


def test_state_machine_table_is_closed():
    """状态机表的每个目标状态都必须是合法枚举值，且 story_created 为终态。"""
    valid = set(ProposalStatus)
    for src, targets in PROPOSAL_TRANSITIONS.items():
        assert src in valid
        assert targets <= valid, f"{src} 指向了非法状态 {targets - valid}"
    assert PROPOSAL_TRANSITIONS[ProposalStatus.STORY_CREATED] == set()
    # 每个非终态都可达 failed 或 story_created，避免出现悬空状态
    assert PROPOSAL_TRANSITIONS[ProposalStatus.FAILED] == {
        ProposalStatus.QUEUED, ProposalStatus.DRAFT,
    }


# ---------------- 2. CRUD ----------------

def test_create_and_get_proposal(ctx):
    p = _new_proposal(ctx)
    assert p["status"] == "draft"
    assert p["current_round"] == 0
    assert p["project_id"] == ctx["project_id"]

    r = ctx["c"].get(f"/api/proposals/{p['id']}")
    assert r.status_code == 200
    assert r.json()["title"] == "澄清一个模糊需求"


def test_list_and_filter_proposals(ctx):
    _new_proposal(ctx, title="待过滤的提案")
    r = ctx["c"].get("/api/proposals", params={"project_id": ctx["project_id"]})
    assert r.status_code == 200
    assert len(r.json()) >= 2

    r = ctx["c"].get("/api/proposals",
                     params={"project_id": ctx["project_id"], "status": "draft"})
    assert r.status_code == 200
    assert all(x["status"] == "draft" for x in r.json())

    r = ctx["c"].get("/api/proposals", params={"status": "nope"})
    assert r.status_code == 422


def test_update_proposal_content(ctx):
    p = _new_proposal(ctx, title="待编辑")
    r = ctx["c"].patch(f"/api/proposals/{p['id']}", json={
        "title": "已编辑标题", "content": "补充了更多背景",
    })
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "已编辑标题"
    assert r.json()["content"] == "补充了更多背景"


# ---------------- 3. 状态机 ----------------

def test_happy_path_full_state_machine(ctx):
    pid = _new_proposal(ctx, title="完整链路")["id"]
    for st in ("queued", "analyzing", "awaiting", "answered", "converged",
               "story_created"):
        r = _set_status(ctx, pid, st)
        assert r.status_code == 200, f"{st}: {r.text}"
        assert r.json()["status"] == st
    # 终态不可再流转
    assert _set_status(ctx, pid, "queued").status_code == 400


def test_illegal_transition_rejected(ctx):
    pid = _new_proposal(ctx, title="非法迁移")["id"]
    r = _set_status(ctx, pid, "awaiting")  # draft 不能直跳 awaiting
    assert r.status_code == 400, r.text
    assert "不合法" in r.json()["detail"]
    assert _set_status(ctx, pid, "not_a_status").status_code == 422


def test_failure_and_requeue(ctx):
    pid = _new_proposal(ctx, title="失败重投")["id"]
    _set_status(ctx, pid, "queued")
    _set_status(ctx, pid, "analyzing")
    r = _set_status(ctx, pid, "failed", error="worker crashed")
    assert r.status_code == 200, r.text
    assert r.json()["error"] == "worker crashed"
    r = _set_status(ctx, pid, "queued")  # failed → queued 重投，error 清空
    assert r.status_code == 200
    assert r.json()["error"] == ""


# ---------------- 4. 轮次问答 ----------------

def test_ask_requires_analyzing(ctx):
    pid = _new_proposal(ctx, title="提问前置校验")["id"]
    r = ctx["c"].post(f"/api/proposals/{pid}/questions",
                      json={"questions": ["目标用户是谁？"]})
    assert r.status_code == 400, r.text
    assert "analyzing" in r.json()["detail"]


def test_full_qa_round_trip(ctx):
    pid = _new_proposal(ctx, title="问答闭环")["id"]
    _set_status(ctx, pid, "queued")
    _set_status(ctx, pid, "analyzing")

    r = ctx["c"].post(f"/api/proposals/{pid}/questions", json={
        "questions": ["目标用户是谁？", "周报数据从哪来？", "  ", "需要导出 PDF 吗？"],
        "summary": "第 1 轮澄清", "agent": "workbuddy-worker",
    })
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["round"]["round_no"] == 1
    assert payload["round"]["agent"] == "workbuddy-worker"
    assert len(payload["questions"]) == 3, "空白问题应被剔除"
    assert [q["seq"] for q in payload["questions"]] == [1, 2, 3]

    # 提问后自动进入 awaiting
    body = ctx["c"].get(f"/api/proposals/{pid}").json()
    assert body["status"] == "awaiting"
    assert body["current_round"] == 1

    qids = [q["id"] for q in payload["questions"]]
    for qid, ans in zip(qids[:2], ["内部研发团队", "从 AgentBoard 任务流水"]):
        r = ctx["c"].put(f"/api/proposal-questions/{qid}/answer", json={"answer": ans})
        assert r.status_code == 200, r.text
        assert r.json()["answer"] == ans
        assert r.json()["answered_at"] is not None
    assert ctx["c"].get(f"/api/proposals/{pid}").json()["status"] == "awaiting"

    # 最后一条标记「不确定」→ 整轮处理完，自动 awaiting→answered
    r = ctx["c"].put(f"/api/proposal-questions/{qids[2]}/answer", json={"unsure": True})
    assert r.status_code == 200, r.text
    assert r.json()["unsure"] is True
    assert ctx["c"].get(f"/api/proposals/{pid}").json()["status"] == "answered"

    rounds = ctx["c"].get(f"/api/proposals/{pid}/rounds").json()
    assert len(rounds) == 1
    assert rounds[0]["summary"] == "第 1 轮澄清"
    assert len(rounds[0]["questions"]) == 3
    assert rounds[0]["questions"][0]["answer"] == "内部研发团队"


def test_answer_requires_content_unless_unsure(ctx):
    pid = _new_proposal(ctx, title="作答校验")["id"]
    _set_status(ctx, pid, "queued")
    _set_status(ctx, pid, "analyzing")
    r = ctx["c"].post(f"/api/proposals/{pid}/questions",
                      json={"questions": ["一个问题？"]})
    qid = r.json()["questions"][0]["id"]
    r = ctx["c"].put(f"/api/proposal-questions/{qid}/answer", json={"answer": "   "})
    assert r.status_code == 422, r.text


def test_round_uniqueness_makes_redelivery_idempotent(ctx):
    """at-least-once 重投：同一 (proposal, round) 重复提交不产生重复轮次/问题。"""
    pid = _new_proposal(ctx, title="重投幂等")["id"]
    _set_status(ctx, pid, "queued")
    _set_status(ctx, pid, "analyzing")

    body = {"questions": ["Q1？", "Q2？"], "round": 1, "agent": "worker-a"}
    r1 = ctx["c"].post(f"/api/proposals/{pid}/questions", json=body)
    assert r1.status_code == 201, r1.text

    # 回到 analyzing 后用同一 round 重投（模拟 MQ 重复投递）
    assert _set_status(ctx, pid, "converged").status_code == 200
    assert _set_status(ctx, pid, "analyzing").status_code == 200
    r2 = ctx["c"].post(f"/api/proposals/{pid}/questions", json=body)
    assert r2.status_code == 201, r2.text
    assert r2.json()["round"]["id"] == r1.json()["round"]["id"]
    assert len(r2.json()["questions"]) == 2

    rounds = ctx["c"].get(f"/api/proposals/{pid}/rounds").json()
    assert len(rounds) == 1, "重投不应产生第二轮"
    assert len(rounds[0]["questions"]) == 2, "重投不应重复写入问题"


def test_multi_round_increments(ctx):
    pid = _new_proposal(ctx, title="多轮澄清")["id"]
    _set_status(ctx, pid, "queued")
    _set_status(ctx, pid, "analyzing")
    r = ctx["c"].post(f"/api/proposals/{pid}/questions",
                      json={"questions": ["第一轮问题？"]})
    assert r.json()["round"]["round_no"] == 1

    qid = r.json()["questions"][0]["id"]
    ctx["c"].put(f"/api/proposal-questions/{qid}/answer", json={"answer": "答案"})
    assert _set_status(ctx, pid, "analyzing").status_code == 200  # answered → 下一轮
    r = ctx["c"].post(f"/api/proposals/{pid}/questions",
                      json={"questions": ["第二轮问题？"]})
    assert r.json()["round"]["round_no"] == 2

    assert ctx["c"].get(f"/api/proposals/{pid}").json()["current_round"] == 2
    rounds = ctx["c"].get(f"/api/proposals/{pid}/rounds").json()
    assert [x["round_no"] for x in rounds] == [1, 2]


# ---------------- 5. Worker 轮询端点与定稿回填 ----------------

def test_pending_endpoint_returns_queued_only(ctx):
    p = _new_proposal(ctx, title="待认领")
    _set_status(ctx, p["id"], "queued")
    r = ctx["c"].get("/api/proposals/pending")
    assert r.status_code == 200, r.text
    assert p["id"] in [x["id"] for x in r.json()]
    assert all(x["status"] == "queued" for x in r.json())


def test_converged_spec_and_story_backfill(ctx):
    pid = _new_proposal(ctx, title="定稿回填")["id"]
    for st in ("queued", "analyzing", "converged"):
        assert _set_status(ctx, pid, st).status_code == 200
    r = ctx["c"].patch(f"/api/proposals/{pid}", json={
        "converged_spec": "## 最终需求\n- 自动整理周报",
        "story_id": ctx["story_id"],
    })
    assert r.status_code == 200, r.text
    assert r.json()["story_id"] == ctx["story_id"]
    assert "自动整理周报" in r.json()["converged_spec"]
    assert _set_status(ctx, pid, "story_created").status_code == 200


# ---------------- 6. 删除级联与 404 ----------------

def test_delete_proposal_cascades(ctx):
    pid = _new_proposal(ctx, title="删除级联")["id"]
    _set_status(ctx, pid, "queued")
    _set_status(ctx, pid, "analyzing")
    ctx["c"].post(f"/api/proposals/{pid}/questions",
                  json={"questions": ["会被级联删除的问题？"]})

    assert ctx["c"].delete(f"/api/proposals/{pid}").status_code == 200
    assert ctx["c"].get(f"/api/proposals/{pid}").status_code == 404
    assert ctx["c"].get(f"/api/proposals/{pid}/rounds").status_code == 404


def test_404_on_unknown_proposal(ctx):
    assert ctx["c"].get("/api/proposals/999999").status_code == 404
    assert ctx["c"].get("/api/proposals/999999/rounds").status_code == 404
    assert ctx["c"].delete("/api/proposals/999999").status_code == 404
