"""Epic 96 P1-2 — Proposal 澄清 Worker 消费者回归护栏。

背景
----
P1-1（Task 931）交付了 6 个 ``proposal_*`` MCP 工具，但没有常驻消费者：提案
提交后停在 ``queued``，必须靠人手工调工具推进。``agentboard/worker.py`` 补上
执行侧，本模块为其建立护栏。

覆盖四层：

1. **纯函数层**：决策解析（噪声日志中抽 JSON / 非法 action / 空问题拒绝）、
   时间解析。不需要起服务，跑得快，坏了定位准。
2. **闭环层**：真实 uvicorn 子进程 + REST，跑通
   queued → claim → ask → 用户作答 → answered → 下一轮 → finalize → converged。
3. **鲁棒层**：并发认领竞争、租约超时崩溃恢复、Agent 异常/超时/输出非 JSON、
   轮次上限护栏。
4. **真实子进程层**：用一个 fake CLI 脚本自证 ``SubprocessAgentInvoker``
   的 stdin/stdout 协议真的可用（不只是 stub 好使）。

运行::

    PYTHONPATH=. python -m pytest tests/test_epic96_p12_proposal_worker.py -q

与 test_epic96_p0_proposals.py 同因，必须用真实 uvicorn 子进程而非进程内
TestClient（audit_log_middleware 会 await request.body() 造成死锁）。
测试完全自包含，不依赖也不触碰 18001 上的 MCP 容器。
"""
import os
import json
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import worker as W  # noqa: E402
from agentboard.database import init_db  # noqa: E402

init_db()


# ===================== 第 1 层：纯函数 =====================

def test_extract_decision_from_noisy_stdout():
    """真实 CLI 会先刷一堆日志再给结论，抽取必须只认最后一个带 action 的对象。"""
    stdout = textwrap.dedent("""
        [info] loading workspace...
        {"trace": "some intermediate object without action"}
        我先思考一下：这个需求缺少 {重要} 的约束信息。
        ```json
        {"action": "ask", "questions": ["目标用户是谁？", "并发量级？"], "summary": "范围澄清"}
        ```
        [info] done in 3.2s
    """)
    data = W.extract_decision_json(stdout)
    d = W.AgentDecision.from_dict(data)
    assert d.action == "ask"
    assert d.questions == ["目标用户是谁？", "并发量级？"]
    assert d.summary == "范围澄清"


def test_extract_decision_handles_braces_inside_strings():
    """字符串里的花括号不能把括号配对扫描带偏。"""
    stdout = '{"action":"finalize","converged_spec":"用 {placeholder} 模板渲染 }} 转义"}'
    d = W.AgentDecision.from_dict(W.extract_decision_json(stdout))
    assert d.action == "finalize"
    assert "placeholder" in d.converged_spec


def test_extract_decision_rejects_garbage():
    with pytest.raises(W.AgentOutputError):
        W.extract_decision_json("完全没有 JSON 的一段输出")
    with pytest.raises(W.AgentOutputError):
        W.extract_decision_json("")


@pytest.mark.parametrize("payload,reason", [
    ({"action": "unknown"}, "非法 action"),
    ({"action": "ask", "questions": []}, "ask 无问题"),
    ({"action": "ask", "questions": ["   "]}, "ask 问题全空白"),
    ({"action": "finalize", "converged_spec": ""}, "finalize 无规格"),
    (["not", "a", "dict"], "非对象"),
])
def test_decision_validation_guards(payload, reason):
    with pytest.raises(W.AgentOutputError):
        W.AgentDecision.from_dict(payload)


def test_parse_dt_treats_naive_as_utc():
    """服务端用 utc_now 落库且序列化后无时区，误当本地时间会让租约判定完全错乱。"""
    dt = W._parse_dt("2026-07-31T08:00:00")
    assert dt is not None and dt.tzinfo is timezone.utc
    assert W._parse_dt("2026-07-31T08:00:00Z") == dt
    assert W._parse_dt(None) is None
    assert W._parse_dt("not-a-date") is None


