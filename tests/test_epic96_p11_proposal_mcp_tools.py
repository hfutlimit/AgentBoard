"""Epic 96 P1-1 — Proposal 澄清回路的 MCP Worker 侧工具面。

背景
----
Epic 96 P0 交付了完整的 REST 层（`/api/proposals` CRUD + 状态机 + questions +
rounds + answer + pending）与前端问答工作台，但 `mcp_server.py` 里 proposal
相关工具数量为 **0** —— 无头 Agent / Worker 根本没有入口，P1 的 Worker 消费者
无从接入。本模块为新增的 6 个工具建立回归护栏。

覆盖三层：

1. **注册层**：6 个工具确实注册进 FastMCP 工具表（不是只写了个裸函数）。
2. **闭环层**：真实 uvicorn 子进程 + 直接调工具，跑通 Worker 完整链路——
   造提案 → queued → pending 可见 → claim → get（全量重放）→ ask 回写问题
   → REST 作答 → get 二次拉取含答案 → finalize → converged 落库。
3. **鲁棒层**：幂等（重复 ask 不产生重复轮次 / 重复 claim 报错）、
   状态机护栏（非 queued 不可认领）、空 converged_spec 拒绝、404 提案。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic96_p11_proposal_mcp_tools.py -q

注意：与 test_epic96_p0_proposals.py / test_epic97 同因，必须用真实 uvicorn
子进程而非进程内 TestClient（audit_log_middleware 会 await request.body()
造成死锁）。测试完全自包含，不依赖也不触碰 18001 上的 MCP 容器。
"""
import asyncio
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

# 独立临时数据库（与其它测试隔离），子进程通过环境变量继承同一个库
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import mcp_server  # noqa: E402
from agentboard.database import init_db  # noqa: E402

init_db()

_EXPECTED_TOOLS = [
    "proposal_pending", "proposal_claim", "proposal_get",
    "proposal_ask", "proposal_finalize", "proposal_fail",
]


# ===================== 第 1 层：工具注册 =====================

def test_all_proposal_tools_registered_in_mcp():
    """6 个 proposal 工具必须真正注册进 FastMCP 工具表。

    只在模块里定义函数而漏了 `@mcp.tool()` 装饰器，Agent 侧是看不见的——
    本用例直接查 FastMCP 的工具注册表，钉死「可被 Agent 发现」这件事。
    """
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {getattr(t, "name", t) for t in tools}
    missing = [t for t in _EXPECTED_TOOLS if t not in names]
    assert not missing, (
        f"以下 proposal 工具未注册到 MCP（多半漏了 @mcp.tool() 装饰器）：{missing}\n"
        f"已注册的 proposal_* 工具：{sorted(n for n in names if n.startswith('proposal_'))}"
    )


def test_proposal_tools_have_docstrings():
    """工具描述是 Agent 选工具的唯一依据，不能为空。"""
    empty = [
        name for name in _EXPECTED_TOOLS
        if not (getattr(mcp_server, name).__doc__ or "").strip()
    ]
    assert not empty, f"以下工具缺少 docstring，Agent 无法理解其用途：{empty}"


# ===================== 真实栈 fixture =====================

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
def stack():
    """真实拉起 API，并把 mcp_server 的 HTTP 客户端指向它。"""
    port = _free_port()
    proc = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    prev_url = mcp_server.API_URL
    prev_token = os.environ.get("AGENTBOARD_MCP_TOKEN")
    try:
        _wait_ready(base)
        c = httpx.Client(base_url=base, timeout=30)
        c.post("/api/auth/register", json={"username": "e96p11", "password": "e96p11pass"})
        r = c.post("/api/auth/login", json={"username": "e96p11", "password": "e96p11pass"})
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        c.headers.update({"Authorization": f"Bearer {token}"})

        # 让 MCP 工具走这套真实栈
        mcp_server.API_URL = base
        os.environ["AGENTBOARD_MCP_TOKEN"] = token

        r = c.post("/api/projects", json={"name": "Epic96 P1-1 Proposal MCP"})
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]

        yield {"c": c, "base": base, "project_id": pid}
        c.close()
    finally:
        mcp_server.API_URL = prev_url
        if prev_token is None:
            os.environ.pop("AGENTBOARD_MCP_TOKEN", None)
        else:
            os.environ["AGENTBOARD_MCP_TOKEN"] = prev_token
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _new_queued_proposal(stack, title: str, content: str = "") -> int:
    """造一个已入队（queued）的提案，模拟用户在 Web 端提交派发。"""
    c = stack["c"]
    r = c.post("/api/proposals", json={
        "project_id": stack["project_id"], "title": title,
        "content": content or f"{title} 的原始需求正文",
    })
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    r = c.put(f"/api/proposals/{pid}/status", json={"status": "queued"})
    assert r.status_code == 200, r.text
    return pid