# ===================== 真实栈 fixture =====================

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
    port = _free_port()
    env = os.environ.copy()
    # 隔离 DB：避免继承同会话中其它模块（如 p0）写入进程环境的 AGENTBOARD_DB_URL，
    # 否则本模块的 Worker 会顺手处理到别的模块的遗留提案，导致断言失真。
    env["AGENTBOARD_DB_URL"] = f"sqlite:///{tempfile.mktemp(suffix='.db')}"
    env["AGENTBOARD_MCP_BACKEND"] = "db"
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        c = httpx.Client(base_url=base, timeout=30)
        c.post("/api/auth/register", json={"username": "e96p12", "password": "e96p12pass"})
        r = c.post("/api/auth/login", json={"username": "e96p12", "password": "e96p12pass"})
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        r = c.post("/api/projects", json={"name": "Epic96 P1-2 Worker"})
        assert r.status_code in (200, 201), r.text
        yield {"c": c, "base": base, "project_id": r.json()["id"], "token": token}
        c.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _make_worker(stack, invoker, **overrides) -> W.ProposalWorker:
    cfg = W.WorkerConfig(
        api_url=stack["base"], token=stack["token"], agent="pytest-worker",
        poll_interval=0.01, **overrides,
    )
    return W.ProposalWorker(cfg, invoker=W.CallableAgentInvoker(invoker))


def _new_queued(stack, title: str) -> int:
    c = stack["c"]
    r = c.post("/api/proposals", json={
        "project_id": stack["project_id"], "title": title,
        "content": f"{title} 的原始需求正文",
    })
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    assert c.put(f"/api/proposals/{pid}/status", json={"status": "queued"}).status_code == 200
    return pid


def _status(stack, pid: int) -> str:
    return stack["c"].get(f"/api/proposals/{pid}").json()["status"]


def _answer_all_open(stack, pid: int, answer: str = "已确认") -> int:
    """模拟用户在 Web 工作台把本轮所有未答问题答完。"""
    rounds = stack["c"].get(f"/api/proposals/{pid}/rounds").json()
    n = 0
    for r in rounds:
        for q in r.get("questions") or []:
            if not q.get("answered_at"):
                resp = stack["c"].put(f"/api/proposal-questions/{q['id']}/answer",
                                      json={"answer": answer, "unsure": False})
                assert resp.status_code == 200, resp.text
                n += 1
    return n


# ===================== 第 2 层：完整闭环 =====================

def test_full_clarification_loop_queued_to_converged(stack):
    """一次跑通：queued → 认领 → 提问 → 用户作答 → answered 续轮 → 收敛。

    这是 Story 155 P1 的核心验收路径：Worker 全程无人工干预地推动状态机。
    """
    pid = _new_queued(stack, "P1-2 全闭环：订单导出功能")
    calls: list[dict] = []

    def agent(ctx):
        calls.append(ctx)
        if ctx["current_round"] == 0:
            return {"action": "ask",
                    "questions": ["导出格式需要哪些？", "单次导出上限多少行？"],
                    "summary": "第一轮：范围与规模"}
        # 第二轮：历史里必须能看到用户答案，否则全量重放没起作用
        assert ctx["history"], "第二轮上下文缺少历史问答，全量重放失效"
        assert all(h["answered"] for h in ctx["history"]), "历史问答未带上用户答案"
        return {"action": "finalize",
                "converged_spec": "## 订单导出\n- 支持 CSV/XLSX\n- 单次上限 10 万行"}

    with _make_worker(stack, agent) as w:
        # 第一轮
        summary = w.poll_once()
        assert {"proposal_id": pid, "outcome": "asked"} in summary["handled"]
        assert _status(stack, pid) == "awaiting"
        rounds = stack["c"].get(f"/api/proposals/{pid}/rounds").json()
        assert len(rounds) == 1 and len(rounds[0]["questions"]) == 2

        # 用户作答 → 自动推进 answered
        assert _answer_all_open(stack, pid) == 2
        assert _status(stack, pid) == "answered"

        # 第二轮：Worker 从 answered 里发现工作项并收敛
        summary = w.poll_once()
        assert {"proposal_id": pid, "outcome": "converged"} in summary["handled"]

    assert _status(stack, pid) == "converged"
    body = stack["c"].get(f"/api/proposals/{pid}").json()
    assert "CSV/XLSX" in body["converged_spec"]
    assert len(calls) == 2, "应恰好调用 Agent 两次（每轮一次）"


def test_replay_context_carries_unsure_flag(stack):
    """用户标记「不确定」是重要信号，必须原样传给下一轮 Agent。"""
    pid = _new_queued(stack, "P1-2 不确定标记透传")
    seen: dict = {}

    def agent(ctx):
        if ctx["current_round"] == 0:
            return {"action": "ask", "questions": ["需要支持离线吗？"]}
        seen["history"] = ctx["history"]
        return {"action": "finalize", "converged_spec": "最终规格"}

    with _make_worker(stack, agent) as w:
        w.poll_once()
        q = stack["c"].get(f"/api/proposals/{pid}/rounds").json()[0]["questions"][0]
        stack["c"].put(f"/api/proposal-questions/{q['id']}/answer",
                       json={"answer": "不太清楚", "unsure": True})
        w.poll_once()

    assert seen["history"][0]["unsure"] is True
    assert seen["history"][0]["answer"] == "不太清楚"


# ===================== 第 3 层：鲁棒性 =====================

def test_concurrent_claim_only_one_wins(stack):
    """两个 Worker 抢同一个提案：一个成功，另一个静默跳过（不抛错、不重复提问）。"""
    pid = _new_queued(stack, "P1-2 并发认领竞争")
    proposal = stack["c"].get(f"/api/proposals/{pid}").json()

    def agent(ctx):
        return {"action": "ask", "questions": ["只应出现一次的问题"]}

    with _make_worker(stack, agent) as w1, _make_worker(stack, agent) as w2:
        assert w1.claim(proposal) is True
        assert w2.claim(proposal) is False, "第二个 Worker 不应认领成功"
    assert _status(stack, pid) == "analyzing"


def test_stale_analyzing_is_reclaimed_and_reprocessed(stack):
    """Worker 崩在 analyzing 中途 → 租约过期后被回收重投，提案不会永久卡死。"""
    pid = _new_queued(stack, "P1-2 崩溃恢复租约")

    def crashing_agent(ctx):
        raise KeyboardInterrupt("模拟 Worker 进程被 kill")

    # 直接认领后不处理，模拟持有 Worker 猝死
    proposal = stack["c"].get(f"/api/proposals/{pid}").json()
    with _make_worker(stack, crashing_agent) as w:
        assert w.claim(proposal) is True
    assert _status(stack, pid) == "analyzing"

    # 租约设成 0 秒 → 立即视为过期
    def good_agent(ctx):
        return {"action": "ask", "questions": ["接手后的第一个问题"]}

    with _make_worker(stack, good_agent, lease_seconds=0) as w:
        summary = w.poll_once()
        assert pid in summary["reclaimed"], "超租约的 analyzing 提案应被回退 queued"
        assert {"proposal_id": pid, "outcome": "asked"} in summary["handled"]
    assert _status(stack, pid) == "awaiting"


def test_fresh_analyzing_is_not_reclaimed(stack):
    """租约未到期的 analyzing 不能被抢走，否则会出现两个 Agent 同时分析。"""
    pid = _new_queued(stack, "P1-2 租约未到期不回收")
    proposal = stack["c"].get(f"/api/proposals/{pid}").json()
    with _make_worker(stack, lambda ctx: {"action": "fail", "error": "x"}) as w:
        assert w.claim(proposal) is True
    with _make_worker(stack, lambda ctx: {"action": "fail", "error": "x"},
                      lease_seconds=3600) as w:
        assert pid not in w.reclaim_stale()
    assert _status(stack, pid) == "analyzing"