def _is_err(resp) -> bool:
    """工具失败时返回的是「只有 error 一个键」的 dict。

    注意不能用 ``"error" in resp``——提案实体自身带 ``error`` 字段
    （失败原因列），正常提案 dict 也含该键。
    """
    return isinstance(resp, dict) and set(resp.keys()) == {"error"}


def _no_error(label: str, resp):
    """断言工具返回里没有传输层错误痕迹（NameError / 路由未命中 / 传参错）。"""
    if _is_err(resp):
        err = str(resp["error"])
        assert "not defined" not in err, f"{label} 触发 NameError：{err}"
        assert "Not Found" not in err, f"{label} 路径未命中（多半缺 /api 前缀）：{err}"
        assert "Method Not Allowed" not in err, f"{label} 方法不匹配：{err}"
        assert "Field required" not in err, f"{label} 请求体未正确传递：{err}"
        pytest.fail(f"{label} 返回错误：{err}")
    return resp


# ===================== 第 2 层：Worker 完整闭环 =====================

def test_worker_full_clarification_loop(stack):
    """跑通无头 Worker 的完整澄清回路（本任务的核心验收）。

    pending → claim → get(重放) → ask → 用户作答 → get(含答案)
            → 第二轮 ask → 作答 → finalize → converged
    """
    c = stack["c"]
    pid = _new_queued_proposal(
        stack, "导出功能需求", "希望支持把任务列表导出成表格，方便周会汇报。",
    )

    # --- 1. Worker 轮询到待认领提案 ---
    pending = _no_error("proposal_pending", mcp_server.proposal_pending(limit=50))
    assert isinstance(pending, list), f"pending 应返回 list，实得 {pending!r}"
    assert pid in [p["id"] for p in pending], "新入队的提案未出现在 pending 列表中"

    # --- 2. 认领：queued → analyzing ---
    claimed = _no_error("proposal_claim", mcp_server.proposal_claim(pid, agent="worker-1"))
    assert claimed.get("ok") is True, f"认领失败：{claimed!r}"
    assert claimed["claimed_by"] == "worker-1"
    assert c.get(f"/api/proposals/{pid}").json()["status"] == "analyzing", \
        "认领后状态未落库为 analyzing"

    # 认领后不应再出现在 pending 中（避免被其它 Worker 重复领取）
    pending2 = mcp_server.proposal_pending(limit=50)
    assert pid not in [p["id"] for p in pending2], "已认领的提案仍出现在 pending 列表"

    # --- 3. 拉取全量重放上下文（首轮：无历史） ---
    ctx = _no_error("proposal_get", mcp_server.proposal_get(pid))
    assert ctx["proposal_id"] == pid
    assert ctx["status"] == "analyzing"
    assert "周会汇报" in ctx["content"], "重放上下文缺少原始需求正文"
    assert ctx["history"] == [], "首轮不应有历史问答"
    assert ctx["total_questions"] == 0

    # --- 4. Agent 回写第 1 轮 open questions ---
    asked = _no_error("proposal_ask", mcp_server.proposal_ask(
        pid,
        questions=["需要支持哪些导出格式？", "导出范围是当前筛选结果还是全部任务？"],
        summary="首轮澄清：格式与范围", agent="worker-1",
    ))
    assert asked["round"]["round_no"] == 1, f"首轮 round_no 应为 1：{asked['round']!r}"
    assert len(asked["questions"]) == 2
    assert asked["round"]["agent"] == "worker-1", "轮次未记录 Worker 账号名"
    assert c.get(f"/api/proposals/{pid}").json()["status"] == "awaiting", \
        "回写问题后应推进到 awaiting 等待用户作答"

    # --- 5. 用户在 Web 端逐条作答（一条真答、一条标记不确定） ---
    q1, q2 = asked["questions"]
    r = c.put(f"/api/proposal-questions/{q1['id']}/answer",
              json={"answer": "CSV 和 Excel 两种"})
    assert r.status_code == 200, r.text
    r = c.put(f"/api/proposal-questions/{q2['id']}/answer",
              json={"answer": "", "unsure": True})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/proposals/{pid}").json()["status"] == "answered", \
        "整轮处理完应自动推进到 answered"

    # --- 6. 全量重放：Agent 无状态续接，必须拿到问题+答案+unsure 标记 ---
    ctx2 = _no_error("proposal_get", mcp_server.proposal_get(pid))
    assert ctx2["total_questions"] == 2
    assert ctx2["answered_count"] == 2, f"两条均已处理，实得 {ctx2['answered_count']}"
    assert ctx2["open_questions"] == [], "已全部作答，不应还有 open questions"

    by_text = {h["question"]: h for h in ctx2["history"]}
    fmt = by_text["需要支持哪些导出格式？"]
    assert fmt["answer"] == "CSV 和 Excel 两种", "重放上下文丢失了用户答案"
    assert fmt["unsure"] is False
    scope = by_text["导出范围是当前筛选结果还是全部任务？"]
    assert scope["unsure"] is True, "重放上下文丢失了「不确定」标记"
    assert scope["answered"] is True, "标记不确定也算已处理"
    assert all(h["round"] == 1 for h in ctx2["history"]), "history 轮次号错误"

    # --- 7. 第 2 轮澄清（answered → analyzing → ask） ---
    r = c.put(f"/api/proposals/{pid}/status", json={"status": "analyzing"})
    assert r.status_code == 200, r.text
    asked2 = _no_error("proposal_ask", mcp_server.proposal_ask(
        pid, questions=["Excel 是否需要保留任务状态的颜色标记？"],
        summary="次轮澄清：样式细节", agent="worker-1",
    ))
    assert asked2["round"]["round_no"] == 2, "未自动推进到第 2 轮"

    ctx3 = mcp_server.proposal_get(pid)
    assert ctx3["total_questions"] == 3, "重放上下文应累计全部轮次问题"
    assert len(ctx3["open_questions"]) == 1, "第 2 轮问题应处于待答状态"
    assert ctx3["open_questions"][0]["round"] == 2
    assert {h["round"] for h in ctx3["history"]} == {1, 2}, "历史应跨越两轮"

    q3 = asked2["questions"][0]
    r = c.put(f"/api/proposal-questions/{q3['id']}/answer", json={"answer": "不需要，纯数据即可"})
    assert r.status_code == 200, r.text

    # --- 8. 收敛定稿 → converged（等待人工终审，不直接建 Story） ---
    spec = "## 导出功能\n- 格式：CSV / Excel\n- 范围：当前筛选结果\n- 样式：纯数据，不带颜色"
    fin = _no_error("proposal_finalize", mcp_server.proposal_finalize(pid, spec))
    assert fin["status"] == "converged", f"收敛后状态应为 converged：{fin!r}"

    final = c.get(f"/api/proposals/{pid}").json()
    assert final["status"] == "converged"
    assert final["converged_spec"] == spec, "收敛规格未落库"
    assert final["story_id"] is None, "P1 不应自动创建 Story（人工终审保留在 P3）"