def test_agent_exception_marks_failed_not_stuck(stack):
    """Agent 抛异常时提案必须落 failed 带可读原因，绝不静默卡在 analyzing。"""
    pid = _new_queued(stack, "P1-2 Agent 异常兜底")

    def boom(ctx):
        raise RuntimeError("模型服务 503")

    with _make_worker(stack, boom) as w:
        assert w.poll_once()["counts"].get("failed") == 1
    body = stack["c"].get(f"/api/proposals/{pid}").json()
    assert body["status"] == "failed"
    assert "503" in body["error"]


def test_agent_invalid_output_marks_failed(stack):
    """Agent 输出非法（如 action 拼错）同样要落 failed，而不是静默丢单。"""
    pid = _new_queued(stack, "P1-2 Agent 输出非法")
    with _make_worker(stack, lambda ctx: {"action": "asks", "questions": ["x"]}) as w:
        w.poll_once()
    body = stack["c"].get(f"/api/proposals/{pid}").json()
    assert body["status"] == "failed"
    assert "action" in body["error"]


def test_agent_explicit_fail_records_reason(stack):
    pid = _new_queued(stack, "P1-2 Agent 主动判定失败")
    with _make_worker(stack, lambda ctx: {"action": "fail", "error": "需求描述为空，无法澄清"}) as w:
        w.poll_once()
    body = stack["c"].get(f"/api/proposals/{pid}").json()
    assert body["status"] == "failed"
    assert body["error"] == "需求描述为空，无法澄清"


def test_max_rounds_guard_stops_endless_questioning(stack):
    """Agent 死循环提问时，轮次上限必须刹车并转人工，避免无限骚扰用户。"""
    pid = _new_queued(stack, "P1-2 轮次上限护栏")

    def always_ask(ctx):
        return {"action": "ask", "questions": [f"第 {ctx['current_round'] + 1} 轮追问"]}

    # max_rounds=2：第 1、2 轮正常提问，第 3 轮触发护栏
    with _make_worker(stack, always_ask, max_rounds=2) as w:
        for _ in range(2):
            w.poll_once()
            _answer_all_open(stack, pid)
        assert _status(stack, pid) == "answered"
        w.poll_once()

    body = stack["c"].get(f"/api/proposals/{pid}").json()
    assert body["status"] == "failed"
    assert "最大澄清轮次" in body["error"]


def test_ask_is_idempotent_on_repeated_round(stack):
    """at-least-once 重投兜底：显式指定同一 round 重复提交不产生重复轮次。"""
    pid = _new_queued(stack, "P1-2 同轮重投幂等")
    with _make_worker(stack, lambda ctx: {"action": "ask", "questions": ["Q1"], "round": 1}) as w:
        w.poll_once()
        # 人为回退到 analyzing 再投一次，模拟消息重投
        stack["c"].put(f"/api/proposals/{pid}/status", json={"status": "converged"})
        stack["c"].put(f"/api/proposals/{pid}/status", json={"status": "analyzing"})
        w._apply_ask(pid, W.AgentDecision(action="ask", questions=["Q1"], round=1))
    rounds = stack["c"].get(f"/api/proposals/{pid}/rounds").json()
    assert len(rounds) == 1, f"同一 round 重复提交产生了 {len(rounds)} 个轮次"


def test_fetch_work_covers_both_queued_and_answered(stack):
    """两类工作项都要被发现，漏掉 answered 会让澄清停在第二轮之前。"""
    q_pid = _new_queued(stack, "P1-2 发现 queued")
    a_pid = _new_queued(stack, "P1-2 发现 answered")
    with _make_worker(stack, lambda ctx: {"action": "ask", "questions": ["Q"]}) as w:
        proposal = stack["c"].get(f"/api/proposals/{a_pid}").json()
        w.claim(proposal)
        w._apply_ask(a_pid, W.AgentDecision(action="ask", questions=["Q"]))
        _answer_all_open(stack, a_pid)
        assert _status(stack, a_pid) == "answered"

        ids = {p["id"] for p in w.fetch_work()}
    assert q_pid in ids, "queued 提案未被发现"
    assert a_pid in ids, "answered 提案未被发现（下一轮澄清将永远不会触发）"


def test_run_forever_stops_after_max_cycles(stack):
    """常驻循环要能被 max_cycles / stop 事件收住，否则测试与优雅退出都没法做。"""
    with _make_worker(stack, lambda ctx: {"action": "fail", "error": "noop"}) as w:
        assert w.run_forever(max_cycles=2) == 2


# ===================== 第 4 层：真实子进程调用 =====================

_FAKE_CLI = textwrap.dedent('''
    """假的无头 Agent CLI：从 stdin 读 prompt，按约定往 stdout 打 JSON 决策。"""
    import sys
    prompt = sys.stdin.read()
    print("[fake-agent] 收到 prompt %d 字符" % len(prompt))
    if "子进程提出的问题" in prompt:
        print('{"action":"finalize","converged_spec":"来自子进程的最终规格","inspected_files":["src/backend-fastapi/agentboard/agent_runtime/worker.py"]}')
    else:
        print('{"action":"ask","questions":["子进程提出的问题"],"summary":"round1","inspected_files":["src/backend-fastapi/agentboard/agent_runtime/worker.py"]}')
    print("[fake-agent] done")
''')


@pytest.fixture(scope="module")
def fake_cli(tmp_path_factory):
    p = tmp_path_factory.mktemp("fakeagent") / "fake_agent.py"
    p.write_text(_FAKE_CLI, encoding="utf-8")
    return str(p)


def test_subprocess_invoker_real_process_roundtrip(fake_cli):
    """自证 stdin/stdout 协议：真起一个子进程，不是只测 stub。"""
    inv = W.SubprocessAgentInvoker(f'"{sys.executable}" "{fake_cli}"', timeout=60)
    d = inv.invoke({"proposal_id": 1, "title": "T", "content": "C",
                    "current_round": 0, "history": []})
    assert d.action == "ask"
    assert d.questions == ["子进程提出的问题"]


def test_subprocess_invoker_drives_real_loop(stack, fake_cli, tmp_path):
    """把真实子进程 Invoker 挂到 Worker 上，跑完整两轮闭环。"""
    pid = _new_queued(stack, "P1-2 子进程驱动闭环")
    cfg = W.WorkerConfig(api_url=stack["base"], token=stack["token"],
                         agent="subprocess-worker", poll_interval=0.01,
                         agent_cmd=f'"{sys.executable}" "{fake_cli}"', agent_timeout=60)
    mapping = tmp_path / "project-mappings.json"
    mapping.write_text(json.dumps({
        "projects": {str(stack["project_id"]): {"local_dir": _ROOT}},
    }), encoding="utf-8")
    old_mapping = os.environ.get("AGENTBOARD_LOCAL_MAPPINGS")
    os.environ["AGENTBOARD_LOCAL_MAPPINGS"] = str(mapping)
    try:
        with W.ProposalWorker(cfg) as w:
            assert isinstance(w.invoker, W.ComplianceEnforcingInvoker), "真实 CLI 未经强制合规包装"
            assert isinstance(w.invoker.delegate, W.SubprocessAgentInvoker), "包装内部未走真实子进程适配器"
            w.poll_once()
            assert _status(stack, pid) == "awaiting"
            _answer_all_open(stack, pid)
            w.poll_once()
    finally:
        if old_mapping is None:
            os.environ.pop("AGENTBOARD_LOCAL_MAPPINGS", None)
        else:
            os.environ["AGENTBOARD_LOCAL_MAPPINGS"] = old_mapping
    body = stack["c"].get(f"/api/proposals/{pid}").json()
    assert body["status"] == "converged"
    assert body["converged_spec"] == "来自子进程的最终规格"


def test_subprocess_invoker_timeout_is_reported(tmp_path):
    slow = tmp_path / "slow_agent.py"
    slow.write_text("import time,sys\nsys.stdin.read()\ntime.sleep(30)\n", encoding="utf-8")
    inv = W.SubprocessAgentInvoker(f'"{sys.executable}" "{slow}"', timeout=1)
    with pytest.raises(W.AgentInvocationError) as e:
        inv.invoke({"proposal_id": 1, "title": "T", "content": "C",
                    "current_round": 0, "history": []})
    assert "超时" in str(e.value)