# ===================== 第 3 层：幂等与错误路径 =====================

def test_claim_is_exclusive(stack):
    """重复认领必须报错，防止多个 Worker 重复分析同一提案。"""
    pid = _new_queued_proposal(stack, "并发认领校验")

    first = mcp_server.proposal_claim(pid, agent="worker-a")
    assert first.get("ok") is True, f"首次认领应成功：{first!r}"

    second = mcp_server.proposal_claim(pid, agent="worker-b")
    assert _is_err(second), f"重复认领应返回 error，实得 {second!r}"
    assert "analyzing" in str(second["error"]), "错误信息应说明当前状态"

    assert stack["c"].get(f"/api/proposals/{pid}").json()["status"] == "analyzing"


def test_ask_same_round_is_idempotent(stack):
    """同一 (proposal, round) 重复 ask 幂等 —— 兜底 at-least-once 重投。"""
    pid = _new_queued_proposal(stack, "重投幂等校验")
    mcp_server.proposal_claim(pid, agent="worker-1")

    first = mcp_server.proposal_ask(
        pid, questions=["问题甲", "问题乙"], round=1, agent="worker-1")
    _no_error("proposal_ask", first)
    rid = first["round"]["id"]

    # 模拟消息重投：同轮次再提交一次（甚至换了问题内容）
    again = mcp_server.proposal_ask(
        pid, questions=["问题甲", "问题乙", "问题丙"], round=1, agent="worker-1")
    _no_error("proposal_ask 重投", again)

    assert again["round"]["id"] == rid, "重投应复用既有轮次，而非新建"
    assert len(again["questions"]) == 2, \
        f"重投不得追加问题（幂等），实得 {len(again['questions'])} 条"

    ctx = mcp_server.proposal_get(pid)
    assert ctx["total_questions"] == 2, "重投后问题总数不应膨胀"
    assert len([r for r in ctx["rounds"] if r["round_no"] == 1]) == 1, "不应产生重复轮次"
    assert ctx["status"] == "awaiting", "重投不应改动提案状态"

    # 放宽「重投可复用」不得连带放宽「新开轮次」：awaiting 下开新轮仍须被拒
    fresh = mcp_server.proposal_ask(pid, questions=["问题丁"], round=2, agent="worker-1")
    assert _is_err(fresh), f"awaiting 状态下开新轮次应被拒绝：{fresh!r}"
    assert "analyzing" in str(fresh["error"])
    assert mcp_server.proposal_get(pid)["total_questions"] == 2, "被拒的新轮次不得留下数据"

    # 省略 round 参数时无法判定是否重投，同样必须按「新开一轮」拒绝
    implicit = mcp_server.proposal_ask(pid, questions=["问题戊"], agent="worker-1")
    assert _is_err(implicit), f"awaiting 下省略 round 应被拒绝：{implicit!r}"