def test_subprocess_invoker_nonzero_exit_is_reported(tmp_path):
    bad = tmp_path / "bad_agent.py"
    bad.write_text("import sys\nsys.stdin.read()\nprint('boom', file=sys.stderr)\n"
                   "sys.exit(3)\n", encoding="utf-8")
    inv = W.SubprocessAgentInvoker(f'"{sys.executable}" "{bad}"', timeout=60)
    with pytest.raises(W.AgentInvocationError) as e:
        inv.invoke({"proposal_id": 1, "title": "T", "content": "C",
                    "current_round": 0, "history": []})
    assert "退出码 3" in str(e.value)


def test_missing_agent_cmd_fails_fast():
    """没配命令模板又没传 invoker，必须在构造期就报错，不能跑起来后静默空转。"""
    with pytest.raises(ValueError):
        W.ProposalWorker(W.WorkerConfig(api_url="http://127.0.0.1:1", agent_cmd=""))


def test_prompt_contains_protocol_and_history():
    """提示词必须自带决策协议与全量历史，否则 Agent 无从续接。"""
    prompt = W.build_prompt({
        "proposal_id": 7, "title": "标题", "content": "正文", "current_round": 1,
        "history": [{"round": 1, "question": "Q1", "answer": "A1", "unsure": True,
                     "answered": True}],
    })
    assert '"action":"ask"' in prompt and '"action":"finalize"' in prompt
    assert "Q1" in prompt and "A1" in prompt and "不确定" in prompt


# ===================== Epic 96 P2-0：服务端 CAS 原子认领 + 显式租约 =====================
#
# 这批用例专门守住「消灭 Worker TOCTOU 竞态」与「租约挂靠 claimed_at 而非 updated_at」
# 两个真实缺陷。旧实现用「先 GET 复核状态再 PUT /status」认领，而状态机对同状态迁移
# （analyzing→analyzing）是幂等 no-op 返回 200，根本无法仲裁并发 —— 换回旧写法这些
# 用例会真失败，因此它们是实现正确性的硬证明。


def _new_draft(stack, title: str) -> int:
    """只创建、不推进，保持 draft，用于验证非 queued/answered 不可认领。"""
    c = stack["c"]
    r = c.post("/api/proposals", json={
        "project_id": stack["project_id"], "title": title,
        "content": f"{title} 的原始需求正文",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _to_answered(stack, title: str) -> int:
    """走一轮 ask→用户作答，把提案推进到 answered（下一轮澄清的起点）。"""
    pid = _new_queued(stack, title)
    with _make_worker(stack, lambda ctx: {"action": "ask", "questions": ["目标用户是谁？"]}) as w:
        w.poll_once()  # claim + ask → awaiting
    assert _status(stack, pid) == "awaiting"
    _answer_all_open(stack, pid)  # → answered
    assert _status(stack, pid) == "answered"
    return pid


def _claim(stack, pid, agent: str = "endpoint-tester"):
    """直接打 CAS 原子认领端点，返回 (status_code, json)。"""
    return stack["c"].post(f"/api/proposals/{pid}/claim", json={"agent": agent}), None


def test_claim_endpoint_returns_200_with_lease_fields(stack):
    """queued 提案认领成功：200 + 带上 claimed_by / claimed_at 租约。"""
    pid = _new_queued(stack, "P2-0 认领端点 200")
    c = stack["c"]
    r = c.post(f"/api/proposals/{pid}/claim", json={"agent": "worker-A"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "analyzing"
    assert body["claimed_by"] == "worker-A"
    assert body["claimed_at"], "认领后必须盖上 claimed_at 租约时间戳"
    # 二次读取状态机已流转
    assert _status(stack, pid) == "analyzing"


def test_claim_endpoint_409_when_already_analyzing(stack):
    """已被认领（analyzing）再认领必须 409，绝不静默成功（否则双 Agent 并行分析）。"""
    pid = _new_queued(stack, "P2-0 重复认领 409")
    c = stack["c"]
    assert c.post(f"/api/proposals/{pid}/claim", json={"agent": "w1"}).status_code == 200
    r2 = c.post(f"/api/proposals/{pid}/claim", json={"agent": "w2"})
    assert r2.status_code == 409, r2.text
    assert "analyzing" in r2.json()["detail"]


def test_claim_endpoint_404_for_unknown_proposal(stack):
    """认领不存在的提案必须 404，而不是把竞争失败和不存在混为一谈。"""
    r = stack["c"].post("/api/proposals/999999/claim", json={"agent": "x"})
    assert r.status_code == 404, r.text


def test_claim_endpoint_409_for_unclaimable_status(stack):
    """仅 queued/answered 可认领；draft 等其它状态必须 409（与非法迁移 400 区分）。"""
    pid = _new_draft(stack, "P2-0 未开始(pending)不可认领")
    r = stack["c"].post(f"/api/proposals/{pid}/claim", json={"agent": "x"})
    assert r.status_code == 409, r.text
    assert "pending" in r.json()["detail"]


def test_claim_endpoint_answered_to_analyzing(stack):
    """用户作答后的 answered 提案应可被 Worker 接手进入下一轮澄清（MCP proposal_claim 语义）。"""
    pid = _to_answered(stack, "P2-0 answered 可再认领")
    r = stack["c"].post(f"/api/proposals/{pid}/claim", json={"agent": "worker-B"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "analyzing"
    assert body["claimed_by"] == "worker-B"
    assert _status(stack, pid) == "analyzing"


def test_concurrent_claim_exactly_one_winner_per_proposal(stack):
    """≥8 个提案各被 12 条线程同时抢：每个提案恰好 1 个 200、其余全部 409。

    这是原子性的最强证明 —— 若退回「GET 复核 + PUT」的非原子写法，多线程会偶发
    多个 200（都在 analyzing 上拿到 no-op 200）。单条条件 UPDATE 由 SQLite 写锁串行化，
    后到者必然 rowcount=0 → 409。
    """
    import concurrent.futures as cf

    N_PROP = 8
    BURST = 12
    pids = [_new_queued(stack, f"P2-0 并发认领-{i}") for i in range(N_PROP)]
    wins = {p: 0 for p in pids}
    conflicts = {p: 0 for p in pids}

    def try_claim(pid):
        cl = httpx.Client(base_url=stack["base"], timeout=30)
        cl.headers.update({"Authorization": f"Bearer {stack['token']}"})
        code = cl.post(f"/api/proposals/{pid}/claim", json={"agent": "racer"}).status_code
        cl.close()
        return pid, code

    with cf.ThreadPoolExecutor(max_workers=64) as ex:
        futs = [ex.submit(try_claim, pid) for pid in pids for _ in range(BURST)]
        for f in cf.as_completed(futs):
            pid, code = f.result()
            assert code in (200, 409), f"非预期状态码 {code}"
            (wins if code == 200 else conflicts)[pid] += 1

    for pid in pids:
        assert wins[pid] == 1, f"提案 {pid} 赢家数={wins[pid]}（必须恰好 1）"
        assert conflicts[pid] == BURST - 1, f"提案 {pid} 冲突数={conflicts[pid]}"
    # 总计：8 个赢家 + 8*11 个冲突
    assert sum(wins.values()) == N_PROP
    assert sum(conflicts.values()) == N_PROP * (BURST - 1)


def test_claimed_at_not_refreshed_by_unrelated_patch(stack):
    """认领后 claimed_at 必须不被无关写入刷新，这是租约隔离的核心。

    `updated_at` 带 onupdate，PATCH 正文会刷新它；但 claimed_at 是显式租约字段，
    只应由认领动作写入。若二者混用，崩溃 Worker 的租约会被旁人 PATCH 无限续期。
    """
    pid = _new_queued(stack, "P2-0 claimed_at 不被 PATCH 刷新")
    c = stack["c"]
    claimed = c.post(f"/api/proposals/{pid}/claim", json={"agent": "lease-owner"}).json()
    t0_claimed = claimed["claimed_at"]
    t0_updated = claimed["updated_at"]
    assert t0_claimed and t0_updated

    # 模拟无关写入：worker 写入 converged_spec（刷新 updated_at，但与持有者无关；
    # 用户编辑 content 会回退 pending 清租约，属 2026-08-08 有意行为，不作租约对照组）
    r = c.patch(f"/api/proposals/{pid}", json={"converged_spec": "# 规格"})
    assert r.status_code == 200, r.text

    after = c.get(f"/api/proposals/{pid}").json()
    assert after["claimed_at"] == t0_claimed, "claimed_at 被无关 PATCH 改写了 —— 租约隔离失效"
    assert after["updated_at"] != t0_updated, "updated_at 应当被 PATCH 刷新（对照组）"
    assert after["updated_at"] > t0_updated


def test_reclaim_stale_uses_claimed_at_not_updated_at(stack):
    """决定性用例：崩溃 Worker 的租约靠 claimed_at 判定，不因他人 PATCH 续期而失活。

    构造：认领 → sleep 让 claimed_at 变旧 → 他人 PATCH 刷新 updated_at（变新）
    → 以短租约回收。若实现错误地用 updated_at，会被判为「新鲜」永不回收 → 永久卡死；
    正确实现依据 claimed_at，应当回收。
    """
    import time

    pid = _new_queued(stack, "P2-0 回收依据 claimed_at")
    c = stack["c"]
    claimed = c.post(f"/api/proposals/{pid}/claim", json={"agent": "crashed"}).json()
    assert claimed["status"] == "analyzing"

    time.sleep(1.1)  # 让 claimed_at 明显早于「现在」
    r = c.patch(f"/api/proposals/{pid}", json={"converged_spec": "# 续期陷阱"})
    assert r.status_code == 200

    # lease_seconds=1：cutoff = now - 1s。
    #   claimed_at（≈1.1s 前）< cutoff → 陈旧，应回收
    #   updated_at（刚刚 PATCH）≥ cutoff → 若用 updated_at 则判为新鲜，不回收
    resp = c.post("/api/proposals/reclaim-stale", json={"lease_seconds": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) >= {"reclaimed", "count", "lease_seconds"}
    assert body["lease_seconds"] == 1
    assert pid in body["reclaimed"], "依据 claimed_at 应回收，但实现漏回收（可能误用了 updated_at）"
    assert _status(stack, pid) == "queued", "回收后必须回退 queued 重投"


def test_reclaim_stale_endpoint_contract_and_fresh_untouched(stack):
    """回收端点契约：返回标准字段；租约未到期的 analyzing 不被抢走（避免双 Agent）。"""
    pid = _new_queued(stack, "P2-0 未到期不回收")
    c = stack["c"]
    assert c.post(f"/api/proposals/{pid}/claim", json={"agent": "live"}).status_code == 200

    # lease_seconds 很大 → cutoff 远在过去 → 刚认领的不算陈旧
    resp = c.post("/api/proposals/reclaim-stale", json={"lease_seconds": 3600})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0 and body["reclaimed"] == []
    assert _status(stack, pid) == "analyzing", "租约未到期却被回收，会出现两个 Agent 并行"


def test_reclaim_stale_only_touches_analyzing(stack):
    """回收只回退 analyzing 行；answered / converged 等其它状态必须纹丝不动。"""
    c = stack["c"]
    answered_pid = _to_answered(stack, "P2-0 回收不碰 answered")
    # 构造一个 converged 行
    conv_pid = _new_queued(stack, "P2-0 回收不碰 converged")
    with _make_worker(stack, lambda ctx: {"action": "finalize",
                                          "converged_spec": "收敛规格"}) as w:
        w.poll_once()
    assert _status(stack, conv_pid) == "converged"

    resp = c.post("/api/proposals/reclaim-stale", json={"lease_seconds": 0})
    assert resp.status_code == 200
    reclaimed = resp.json()["reclaimed"]
    assert answered_pid not in reclaimed, "answered 提案不应被回收"
    assert conv_pid not in reclaimed, "converged 提案不应被回收"
    assert _status(stack, answered_pid) == "answered"
    assert _status(stack, conv_pid) == "converged"