def test_claim_rejects_non_queued_proposal(stack):
    """draft 状态（尚未派发）不可被 Worker 认领。"""
    c = stack["c"]
    r = c.post("/api/proposals", json={
        "project_id": stack["project_id"], "title": "草稿态不可认领", "content": "x",
    })
    pid = r.json()["id"]
    assert r.json()["status"] == "draft"

    res = mcp_server.proposal_claim(pid, agent="worker-1")
    assert _is_err(res), f"draft 提案不应可认领：{res!r}"
    assert "draft" in str(res["error"])
    assert c.get(f"/api/proposals/{pid}").json()["status"] == "draft", "状态不应被改动"


def test_finalize_rejects_empty_spec(stack):
    """收敛定稿必须给出规格，空白直接拒绝且不改状态。"""
    pid = _new_queued_proposal(stack, "空规格校验")
    mcp_server.proposal_claim(pid, agent="worker-1")

    res = mcp_server.proposal_finalize(pid, "   ")
    assert _is_err(res), f"空 converged_spec 应被拒绝：{res!r}"
    assert stack["c"].get(f"/api/proposals/{pid}").json()["status"] == "analyzing", \
        "校验失败不应改动状态"


def test_proposal_fail_records_error(stack):
    """失败标记必须落 error 原因，供后续回退 queued 重投。"""
    c = stack["c"]
    pid = _new_queued_proposal(stack, "失败回退校验")
    mcp_server.proposal_claim(pid, agent="worker-1")

    res = _no_error("proposal_fail", mcp_server.proposal_fail(pid, "LLM 调用超时"))
    assert res["status"] == "failed"

    row = c.get(f"/api/proposals/{pid}").json()
    assert row["status"] == "failed"
    assert row["error"] == "LLM 调用超时", "失败原因未落库"

    # 回归护栏：提案实体自身带 error 字段，工具不得把它误判为传输层错误
    # （初版用 `"error" in resp` 判错，导致 failed 提案的一切读取都被当成失败）
    ctx = mcp_server.proposal_get(pid)
    assert not _is_err(ctx), f"failed 提案的重放上下文被误判为错误：{ctx!r}"
    assert ctx["proposal_id"] == pid
    assert ctx["status"] == "failed"
    assert ctx["error"] == "LLM 调用超时", "重放上下文应带上失败原因供 Agent 参考"

    # failed → queued 可重投，重投后 Worker 能再次领取
    r = c.put(f"/api/proposals/{pid}/status", json={"status": "queued"})
    assert r.status_code == 200, r.text
    assert pid in [p["id"] for p in mcp_server.proposal_pending(limit=50)], \
        "重投后的提案应重新出现在 pending 列表"


def test_tools_handle_missing_proposal_gracefully(stack):
    """不存在的提案应返回结构化 error，而不是抛异常。"""
    ghost = 99999999
    for label, resp in [
        ("proposal_get", mcp_server.proposal_get(ghost)),
        ("proposal_claim", mcp_server.proposal_claim(ghost)),
        ("proposal_ask", mcp_server.proposal_ask(ghost, questions=["x"])),
        ("proposal_fail", mcp_server.proposal_fail(ghost, "boom")),
    ]:
        assert isinstance(resp, dict) and "error" in resp, \
            f"{label} 对不存在的提案应返回 error，实得 {resp!r}"
        assert "not defined" not in str(resp["error"]), f"{label} 触发 NameError"
