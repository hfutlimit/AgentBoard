"""Proposal 澄清 Worker 消费者（Epic 96 · Story 155 P1-2）。

Epic 96 的澄清回路在 P0 交付了 REST 层与前端问答工作台，P1-1 交付了 6 个
``proposal_*`` MCP 工具。但工具只是「入口」——没有常驻消费者，用户提交的提案
会永远停在 ``queued``，必须靠人手工在 MCP 客户端里逐个调工具才能推进。

本模块补上执行侧：一个**无状态、可横向扩容、崩溃可恢复**的 Worker 进程。

调度模型
--------
Worker 每轮扫描两类工作项（P1 用 DB 轮询，P2 由 RabbitMQ 替换，届时仅替换
`fetch_work()` 的来源，其余逻辑不变）：

- ``queued``   —— 用户刚派发，需要第一轮澄清；
- ``answered`` —— 用户已答完上一轮，需要进入下一轮澄清或收敛。

两者统一走 ``claim → 全量重放 → 调 Agent → 落决策`` 的同一条流水线。

全量重放（幂等的根基）
----------------------
Worker **不持有会话状态**。每次分析都把「提案正文 + 全部历史轮次问答（含
unsure 标记）」重新拼成一份完整上下文喂给 Agent。因此：

- 任意时刻杀掉 Worker，另一个 Worker 接手后结果一致；
- 消息 at-least-once 重投不会污染状态（叠加 ``(proposal_id, round_no)``
  唯一约束，重复 ask 幂等复用既有轮次）。

崩溃恢复
--------
Worker 在 ``analyzing`` 中途崩溃，提案会卡在 ``analyzing``。任一 Worker 调用
``POST /api/proposals/reclaim-stale`` 即可把租约过期者批量回退 ``queued`` 重投
——这条回退边在 ``PROPOSAL_TRANSITIONS`` 中已预留（analyzing → queued「超时回退」）。

租约判定由服务端依据 ``claimed_at`` 完成，**不再使用 ``updated_at``**：后者带
onupdate，用户作答等与持有者无关的写入都会刷新它，会让崩溃 Worker 的租约被无限
续期，提案永久卡死。

并发安全（P2-0）
----------------
认领走服务端 **CAS 原子端点** ``POST /api/proposals/{pid}/claim``：判定与写入压在
单条条件 UPDATE 里由数据库仲裁，恰好一个 Worker 拿到 200，其余全部 409。

不能退回用 ``PUT /status`` 认领：状态机对同状态迁移（analyzing→analyzing）是
幂等 no-op 返回 200，根本不具备仲裁能力，N 个 Worker 会同时「认领成功」。

约束
----
- 仅通过既有 REST 端点工作，**零 REST 契约变更**；
- 不导入 ``mcp_server``（那会拉入 fastmcp 依赖），复制少量重放逻辑保持解耦；
- 不触碰端口 18001。

运行::

    python -m agentboard.worker --once     # 跑一轮就退出（调试 / cron）
    python -m agentboard.worker --loop     # 常驻轮询
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Protocol

import httpx

from . import mq

log = logging.getLogger("agentboard.worker")

# Agent 可以给出的决策
ACTION_ASK = "ask"
ACTION_FINALIZE = "finalize"
ACTION_FAIL = "fail"
# Proposal → Ticket 转化（文档 #59）：agent 已通过 AgentBoard MCP 的
# proposal_create_ticket 工具完成创建，worker 回查请求状态确认。
ACTION_TICKET_CREATED = "ticket_created"
# Ticket 全流程（2026-08-09）：agent 处理 Story 编排后确认（story_handled）。
# agent 经 MCP 逐步推进 Story 下 task（design→实现→评审→测试），完成后打印该 action。
ACTION_STORY_HANDLED = "story_handled"
VALID_ACTIONS = {ACTION_ASK, ACTION_FINALIZE, ACTION_FAIL,
                 ACTION_TICKET_CREATED, ACTION_STORY_HANDLED}

# Worker 会主动认领的状态：queued=首轮，answered=用户答完进入下一轮
CLAIMABLE_STATUSES = ("queued", "answered")


# ===================== 异常 =====================

class WorkerError(Exception):
    """Worker 侧可预期的失败基类。"""


class AgentInvocationError(WorkerError):
    """无头 Agent 调用失败（进程退出码非零 / 超时 / 无法启动）。"""


class AgentOutputError(WorkerError):
    """Agent 输出无法解析为合法决策。"""


# ===================== 配置 =====================

def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        log.warning("环境变量 %s=%r 不是整数，回退默认值 %s", name, raw, default)
        return default


@dataclass
class WorkerConfig:
    """Worker 运行参数，全部可由环境变量覆盖（容器部署友好）。"""

    api_url: str = "http://127.0.0.1:58124"
    # 服务账号 abk_ key（或登录 token）；身份归属由 proposal_id 反查项目与提出人
    token: str | None = None
    agent: str = "worker"
    # Agent MQ 消费身份（2026-08-09）：设置后本 Worker 以该 agent 身份消费
    # 自己的 direct queue（agent_queue）接收指定任务；留空则仅澄清/轮询。
    agent_id: str = ""
    # 轮询间隔（秒）
    poll_interval: float = 10.0
    # 单轮最多处理多少个提案，避免一个 Worker 长时间独占
    batch_size: int = 5
    # analyzing 租约（秒）：超过即判定持有者已崩溃，回退 queued 重投
    lease_seconds: int = 1800
    # 澄清轮次上限，防止 Agent 无限提问
    max_rounds: int = 5
    # 无头 Agent 命令模板（留空则不启用子进程调用，须显式传入 invoker）
    agent_cmd: str = ""
    # 单次 Agent 调用超时（秒）
    agent_timeout: int = 900
    http_timeout: float = 30.0
    # 消息总线（P2）。url 为空即禁用，Worker 回退 P1 轮询模式。
    mq: "mq.MQConfig" = field(default_factory=lambda: mq.MQConfig())
    # MQ 模式下的维护周期（秒）：回收超租约 + 自愈重投遗留工作项
    maintenance_interval: float = 60.0
    # Agent 心跳探测周期（秒，Ticket 全流程 2026-08-09）：worker 主动经 CLI 判活
    heartbeat_interval: float = 60.0
    # 单次 CLI 探测超时（秒）
    heartbeat_timeout: float = 8.0

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        return cls(
            mq=mq.MQConfig.from_env(),
            maintenance_interval=float(
                _env_int("AGENTBOARD_WORKER_MAINTENANCE_INTERVAL", 60)),
            heartbeat_interval=float(
                _env_int("AGENTBOARD_WORKER_HEARTBEAT_INTERVAL", 60)),
            heartbeat_timeout=float(
                _env_int("AGENTBOARD_WORKER_HEARTBEAT_TIMEOUT", 8)),
            api_url=os.getenv("AGENTBOARD_API_URL", cls.api_url).rstrip("/"),
            token=os.getenv("AGENTBOARD_WORKER_TOKEN")
            or os.getenv("AGENTBOARD_MCP_TOKEN"),
            agent=os.getenv("AGENTBOARD_WORKER_AGENT", cls.agent),
            agent_id=os.getenv("AGENTBOARD_WORKER_AGENT_ID", ""),
            poll_interval=float(_env_int("AGENTBOARD_WORKER_INTERVAL", 10)),
            batch_size=_env_int("AGENTBOARD_WORKER_BATCH", 5),
            lease_seconds=_env_int("AGENTBOARD_WORKER_LEASE", 1800),
            max_rounds=_env_int("AGENTBOARD_WORKER_MAX_ROUNDS", 5),
            agent_cmd=os.getenv("AGENTBOARD_WORKER_AGENT_CMD", ""),
            agent_timeout=_env_int("AGENTBOARD_WORKER_AGENT_TIMEOUT", 900),
        )


# ===================== Agent 决策 =====================

@dataclass
class AgentDecision:
    """无头 Agent 一次分析的产出。

    ``ask``      —— 还需澄清，给出本轮 open questions；
    ``finalize`` —— 澄清收敛，给出最终需求规格，等待人工终审；
    ``fail``     —— Agent 判定无法处理（信息严重不足 / 超出能力）。
    """

    action: str
    questions: list[str] = field(default_factory=list)
    summary: str = ""
    converged_spec: str = ""
    error: str = ""
    round: int | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "AgentDecision":
        if not isinstance(data, dict):
            raise AgentOutputError(f"Agent 决策必须是 JSON 对象，实际为 {type(data).__name__}")
        action = str(data.get("action") or "").strip().lower()
        if action not in VALID_ACTIONS:
            raise AgentOutputError(
                f"Agent 决策 action={action!r} 非法，仅允许 {sorted(VALID_ACTIONS)}"
            )
        raw_qs = data.get("questions") or []
        if isinstance(raw_qs, str):
            raw_qs = [raw_qs]
        questions = [str(q).strip() for q in raw_qs if str(q).strip()]
        if action == ACTION_ASK and not questions:
            raise AgentOutputError("action=ask 必须给出至少一个非空问题")
        spec = str(data.get("converged_spec") or "").strip()
        if action == ACTION_FINALIZE and not spec:
            raise AgentOutputError("action=finalize 必须给出非空 converged_spec")
        rnd = data.get("round")
        return cls(
            action=action,
            questions=questions,
            summary=str(data.get("summary") or "").strip(),
            converged_spec=spec,
            error=str(data.get("error") or "").strip(),
            round=int(rnd) if isinstance(rnd, (int, float, str)) and str(rnd).strip().isdigit() else None,
        )


# ===================== 无头 Agent 调用 =====================

class AgentInvoker(Protocol):
    """无头 Agent 适配器。实现方只需把上下文变成一个决策。"""

    def invoke(self, context: dict) -> AgentDecision:  # pragma: no cover - 协议声明
        ...


class CallableAgentInvoker:
    """把任意可调用对象包装成 Invoker —— 测试与内嵌策略用。"""

    def __init__(self, fn: Callable[[dict], Any]):
        self._fn = fn

    def invoke(self, context: dict) -> AgentDecision:
        out = self._fn(context)
        if isinstance(out, AgentDecision):
            return out
        return AgentDecision.from_dict(out)


def extract_decision_json(stdout: str) -> dict:
    """从 Agent stdout 中抽取**最后一个**顶层 JSON 对象。

    真实 CLI（WorkBuddy / Claude Code / Codex）会在决策前后打印大量日志、进度、
    Markdown 包裹（```json ... ```）。这里做括号配对扫描而不是正则，能正确跳过
    字符串内的花括号；取「最后一个」是因为 Agent 常先思考再给结论。
    """
    text = (stdout or "").strip()
    if not text:
        raise AgentOutputError("Agent 未输出任何内容（stdout 为空）")

    candidates: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidates.append(text[start:i + 1])
                    start = -1

    for blob in reversed(candidates):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "action" in data:
            return data
    # 没有带 action 的对象时，退而求其次取最后一个可解析对象，让上层报更精确的错
    for blob in reversed(candidates):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise AgentOutputError(
        "Agent 输出中找不到合法 JSON 决策对象；原始输出片段："
        + text[-400:].replace("\n", " ")
    )


def build_prompt(context: dict) -> str:
    """把全量重放上下文渲染成给无头 Agent 的提示词。

    协议刻意做成「一次调用、一次决策、纯 JSON 收口」：Agent 无需记忆，
    每轮都拿到完整历史，输出严格 JSON，Worker 只负责落库。

    ``context.get("action") == "create_ticket"`` 时渲染**转化模式**提示词：
    需求已收敛，指示 agent 通过 AgentBoard MCP 的 ``proposal_create_ticket``
    工具创建指定类型的 ticket（文档 #59），然后打印确认 JSON。

    ``context.get("action") == "process_story"`` 时渲染**Story 执行模式**提示词
    （Ticket 全流程，2026-08-09）：Story 已被用户确认，指示 agent 经 AgentBoard
    MCP 逐步推进其下 task（先 design 后实现，含评审与测试）。

    ``context.get("action") == "process_task"`` 时渲染**单 Task 执行模式**提示词
    （MQ 竞争/定向编排）：agent 竞争认领或收到指定任务后，推进该 task 到完成。
    """
    if str(context.get("action") or "") == "create_ticket":
        return _build_ticket_prompt(context)
    if str(context.get("action") or "") == "process_story":
        return _build_story_prompt(context)
    if str(context.get("action") or "") == "process_task":
        return _build_task_prompt(context)
    lines = [
        "你是需求澄清分析师。请阅读下面的需求提案与全部历史问答，判断需求是否已足够清晰。",
        "",
        "## 决策协议（必须严格遵守）",
        "在输出的最后打印一个 JSON 对象，且只能是以下三种之一：",
        '1. 仍需澄清：{"action":"ask","questions":["问题1","问题2"],"summary":"本轮聚焦点"}',
        '2. 已经收敛：{"action":"finalize","converged_spec":"最终需求规格(Markdown)"}',
        '3. 无法处理：{"action":"fail","error":"原因"}',
        "问题要具体、可回答，不要重复历史中已问过或已答明确的内容。",
        "",
        f"## 提案 #{context.get('proposal_id')}：{context.get('title')}",
        "",
        str(context.get("content") or "(无正文)"),
        "",
        f"## 当前轮次：{context.get('current_round', 0)}",
    ]
    history = context.get("history") or []
    if history:
        lines += ["", "## 历史问答（全量重放）"]
        for h in history:
            mark = "（用户标记不确定）" if h.get("unsure") else ""
            ans = h.get("answer") or ("(尚未作答)" if not h.get("answered") else "(空答案)")
            lines.append(f"- [第{h.get('round')}轮] Q: {h.get('question')}")
            lines.append(f"  A: {ans}{mark}")
    else:
        lines += ["", "## 历史问答", "(暂无，这是第一轮澄清)"]
    return "\n".join(lines)


def _build_ticket_prompt(context: dict) -> str:
    """转化模式提示词：指示 agent 用 AgentBoard MCP 生成 ticket（文档 #59）。"""
    ttype = str(context.get("ticket_type") or "")
    parent_epic = context.get("parent_epic_id")
    parent_story = context.get("parent_story_id")
    lines = [
        "你是需求落单助手。下面的提案已通过多轮澄清收敛（converged_spec 即最终需求规格）。",
        "",
        f"## 任务：把提案 #{context.get('proposal_id')} 生成为「{ttype}」类型工单",
        "",
        "请调用 **AgentBoard MCP 工具 `proposal_create_ticket`** 完成创建，参数：",
        f"- proposal_id: {context.get('proposal_id')}",
        f"- type: {ttype}",
    ]
    if parent_epic is not None:
        lines.append(f"- epic_id: {parent_epic}")
    if parent_story is not None:
        lines.append(f"- story_id: {parent_story}")
    if context.get("ticket_title"):
        lines.append(f"- title: {context.get('ticket_title')}")
    lines += [
        "",
        "## 决策协议（必须严格遵守）",
        "调用成功后，在输出的最后打印 JSON：",
        '{"action":"ticket_created"}',
        "若调用失败（工具报错），打印：",
        '{"action":"fail","error":"原因"}',
        "不要省略参数、不要修改 type。若你所在环境没有 AgentBoard MCP 连接，",
        "直接打印 {\"action\":\"fail\",\"error\":\"缺少 AgentBoard MCP 连接\"}。",
        "",
        f"## 提案 #{context.get('proposal_id')}：{context.get('title')}",
        "",
        str(context.get("content") or "(无正文)"),
    ]
    spec = str(context.get("converged_spec") or "").strip()
    if spec:
        lines += ["", "## 最终需求规格（converged_spec，工单 description 的权威来源）", spec]
    return "\n".join(lines)


def _build_story_prompt(context: dict) -> str:
    """Story 执行模式提示词（Ticket 全流程，2026-08-09）。

    指示 agent 经 AgentBoard MCP 推进 Story 下 task 的下一步：
    - 铁律一：needs_design=true 时，**design task 必须先完成评审**
      （in_design → design_pending_review → design_review_approved），之后才能推进实现 task；
    - 铁律二：实现 task 须走 in_progress → in_review（提交评审）→ 评审通过 → done；
    - 每完成一个里程碑，同步用 MCP 的 update_story 推进 Story 状态
      （设计完成 → todo；开发中 → in_progress；评审 → in_review；全 done → done）；
    - 一次调用尽量推进所有当前可推进的步骤；全部完成后打印 story_handled。
    """
    story_id = context.get("story_id")
    tasks = context.get("tasks") or []
    lines = [
        "你是软件开发执行 Agent。下面的 Story 已被用户确认，请经 AgentBoard MCP 自动推进其下任务。",
        "",
        "## 执行铁律（必须严格遵守）",
        "1. **顺序约定**：needs_design=true 时，必须先完成「设计」任务（type=design，走 "
        "in_design → design_pending_review → design_review_approved 评审流），"
        "评审通过后才能推进「实现」任务（服务端已强制，违反会收到 400）；",
        "2. 实现任务流程：in_progress（开发）→ in_review（用 submit_task_for_review 提交评审）"
        "→ 评审通过 → done → 必要时 verifying（测试）；",
        "3. 每个里程碑完成后，用 MCP `update_story` 同步推进 Story 状态"
        "（设计完成→todo，开发中→in_progress，评审中→in_review，全部完成→done）；",
        "4. 一次调用内尽量推进所有当前可推进的步骤；无需等待外部人工输入。",
        "",
        "## 决策协议（必须严格遵守）",
        "全部可推进步骤完成后，在输出最后打印 JSON：",
        '{"action":"story_handled","summary":"本轮完成的工作"}',
        "若无法继续（缺 MCP 连接 / 依赖缺失 / 需求不清晰等），打印：",
        '{"action":"fail","error":"原因"}',
        "",
        f"## Story #{story_id}：{context.get('title')}",
        "",
        str(context.get("description") or "(无描述)"),
        "",
        f"## needs_design: {context.get('needs_design')}",
        "",
        "## 当前任务列表（经 MCP list_tasks 也可获取最新状态）",
    ]
    for t in tasks:
        lines.append(
            f"- [{t.get('type')}] #{t.get('id')} {t.get('title')} status={t.get('status')}"
            f"{' reviewer=' + str(t.get('reviewer_id')) if t.get('reviewer_id') else ''}"
        )
    return "\n".join(lines)


def _build_task_prompt(context: dict) -> str:
    """单 Task 执行模式提示词（MQ 竞争/定向编排，2026-08-09）。

    agent 竞争认领（task.available）或收到指定任务（task.assigned）后，
    经 AgentBoard MCP 把该 task 推进到完成：
    - design task（type=design / story.needs_design=true）：走 in_design 评审流
      （in_design → design_pending_review → design_review_approved）；
    - 实现 task：in_progress（已认领）→ 开发 → in_review（submit_task_for_review
      提交评审）→ 评审通过 → done → verifying（测试）；
    - 完成后打印 story_handled；失败打印 fail。
    """
    task = context.get("task") or {}
    lines = [
        "你是软件开发执行 Agent。下面这个任务已分配给你（竞争认领成功或指定指派），"
        "请经 AgentBoard MCP 把它推进到完成。",
        "",
        "## 执行要点（必须严格遵守）",
        "1. 任务状态已由 Worker 置 in_progress（开发中）；",
        "2. design 类任务（needs_design=true 的 Story 下 type=design）：推进 "
        "in_design → design_pending_review → design_review_approved（评审流）；",
        "3. 实现任务：开发完成后用 MCP `submit_task_for_review` 提交评审（in_review），"
        "评审通过 → done，必要时 verifying（测试）；",
        "4. 若任务已 done 或被他人处理，直接报告完成即可，不要重复操作。",
        "",
        "## 决策协议（必须严格遵守）",
        "处理完成（或确认无需处理）后，在输出最后打印 JSON：",
        '{"action":"story_handled","summary":"本轮完成的工作"}',
        "若无法继续（缺 MCP 连接 / 依赖缺失 / 需求不清晰等），打印：",
        '{"action":"fail","error":"原因"}',
        "",
        f"## Task #{task.get('id')}：{task.get('title')}",
        "",
        f"- type: {task.get('type')} | 当前状态: {task.get('status')}",
        f"- 所属 Story: #{context.get('story_id')}（needs_design={context.get('needs_design')}）",
        f"- assignee: {context.get('assignee_id')}",
        "",
        str(task.get("description") or task.get("spec") or "(无描述)"),
    ]
    return "\n".join(lines)


def split_command(cmd: str) -> list[str]:
    """把命令模板拆成 argv，兼容 Windows 路径。

    Windows 上必须用 ``posix=False``，否则 ``C:\\Users\\x`` 里的反斜杠会被当成
    转义符吃掉；但 ``posix=False`` 又会把引号原样保留在 token 里，导致
    ``subprocess`` 拿到带引号的可执行文件名而报 WinError 2。这里补一刀去掉
    成对的外层引号——两个坑必须一起填，只填一个都跑不起来。
    """
    if os.name != "nt":
        return shlex.split(cmd)
    out = []
    for tok in shlex.split(cmd, posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


class SubprocessAgentInvoker:
    """无头拉起本机 Agent CLI（WorkBuddy / Claude Code / Codex 等）。

    命令模板用 shell 语法书写并 `shlex.split`；prompt 通过 **stdin** 喂入，
    避免超长命令行在 Windows 上被截断，也免去转义地狱。
    """

    def __init__(self, cmd: str, timeout: int = 900, cwd: str | None = None,
                 env: dict | None = None):
        if not str(cmd).strip():
            raise ValueError("agent_cmd 不能为空")
        self.cmd = str(cmd)
        self.argv = split_command(self.cmd)
        self.timeout = timeout
        self.cwd = cwd
        # Windows 编码修复（2026-08-10 review）：子进程 print() 默认走
        # locale.getpreferredencoding()，在 zh-CN 系统上是 cp936/GBK；父进程
        # encoding="utf-8" 读会得到一堆 replacement char（U+FFFD），导致
        # extract_decision_json 找不到合法 JSON、决策 action 永远停在 ask。
        # 解法：注入 PYTHONIOENCODING=utf-8 + PYTHONUTF8=1 到子进程环境，
        # 强制 Python 子进程按 UTF-8 编码 stdout/stderr。
        base = dict(env) if env is not None else dict(os.environ)
        base["PYTHONIOENCODING"] = "utf-8"
        base["PYTHONUTF8"] = "1"
        self.env = base

    def invoke(self, context: dict) -> AgentDecision:
        prompt = build_prompt(context)
        try:
            proc = subprocess.run(
                self.argv, input=prompt, capture_output=True, text=True,
                timeout=self.timeout, cwd=self.cwd, env=self.env,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            raise AgentInvocationError(
                f"Agent 调用超时（>{self.timeout}s）：{self.cmd}"
            ) from None
        except (FileNotFoundError, OSError) as e:
            raise AgentInvocationError(f"Agent 命令无法启动：{self.cmd}（{e}）") from None
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise AgentInvocationError(
                f"Agent 退出码 {proc.returncode}：{tail or '(无输出)'}"
            )
        return AgentDecision.from_dict(extract_decision_json(proc.stdout))


# ===================== 时间工具 =====================

def _parse_dt(value: Any) -> datetime | None:
    """解析后端返回的时间串；naive 一律按 UTC 处理（服务端用 utc_now 落库）。"""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ===================== Worker 主体 =====================

class ProposalWorker:
    """澄清回路消费者：发现 → 认领 → 全量重放 → 调 Agent → 落决策。"""

    def __init__(self, config: WorkerConfig, invoker: AgentInvoker | None = None,
                 client: httpx.Client | None = None):
        self.config = config
        self.invoker = invoker or self._default_invoker(config)
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=config.api_url, timeout=config.http_timeout,
            headers=({"Authorization": f"Bearer {config.token}"} if config.token else {}),
        )
        # Ticket 全流程（2026-08-09）：Story 编排节流与失败计数（进程内，重启重置可接受）
        self._story_attempts: dict[int, float] = {}      # story_id → 上次拉起时间戳
        self._story_fail_counts: dict[int, int] = {}     # story_id → 连续失败次数
        self._story_min_interval: float = 30.0           # 同一 Story 最小拉起间隔（秒）
        self._last_heartbeat_ts: float = 0.0             # 轮询模式下心跳节流时间戳

    @staticmethod
    def _default_invoker(config: WorkerConfig) -> AgentInvoker:
        if not config.agent_cmd.strip():
            raise ValueError(
                "未配置 AGENTBOARD_WORKER_AGENT_CMD，且未显式传入 invoker —— "
                "Worker 不知道该拉起哪个无头 Agent"
            )
        return SubprocessAgentInvoker(config.agent_cmd, timeout=config.agent_timeout)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "ProposalWorker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- HTTP ----------

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        return self.client.request(method, path, **kw)

    def _get_json(self, path: str, **kw) -> Any:
        r = self._request("GET", path, **kw)
        r.raise_for_status()
        return r.json()

    # ---------- 发现 ----------

    def fetch_work(self) -> list[dict]:
        """拉取本轮待处理提案：queued（首轮）在前，answered（续轮）在后。

        P2 接入 RabbitMQ 后仅需替换本方法的数据来源，下游流水线保持不变。
        """
        items: list[dict] = []
        seen: set[int] = set()
        limit = max(1, self.config.batch_size)
        try:
            queued = self._get_json("/api/proposals/pending", params={"limit": limit})
        except Exception as e:
            log.warning("拉取 queued 提案失败：%s", e)
            queued = []
        for p in queued or []:
            if p.get("id") not in seen:
                seen.add(p["id"])
                items.append(p)
        remaining = limit - len(items)
        if remaining > 0:
            try:
                answered = self._get_json(
                    "/api/proposals", params={"status": "answered", "limit": remaining},
                )
            except Exception as e:
                log.warning("拉取 answered 提案失败：%s", e)
                answered = []
            for p in answered or []:
                if p.get("id") not in seen:
                    seen.add(p["id"])
                    items.append(p)
        return items

    # ---------- 崩溃恢复 ----------

    def reclaim_stale(self) -> list[int]:
        """把租约过期的 analyzing 提案回退 queued（原持有 Worker 已崩溃）。

        这是整个自动化闭环唯一的「丢单兜底」——没有它，一次进程被 kill
        就会让提案永久卡在 analyzing。

        判定与回退整体下沉到服务端 ``POST /api/proposals/reclaim-stale``：
        一次批量条件 UPDATE 完成，既避免「拉全表再逐个 PUT」的 N+1，也让多个
        Worker 同时回收时不会互相打架（未命中者 rowcount 为 0，天然幂等）。
        """
        r = self._request(
            "POST", "/api/proposals/reclaim-stale",
            json={"lease_seconds": self.config.lease_seconds},
        )
        if r.status_code != 200:
            log.warning("回收超租约提案失败：%s %s", r.status_code, r.text[:200])
            return []
        try:
            ids = (r.json() or {}).get("reclaimed") or []
        except Exception as e:
            log.warning("回收响应解析失败：%s", e)
            return []
        for pid in ids:
            log.warning("提案 #%s 租约超时（analyzing 停滞 >%ss），已回退 queued 重投",
                        pid, self.config.lease_seconds)
        return list(ids)

    def reclaim_stale_ticket_requests(self) -> list[int]:
        """回收处理中超时的转换请求（processing 停滞 → failed，proposal 回退
        converged）。

        与提案租约回收（``reclaim_stale``）互补：CLI/进程在 request 已进入
        processing 后崩溃时，没有本回收就没有自动兜底——request 会永久停在
        processing，除非人工调 API（2026-08-09 review 补全）。判定与回退整体
        下沉到服务端 ``POST /api/ticket-requests/reclaim-stale``（admin-only，
        worker 须用 admin 服务账号 token）。
        """
        r = self._request(
            "POST", "/api/ticket-requests/reclaim-stale",
            json={"lease_seconds": self.config.lease_seconds},
        )
        if r.status_code != 200:
            log.warning("回收超时转换请求失败：%s %s", r.status_code, r.text[:200])
            return []
        try:
            ids = (r.json() or {}).get("reclaimed") or []
        except Exception as e:
            log.warning("回收响应解析失败：%s", e)
            return []
        for rid in ids:
            log.warning("ticket 请求 #%s 处理超时（processing 停滞 >%ss），"
                        "已回退 proposal → converged", rid, self.config.lease_seconds)
        return list(ids)

    def recover_failed(self) -> list[int]:
        """把「Agent 不可用」导致的 failed 提案自动回退 queued 重投。

        设计原则（2026-08-09）：agent 拉起失败（命令无法启动/找不到/调用异常）
        **不应要求前端手动重试**——由本维护 job 周期自动重投，直至 agent 恢复
        或服务端达到 max_retries 上限转人工。与 reclaim_stale（analyzing 租约
        超时）互补，共同构成自动闭环的自愈回路。
        """
        r = self._request(
            "POST", "/api/proposals/recover-failed",
            json={"window_seconds": 120, "max_retries": 5},
        )
        if r.status_code != 200:
            log.warning("回收 agent 失败提案异常：%s %s", r.status_code, r.text[:200])
            return []
        try:
            ids = (r.json() or {}).get("recovered") or []
        except Exception as e:
            log.warning("回收响应解析失败：%s", e)
            return []
        for pid in ids:
            log.info("提案 #%s agent 不可用导致 failed，已自动回退 queued 重投", pid)
        return list(ids)

    # ---------- 认领 ----------

    def claim(self, proposal: dict) -> bool:
        """queued/answered → analyzing。竞争失败返回 False 并静默跳过。

        走服务端 **CAS 原子认领端点**，单次调用完成判定与写入，无 TOCTOU 窗口：
        并发下数据库仲裁出恰好一个赢家（200），其余一律 409。

        不要退回「先 GET 复核状态再 PUT /status」的老写法：状态机对同状态迁移是
        幂等 no-op（analyzing→analyzing 返回 200 而非 400），PUT 本身无仲裁能力，
        前置 GET 只能收窄窗口而不能消除它。
        """
        pid = proposal.get("id")
        r = self._request("POST", f"/api/proposals/{pid}/claim",
                          json={"agent": self.config.agent})
        if r.status_code == 200:
            return True
        if r.status_code == 409:
            log.info("提案 #%s 认领竞争失败（已被其它 Worker 抢到或状态已变）", pid)
            return False
        if r.status_code == 404:
            log.info("提案 #%s 已不存在，跳过", pid)
            return False
        log.warning("提案 #%s 认领异常：%s %s", pid, r.status_code, r.text[:200])
        return False

    # ---------- 全量重放上下文 ----------

    def build_context(self, proposal_id: int) -> dict:
        """提案正文 + 全部历史轮次问答，语义与 MCP ``proposal_get`` 一致。"""
        proposal = self._get_json(f"/api/proposals/{proposal_id}")
        rounds = self._get_json(f"/api/proposals/{proposal_id}/rounds")
        history: list[dict] = []
        open_questions: list[dict] = []
        for r in rounds or []:
            for q in r.get("questions", []) or []:
                answered = bool(q.get("answered_at"))
                item = {
                    "round": r.get("round_no"),
                    "question_id": q.get("id"),
                    "seq": q.get("seq"),
                    "question": q.get("question"),
                    "answer": q.get("answer") or "",
                    "unsure": bool(q.get("unsure")),
                    "answered": answered,
                }
                history.append(item)
                if not answered:
                    open_questions.append(item)
        return {
            "proposal_id": proposal.get("id"),
            "project_id": proposal.get("project_id"),
            "title": proposal.get("title"),
            "content": proposal.get("content") or "",
            "status": proposal.get("status"),
            "current_round": proposal.get("current_round", 0),
            "converged_spec": proposal.get("converged_spec") or "",
            "rounds": rounds or [],
            "history": history,
            "open_questions": open_questions,
            "answered_count": sum(1 for h in history if h["answered"]),
            "total_questions": len(history),
            "max_rounds": self.config.max_rounds,
        }

    # ---------- 决策落库 ----------

    def _apply_ask(self, proposal_id: int, decision: AgentDecision) -> str:
        body: dict[str, Any] = {
            "questions": decision.questions,
            "summary": decision.summary,
            "agent": self.config.agent,
        }
        if decision.round is not None:
            body["round"] = decision.round
        r = self._request("POST", f"/api/proposals/{proposal_id}/questions", json=body)
        if r.status_code not in (200, 201):
            raise WorkerError(f"回写问题失败：{r.status_code} {r.text[:200]}")
        return "asked"

    def _apply_finalize(self, proposal_id: int, decision: AgentDecision) -> str:
        r = self._request("PATCH", f"/api/proposals/{proposal_id}",
                          json={"converged_spec": decision.converged_spec})
        if r.status_code != 200:
            raise WorkerError(f"写入 converged_spec 失败：{r.status_code} {r.text[:200]}")
        r = self._request("PUT", f"/api/proposals/{proposal_id}/status",
                          json={"status": "converged"})
        if r.status_code != 200:
            raise WorkerError(f"推进 converged 失败：{r.status_code} {r.text[:200]}")
        return "converged"

    def mark_failed(self, proposal_id: int, error: str) -> str:
        """把提案落到 failed 并带上可读原因（failed 可回退 queued 重投）。"""
        r = self._request("PUT", f"/api/proposals/{proposal_id}/status",
                          json={"status": "failed", "error": error[:2000]})
        if r.status_code != 200:
            log.error("提案 #%s 标记 failed 失败：%s %s",
                      proposal_id, r.status_code, r.text[:200])
        return "failed"

    # ---------- 单个提案处理 ----------

    def handle(self, proposal: dict) -> str:
        """处理一个提案，返回结果码：skipped / asked / converged / failed。

        任何异常都会被收敛成 failed，绝不让提案静默卡在 analyzing。
        """
        pid = proposal.get("id")
        if not self.claim(proposal):
            return "skipped"
        try:
            context = self.build_context(pid)
        except Exception as e:
            log.exception("提案 #%s 构建上下文失败", pid)
            return self.mark_failed(pid, f"构建重放上下文失败：{e}")

        current_round = int(context.get("current_round") or 0)
        try:
            decision = self.invoker.invoke(context)
        except (AgentInvocationError, AgentOutputError) as e:
            log.warning("提案 #%s Agent 调用失败：%s", pid, e)
            return self.mark_failed(pid, str(e))
        except Exception as e:  # 适配器实现方的意外异常同样兜住
            log.exception("提案 #%s Agent 调用抛出未预期异常", pid)
            return self.mark_failed(pid, f"Agent 调用异常：{e}")

        # 轮次上限护栏：达到上限还要继续提问，说明澄清不收敛，转失败等人工介入
        if decision.action == ACTION_ASK and current_round >= self.config.max_rounds:
            msg = (f"已达最大澄清轮次 {self.config.max_rounds}（当前第 {current_round} 轮）"
                   f"仍未收敛，转人工介入")
            log.warning("提案 #%s %s", pid, msg)
            return self.mark_failed(pid, msg)

        try:
            if decision.action == ACTION_ASK:
                return self._apply_ask(pid, decision)
            if decision.action == ACTION_FINALIZE:
                return self._apply_finalize(pid, decision)
            return self.mark_failed(pid, decision.error or "Agent 主动判定无法处理")
        except WorkerError as e:
            log.warning("提案 #%s 落库失败：%s", pid, e)
            return self.mark_failed(pid, str(e))
        except Exception as e:
            log.exception("提案 #%s 落库抛出未预期异常", pid)
            return self.mark_failed(pid, f"决策落库异常：{e}")

    # ---------- Proposal → Ticket 转化（文档 #59，2026-08-08）----------

    def fetch_ticket_requests(self) -> list[dict]:
        """拉取待认领转换请求（status=pending）。"""
        try:
            return (
                self._get_json(
                    "/api/ticket-requests/pending",
                    params={"limit": self.config.batch_size},
                ) or []
            )
        except Exception as e:
            log.warning("拉取 pending ticket 请求失败：%s", e)
            return []

    def claim_ticket_request(self, request: dict) -> bool:
        """pending → processing（CAS）。竞争失败静默跳过。"""
        rid = request.get("id")
        pid = request.get("proposal_id")
        r = self._request(
            "POST", f"/api/proposals/{pid}/ticket-requests/{rid}/claim", json={},
        )
        if r.status_code == 200:
            return True
        if r.status_code == 409:
            log.info("ticket 请求 #%s 认领竞争失败（已被其它 Worker 处理）", rid)
            return False
        log.warning("ticket 请求 #%s 认领异常：%s %s", rid, r.status_code, r.text[:200])
        return False

    def build_ticket_context(self, request: dict) -> dict:
        """提案全量重放 + 工单指令（语义与 MCP proposal_get 一致，多出 ticket 字段）。"""
        pid = request.get("proposal_id")
        proposal = self._get_json(f"/api/proposals/{pid}")
        rounds = self._get_json(f"/api/proposals/{pid}/rounds")
        history: list[dict] = []
        for r in rounds or []:
            for q in r.get("questions", []) or []:
                history.append({
                    "round": r.get("round_no"),
                    "question_id": q.get("id"),
                    "question": q.get("question"),
                    "answer": q.get("answer") or "",
                    "unsure": bool(q.get("unsure")),
                    "answered": bool(q.get("answered_at")),
                })
        return {
            "action": "create_ticket",
            "proposal_id": proposal.get("id"),
            "project_id": proposal.get("project_id"),
            "title": proposal.get("title"),
            "content": proposal.get("content") or "",
            "status": proposal.get("status"),
            "converged_spec": proposal.get("converged_spec") or "",
            "history": history,
            "ticket_request_id": request.get("id"),
            "ticket_type": request.get("type"),
            "parent_epic_id": request.get("parent_epic_id"),
            "parent_story_id": request.get("parent_story_id"),
            "ticket_title": request.get("title") or "",
        }

    def _fail_ticket_request(self, request: dict, error: str) -> str:
        rid = request.get("id")
        pid = request.get("proposal_id")
        r = self._request(
            "POST", f"/api/proposals/{pid}/ticket-requests/{rid}/fail",
            json={"error": error[:2000]},
        )
        if r.status_code != 200:
            log.error("ticket 请求 #%s 标记 failed 失败：%s %s",
                      rid, r.status_code, r.text[:200])
        return "failed"

    def _confirm_ticket(self, request: dict) -> str:
        """agent 声称已创建后，轮询回查请求状态确认（防 agent 谎报）。

        状态语义：done → 成功；failed → 已被判失败；pending/processing →
        agent 的 MCP 调用可能仍在进行，继续等待；超时（6×5s）仍非 done → 判失败。
        """
        rid = request.get("id")
        pid = request.get("proposal_id")
        for _ in range(6):
            try:
                reqs = self._get_json(f"/api/proposals/{pid}/ticket-requests")
                cur = next((r for r in reqs or [] if r.get("id") == rid), None)
                if cur:
                    st = cur.get("status")
                    if st == "done":
                        log.info("ticket 请求 #%s 已生成（ticket_id=%s）",
                                 rid, cur.get("ticket_id"))
                        return "created"
                    if st == "failed":
                        log.warning("ticket 请求 #%s 已被标记失败：%s",
                                    rid, cur.get("error") or "")
                        return "failed"
            except Exception as e:
                log.warning("ticket 请求 #%s 回查异常：%s", rid, e)
            time.sleep(5)
        return self._fail_ticket_request(request, "agent 执行超时，ticket 未生成")

    def handle_ticket_request(self, request: dict) -> str:
        """处理一个转换请求：拉起 agent（指示经 MCP 生成）→ 回查确认。

        不做预认领：``execute-by-type`` 端点内部 CAS（pending → processing → done）
        已保证并发下恰一个 agent 创建成功；竞争失败方回查后静默跳过，绝不让
        「他人正在成功执行」的请求被判失败。

        返回结果码：skipped / created / failed。
        """
        rid = request.get("id")
        pid = request.get("proposal_id")
        try:
            context = self.build_ticket_context(request)
        except Exception as e:
            log.exception("ticket 请求 #%s 构建上下文失败", rid)
            return self._fail_ticket_request(request, f"构建上下文失败：{e}")
        try:
            decision = self.invoker.invoke(context)
        except (AgentInvocationError, AgentOutputError) as e:
            log.warning("ticket 请求 #%s Agent 调用失败：%s", rid, e)
            return self._fail_ticket_request(request, str(e))
        except Exception as e:
            log.exception("ticket 请求 #%s Agent 调用抛出未预期异常", rid)
            return self._fail_ticket_request(request, f"Agent 调用异常：{e}")
        if decision.action == ACTION_TICKET_CREATED:
            return self._confirm_ticket(request)
        # agent 主动放弃（含 execute 409 竞争失败）：回查现状，不盲目判失败
        # —— 若他人已完成则视为成功；否则跳过，由 reclaim-stale 超时兜底 + 用户重试。
        log.warning("ticket 请求 #%s agent 未创建：%s", rid, decision.error or "无原因")
        try:
            reqs = self._get_json(f"/api/proposals/{pid}/ticket-requests")
            cur = next((r for r in reqs or [] if r.get("id") == rid), None)
            if cur and cur.get("status") == "done":
                return "created"
            if cur and cur.get("status") == "failed":
                return "failed"
        except Exception:
            pass
        return "skipped"

    # ---------- Ticket 全流程：Story 执行编排（2026-08-09） ----------

    def fetch_confirmed_stories(self) -> list[dict]:
        """拉取待处理的 Story（status=confirmed，用户已确认的人工闸门）。

        confirmed 语义 = 用户确认 + agent 流水线处理中（worker 周期拉起推进；
        节流防高频拉起）。全部 task done 后 worker 将 Story 置 done 结束。
        """
        try:
            data = self._get_json("/api/stories", params={
                "status": "confirmed", "limit": max(1, self.config.batch_size),
            })
            return (data or {}).get("items", []) or []
        except Exception as e:
            log.warning("拉取 confirmed Story 失败：%s", e)
            return []

    def build_story_context(self, story: dict) -> dict:
        """Story 全量重放 + 其下任务列表（供执行模式提示词）。"""
        sid = story.get("id")
        tasks = self._get_json(f"/api/stories/{sid}/tasks", params={"limit": 200})
        return {
            "action": "process_story",
            "story_id": sid,
            "project_id": story.get("epic_id"),
            "title": story.get("title"),
            "description": story.get("description") or "",
            "needs_design": bool(story.get("needs_design", True)),
            "status": story.get("status"),
            "tasks": (tasks or {}).get("items", []) if isinstance(tasks, dict) else (tasks or []),
        }

    def _story_comment(self, story_id: int, content: str) -> None:
        """在 Story 上落一条执行记录评论（失败原因/进展，审计载体）。"""
        try:
            self._request("POST", f"/api/stories/{story_id}/comments",
                          json={"author": self.config.agent, "content": content[:2000]})
        except Exception as e:
            log.warning("Story #%s 评论失败：%s", story_id, e)

    def _set_story_status(self, story_id: int, status: str) -> bool:
        try:
            r = self._request("PATCH", f"/api/stories/{story_id}",
                              json={"status": status})
            return r.status_code in (200, 201)
        except Exception as e:
            log.warning("Story #%s 置 %s 失败：%s", story_id, status, e)
            return False

    def _complete_story(self, story_id: int) -> bool:
        """Story 自动收尾：POST /api/stories/{sid}/complete（任意非 done/blocked → done）。"""
        try:
            r = self._request("POST", f"/api/stories/{story_id}/complete")
            return r.status_code in (200, 201)
        except Exception as e:
            log.warning("Story #%s 自动收尾失败：%s", story_id, e)
            return False

    def _claim_story(self, story_id: int) -> bool:
        """竞争认领：POST /api/stories/{sid}/claim（CAS confirmed→todo）。

        409 = 已被其它 Worker 实例认领/状态不可认领 → 返回 False（本轮跳过）。
        """
        try:
            r = self._request("POST", f"/api/stories/{story_id}/claim")
            return r.status_code in (200, 201)
        except Exception as e:
            log.warning("Story #%s 认领异常：%s", story_id, e)
            return False

    def _unclaim_story(self, story_id: int) -> bool:
        """认领交接/失败回退：POST /api/stories/{sid}/unclaim（CAS todo→confirmed）。"""
        try:
            r = self._request("POST", f"/api/stories/{story_id}/unclaim")
            return r.status_code in (200, 201)
        except Exception as e:
            log.warning("Story #%s 回退异常：%s", story_id, e)
            return False

    def _story_all_tasks_done(self, story: dict) -> bool:
        """Story 下任务是否全部完成（收尾判据）。

        - 实现 task（type=task）：done 即完成；
        - design task（type=design）：终态是 design_review_approved（设计评审通过
          即交付完成，不再流转到 done），故 approved 亦视为完成。
        """
        sid = story.get("id")
        try:
            data = self._get_json(f"/api/stories/{sid}/tasks", params={"limit": 200})
            tasks = (data or {}).get("items", []) if isinstance(data, dict) else (data or [])
        except Exception as e:
            log.warning("Story #%s 回查任务失败：%s", sid, e)
            return False

        def finished(t: dict) -> bool:
            if t.get("status") == "done":
                return True
            return t.get("type") == "design" and t.get("status") == "design_review_approved"

        pending = [t for t in tasks if not finished(t)]
        return not pending

    def handle_story(self, story: dict) -> str:
        """处理一个 confirmed Story：**竞争认领** → 拉起 agent 推进其下任务。

        多 Worker 实例（不同 agent CLI）编排的竞争模型：
        - **认领**：POST claim（服务端 CAS confirmed→todo）恰一赢家，409 → skipped；
          todo = 已被某实例认领处理中（其它实例扫描 confirmed 不再看到）；
        - agent 返回 story_handled + 全部 task done → complete（done 收尾）；
        - story_handled 但任务未全完成（部分推进）→ **unclaim 回退 confirmed**
          交接，下轮/其它实例继续；
        - agent 失败/异常 → 评论 + 失败计数 + **unclaim 回退 confirmed** 重试；
          连续 3 次 → Story 置 blocked 转人工（不回退）；
        - 节流：同一 Story 最小拉起间隔 30s（防失败风暴空转）。

        返回结果码：skipped（节流/认领失败/已删除）/ handled / blocked / failed。
        """
        sid = story.get("id")
        if sid is None:
            return "skipped"
        now = time.time()
        last = self._story_attempts.get(sid, 0.0)
        if now - last < self._story_min_interval:
            return "skipped"
        self._story_attempts[sid] = now
        # 竞争认领（CAS）：恰一赢家；409（他人已领/状态不可认领）→ 本轮跳过
        if not self._claim_story(sid):
            log.info("Story #%s 认领失败（其它 Worker 已处理或状态不可认领），跳过", sid)
            return "skipped"
        try:
            context = self.build_story_context(story)
        except Exception as e:
            log.exception("Story #%s 构建上下文失败", sid)
            self._story_comment(sid, f"Worker 构建上下文失败：{e}")
            return self._story_fail(sid, f"构建上下文失败：{e}")
        try:
            decision = self.invoker.invoke(context)
        except (AgentInvocationError, AgentOutputError) as e:
            log.warning("Story #%s Agent 调用失败：%s", sid, e)
            return self._story_fail(sid, str(e))
        except Exception as e:
            log.exception("Story #%s Agent 调用抛出未预期异常", sid)
            return self._story_fail(sid, f"Agent 调用异常：{e}")
        if decision.action == ACTION_STORY_HANDLED:
            # 本轮执行成功：节流清零，继续扫描（若任务未全完成则交接下轮继续）
            self._story_fail_counts.pop(sid, None)
            if self._story_all_tasks_done(story):
                ok = self._complete_story(sid)
                log.info("Story #%s 全部任务完成，自动收尾 done=%s", sid, ok)
                return "handled" if ok else "failed"
            # 部分推进：unclaim 回退 confirmed，交接给下轮/其它实例
            ok = self._unclaim_story(sid)
            log.info("Story #%s 本轮推进完成（任务未全部完成），回退 confirmed=%s", sid, ok)
            return "handled"
        # agent 主动放弃
        return self._story_fail(sid, decision.error or "Agent 未报告完成原因")

    def _story_fail(self, sid: int, error: str) -> str:
        """Story 处理失败：评论 + 计数 + 回退 confirmed 重试；连续 3 次 → blocked。"""
        self._story_comment(sid, f"Agent 自动处理失败：{error}")
        count = self._story_fail_counts.get(sid, 0) + 1
        self._story_fail_counts[sid] = count
        if count >= 3:
            self._story_fail_counts.pop(sid, None)
            ok = self._set_story_status(sid, "blocked")
            log.warning("Story #%s 连续 %s 次失败，置 blocked 转人工（%s）", sid, count, ok)
            return "blocked"
        # 未达上限：unclaim 回退 confirmed，重新入池待重试
        ok = self._unclaim_story(sid)
        log.info("Story #%s 失败（第 %s 次），回退 confirmed 待重试（%s）", sid, count, ok)
        return "failed"

    def _story_scan_loop(self, stop: threading.Event) -> None:
        """Story 编排扫描兜底（MQ 模式下 workflow 总线事件由 Workflow Worker ack，
        本线程按 poll_interval 周期扫描 confirmed Story，与轮询模式 poll_once 对齐）。"""
        while not stop.wait(self.config.poll_interval):
            try:
                for story in self.fetch_confirmed_stories():
                    try:
                        self.handle_story(story)
                    except Exception:
                        log.exception("Story #%s 处理异常", story.get("id"))
            except Exception:
                log.exception("Story 编排扫描周期异常，将在下个周期重试")

    # ---------- Ticket 全流程：Agent 心跳探测（2026-08-09） ----------

    def _probe_cli(self, cmd: str, model: str = "") -> tuple[bool, str]:
        """CLI 可用性探测：``<cmd> --version``（8s 超时）。

        - ``{model}`` 占位符替换（同 CLI 多 agent 各注入模型；空 model 移除占位符）；
        - 返回 ``(ok, message)``：message 为探测详情（版本号 / 超时 / 退出码），
          随 heartbeat/deregister 上报落 probe_message（前端实时展示）。
        """
        full = str(cmd or "").strip().replace("{model}", (model or "").strip())
        if not full.strip():
            return False, "未配置 cli_command"
        if "{model}" in full:
            full = full.replace("{model}", "").strip()
        try:
            argv = split_command(full) + ["--version"]
        except ValueError as e:
            return False, f"命令解析失败：{e}"
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.config.heartbeat_timeout,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return False, f"探测超时 {self.config.heartbeat_timeout}s"
        except (OSError, FileNotFoundError, ValueError) as e:
            log.debug("Agent CLI 探测失败 %r：%s", cmd, e)
            return False, f"无法启动 CLI：{e}"
        ok = proc.returncode == 0
        detail = ""
        if (proc.stdout or "").strip():
            detail = proc.stdout.strip().splitlines()[0][:80]
        elif (proc.stderr or "").strip():
            detail = proc.stderr.strip().splitlines()[-1][:80]
        msg = (f"OK {detail}" if ok else f"exit={proc.returncode} {detail}").strip()
        return ok, msg or ("OK" if ok else f"exit={proc.returncode}")

    def agent_heartbeat_once(self) -> dict:
        """执行一轮 Agent 心跳探测：遍历 agents 表，逐 agent 跑 cli_command 判活。

        - 成功 → POST /api/agents/{id}/heartbeat（probe_ok=true + 版本详情）；
        - 失败 → POST /api/agents/{id}/deregister（probe_message 带原因）；
        - 无 cli_command / enabled=false 的 agent 跳过（依赖 agent 自报心跳 MCP 路径）。
        单 agent 异常不抛出（try/except 包裹），不影响其它 agent 与主循环。
        """
        try:
            agents = self._get_json("/api/agents") or []
        except Exception as e:
            log.warning("拉取 Agent 列表失败（心跳探测跳过本轮）：%s", e)
            return {"checked": 0, "online": 0, "offline": 0, "skipped": 0}
        stats = {"checked": 0, "online": 0, "offline": 0, "skipped": 0}
        for a in agents or []:
            aid = a.get("agent_id")
            cmd = a.get("cli_command") or ""
            if not aid or not cmd:
                stats["skipped"] += 1
                continue
            if not a.get("enabled", True):
                stats["skipped"] += 1
                continue
            stats["checked"] += 1
            try:
                ok, msg = self._probe_cli(cmd, model=a.get("model") or "")
                if ok:
                    r = self._request("POST", f"/api/agents/{aid}/heartbeat",
                                      json={"probe_ok": True, "probe_message": msg})
                    ok_r = r.status_code in (200, 201)
                    stats["online"] += 1 if ok_r else 0
                else:
                    r = self._request("POST", f"/api/agents/{aid}/deregister",
                                      json={"probe_message": msg})
                    ok_r = r.status_code in (200, 201)
                    stats["offline"] += 1 if ok_r else 0
                if not ok_r:
                    log.warning("Agent %s probe 结果上报失败（HTTP %s）", aid, r.status_code)
            except Exception as e:
                log.warning("Agent %s 心跳上报异常：%s", aid, e)
        if stats["checked"]:
            log.info("Agent 心跳探测：%s", stats)
        return stats

    def _agent_heartbeat_loop(self, stop: threading.Event) -> None:
        """后台心跳探测线程（周期 heartbeat_interval，默认 60s）。"""
        while not stop.wait(self.config.heartbeat_interval):
            try:
                self.agent_heartbeat_once()
            except Exception:
                log.exception("Agent 心跳探测周期异常，将在下个周期重试")

    # ---------- 轮询 ----------

    def poll_once(self) -> dict:
        """执行一轮：先做崩溃恢复 + agent 失败自动重投，再消费工作项。"""
        # Ticket 全流程：轮询模式下按 heartbeat_interval 节流跑 Agent 心跳探测
        now_ts = time.time()
        if now_ts - self._last_heartbeat_ts >= self.config.heartbeat_interval:
            self._last_heartbeat_ts = now_ts
            try:
                self.agent_heartbeat_once()
            except Exception:
                log.exception("Agent 心跳探测异常（不阻断本轮）")
        reclaimed = self.reclaim_stale()
        # 2026-08-09 review：转换请求租约同样需要自动回收（CLI 崩溃后
        # processing 停滞无人工则永久卡死）。
        ticket_reclaimed = self.reclaim_stale_ticket_requests()
        recovered = self.recover_failed()
        results: dict[str, int] = {}
        handled: list[dict] = []
        for proposal in self.fetch_work():
            outcome = self.handle(proposal)
            results[outcome] = results.get(outcome, 0) + 1
            handled.append({"proposal_id": proposal.get("id"), "outcome": outcome})
        # Proposal → Ticket 转化（文档 #59）：转换请求与澄清提案同轮消费
        ticket_results: dict[str, int] = {}
        for req in self.fetch_ticket_requests():
            outcome = self.handle_ticket_request(req)
            ticket_results[outcome] = ticket_results.get(outcome, 0) + 1
            handled.append({"ticket_request_id": req.get("id"), "outcome": outcome})
        # Ticket 全流程（2026-08-09）：confirmed Story 编排（agent 自动处理）
        story_results: dict[str, int] = {}
        for story in self.fetch_confirmed_stories():
            outcome = self.handle_story(story)
            story_results[outcome] = story_results.get(outcome, 0) + 1
            handled.append({"story_id": story.get("id"), "outcome": outcome})
        return {
            "reclaimed": reclaimed,
            "ticket_reclaimed": ticket_reclaimed,
            "recovered": recovered,
            "handled": handled,
            "counts": results,
            "ticket_counts": ticket_results,
            "story_counts": story_results,
        }

    def run_forever(self, stop: threading.Event | None = None,
                    max_cycles: int | None = None) -> int:
        """常驻轮询。``stop`` 用于优雅退出，``max_cycles`` 便于测试收敛。"""
        stop = stop or threading.Event()
        cycles = 0
        log.info("Worker 启动：api=%s agent=%s interval=%ss lease=%ss max_rounds=%s",
                 self.config.api_url, self.config.agent, self.config.poll_interval,
                 self.config.lease_seconds, self.config.max_rounds)
        while not stop.is_set():
            try:
                summary = self.poll_once()
                if summary["handled"] or summary["reclaimed"]:
                    log.info("本轮处理：%s（回收 %s）", summary["counts"], summary["reclaimed"])
            except Exception:
                log.exception("轮询周期异常，将在下个周期重试")
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            stop.wait(self.config.poll_interval)
        log.info("Worker 退出，共执行 %s 轮", cycles)
        return cycles

    # ---------- MQ 消费（P2） ----------

    def handle_message(self, message: "mq.ProposalMessage") -> bool:
        """处理一条派发消息。返回 False 表示拒收，消息转入死信队列。

        **消息只是提示，数据库才是事实源**：这里一律先回查提案再决策。因此
        重投、过期消息、乱序消息都不会造成错误处理——最坏只是一次空回查。

        ack（返回 True）的三种情形：
        - 提案已删除（404）——工作项不存在了，重投多少次都没意义；
        - 当前状态不可认领——已被其它 Worker 处理或用户尚未答完，正常丢弃；
        - 正常走完 ``handle()`` 流水线（其内部已把各类失败收敛成 failed）。

        拒收（返回 False → 死信）只留给「消息本身没救」的情况：回查 API 持续
        异常。这类消息进死信后可人工排查重投；同时提案仍留在 queued/answered，
        维护线程的自愈重投也会把它捞回来，不存在丢单。
        """
        pid = message.proposal_id
        try:
            r = self._request("GET", f"/api/proposals/{pid}")
        except Exception as e:
            log.warning("提案 #%s 回查失败（%s），消息转入死信待人工重投", pid, e)
            return False
        if r.status_code == 404:
            log.info("提案 #%s 已不存在，丢弃消息", pid)
            return True
        if r.status_code != 200:
            log.warning("提案 #%s 回查异常：%s %s，消息转入死信",
                        pid, r.status_code, r.text[:200])
            return False
        proposal = r.json()
        status = str(proposal.get("status") or "")
        if status not in CLAIMABLE_STATUSES:
            log.info("提案 #%s 当前状态 %s 不可认领（已被处理或尚未就绪），丢弃消息",
                     pid, status)
            return True
        outcome = self.handle(proposal)
        log.info("提案 #%s 消费完成：%s", pid, outcome)
        return True

    def sweep(self, publisher: "mq.ProposalPublisher") -> int:
        """自愈重投：把仍滞留在 queued/answered 的工作项重新投递。

        MQ 是 at-least-once，但**不是 exactly-once，也不是永不丢**：broker 重启、
        消息进死信、发布时 broker 恰好不可达，都可能让某个提案没有对应消息。
        这条周期性清扫让「消息丢失」自愈——数据库里还挂着的活，总会被重新推一遍。

        只投递 queued/answered（analyzing 说明有人正在干），叠加服务端 CAS，
        重复投递不会造成重复处理。
        """
        count = 0
        for proposal in self.fetch_work():
            pid = proposal.get("id")
            if pid and publisher.publish(pid, proposal.get("current_round") or 0,
                                         "sweep"):
                count += 1
        if count:
            log.info("自愈重投 %s 个滞留工作项", count)
        return count

    def _maintenance_loop(self, publisher: "mq.ProposalPublisher",
                          stop: threading.Event) -> None:
        """后台维护：回收超租约（提案 + 转换请求）+ 自愈重投。与消费主循环解耦。"""
        while not stop.wait(self.config.maintenance_interval):
            try:
                self.reclaim_stale()
                self.reclaim_stale_ticket_requests()
                self.sweep(publisher)
            except Exception:
                log.exception("维护周期异常，将在下个周期重试")

    def _ticket_scan_loop(self, stop: threading.Event) -> None:
        """MQ 模式兜底（2026-08-09 review）：Proposal Worker 的 MQ 消费的是
        澄清消息队列，``proposal.ticket_requested`` 事件在 workflow 总线
        （由 Workflow Worker 确认 ack），本 Worker 收不到——转换请求由本线程
        按 poll_interval 周期扫描兜底，与轮询模式 ``poll_once`` 的
        ``fetch_ticket_requests`` 分支对齐，避免 MQ 模式下 Proposal 永久卡在
        ticket_preparing。
        """
        while not stop.wait(self.config.poll_interval):
            try:
                for req in self.fetch_ticket_requests():
                    try:
                        self.handle_ticket_request(req)
                    except Exception:
                        log.exception("ticket 请求 #%s 处理异常", req.get("id"))
            except Exception:
                log.exception("ticket 扫描周期异常，将在下个周期重试")

    def run_mq_forever(self, stop: threading.Event | None = None,
                       max_messages: int | None = None,
                       idle_timeout: float | None = None,
                       broker: Any | None = None,
                       publisher: "mq.ProposalPublisher | None" = None) -> dict:
        """MQ 竞争消费模式（P2，替换 P1 的 DB 轮询）。

        多个 Worker 连同一个队列，broker 按 ``prefetch=1`` 分发，服务端 CAS 认领
        做第二重仲裁——即便消息被重复投递给两个 Worker，也只有一个能真正开工。

        未配置 MQ 时**自动回退轮询**，保证部署未就绪不影响功能。
        """
        broker = broker if broker is not None else mq.build_broker(self.config.mq)
        if broker is None:
            log.warning("未配置 AGENTBOARD_MQ_URL（或 pika 不可用），回退 P1 轮询模式")
            cycles = self.run_forever(stop=stop)
            return {"mode": "poll", "cycles": cycles}

        stop = stop or threading.Event()
        publisher = publisher or mq.ProposalPublisher(self.config.mq)
        broker.declare_topology()
        log.info("Worker 以 MQ 模式启动：ns=%s prefetch=%s api=%s agent=%s",
                 self.config.mq.namespace, self.config.mq.prefetch,
                 self.config.api_url, self.config.agent)

        # 启动即做一次崩溃恢复：上一代 Worker 可能带着租约挂了
        try:
            self.reclaim_stale()
        except Exception:
            log.exception("启动期回收超租约提案失败，继续消费")

        keeper = threading.Thread(
            target=self._maintenance_loop, args=(publisher, stop),
            name="proposal-worker-maintenance", daemon=True,
        )
        keeper.start()
        # 2026-08-09 review：ticket 请求扫描兜底线程（MQ 模式下主循环收不到
        # workflow 总线上的 ticket_requested 事件，须周期轮询拉取 pending）。
        ticket_keeper = threading.Thread(
            target=self._ticket_scan_loop, args=(stop,),
            name="proposal-worker-ticket-scan", daemon=True,
        )
        ticket_keeper.start()
        # Ticket 全流程（2026-08-09）：confirmed Story 编排扫描线程（MQ 模式下
        # workflow 总线的 story.confirmed 事件由 Workflow Worker ack，本 Worker
        # 周期轮询 confirmed Story 拉起 agent 推进，与轮询模式 poll_once 对齐）。
        story_keeper = threading.Thread(
            target=self._story_scan_loop, args=(stop,),
            name="proposal-worker-story-scan", daemon=True,
        )
        story_keeper.start()
        # Ticket 全流程（2026-08-09）：Agent 心跳探测线程（CLI 判活，置 online/offline）
        heartbeat_keeper = threading.Thread(
            target=self._agent_heartbeat_loop, args=(stop,),
            name="proposal-worker-agent-heartbeat", daemon=True,
        )
        heartbeat_keeper.start()
        try:
            stats = broker.consume(
                self.handle_message, max_messages=max_messages,
                idle_timeout=idle_timeout, stop=stop,
            )
        finally:
            stop.set()
            keeper.join(timeout=2)
            ticket_keeper.join(timeout=2)
            story_keeper.join(timeout=2)
            heartbeat_keeper.join(timeout=2)
        stats["mode"] = "mq"
        log.info("Worker MQ 模式退出：%s", stats)
        return stats

    # ---------- Agent MQ 消费（2026-08-09）：广播竞争 + 定向 direct ----------

    def build_task_context(self, task: dict) -> dict:
        """单 Task 上下文：task + 所属 Story 摘要（needs_design 决定走哪条执行流）。"""
        story_id = task.get("story_id")
        needs_design = True
        if story_id:
            try:
                story = self._get_json(f"/api/stories/{story_id}")
                needs_design = bool(story.get("needs_design", True))
            except Exception:
                pass
        return {
            "action": "process_task",
            "task": task,
            "story_id": story_id,
            "needs_design": needs_design,
        }

    def _task_comment(self, task_id: int, content: str) -> None:
        try:
            self._request("POST", f"/api/tasks/{task_id}/comments",
                          json={"author": self.config.agent, "content": content[:2000]})
        except Exception as e:
            log.warning("task#%s 评论失败：%s", task_id, e)

    def _process_task(self, task_id: int, task: dict) -> bool:
        """拉起 agent 推进单个 task（认领/定向后）：构建上下文 → invoke → 落评论。"""
        try:
            context = self.build_task_context(task)
        except Exception as e:
            log.exception("task#%s 构建上下文失败", task_id)
            return False
        try:
            decision = self.invoker.invoke(context)
        except (AgentInvocationError, AgentOutputError) as e:
            log.warning("task#%s Agent 调用失败：%s", task_id, e)
            self._task_comment(task_id, f"Agent 自动处理失败：{e}")
            return True  # ack：失败留评论，task 停留当前态（人工/轮询兜底）
        except Exception:
            log.exception("task#%s Agent 调用抛出未预期异常", task_id)
            return True
        if decision.action == ACTION_STORY_HANDLED:
            log.info("task#%s 本轮处理完成", task_id)
            return True
        self._task_comment(task_id, decision.error or "Agent 未报告完成原因")
        return True

    def handle_task_available(self, msg: "mq.WorkflowMessage") -> bool:
        """广播 task.available 竞争处理：回查 → CAS 认领（claim）→ 拉起 agent。

        claim 服务端 CAS（backlog/todo → in_progress + assignee）恰一赢家；
        409 = 他人已认领 → 正常丢弃（不转死信）。
        """
        tid = msg.entity_id
        try:
            task = self._get_json(f"/api/tasks/{tid}")
        except Exception as e:
            log.warning("task.available 回查 task#%s 失败：%s", tid, e)
            return False  # 转死信（轮询兜底会再捞）
        if task.get("status") not in ("backlog", "todo"):
            return True  # 已被处理/认领
        try:
            r = self._request("POST", f"/api/tasks/{tid}/claim")
        except Exception as e:
            log.warning("task#%s 认领异常：%s", tid, e)
            return False
        if r.status_code == 409:
            return True  # 竞争失败：他人已认领
        if r.status_code not in (200, 201):
            log.warning("task#%s 认领失败：%s %s", tid, r.status_code, r.text[:120])
            return True
        log.info("task#%s 竞争认领成功（广播轮）", tid)
        return self._process_task(tid, r.json())

    def handle_direct_task(self, msg: "mq.WorkflowMessage") -> bool:
        """定向任务（task.assigned 投递到本 agent 的 direct queue）：回查后处理。"""
        tid = msg.entity_id
        try:
            task = self._get_json(f"/api/tasks/{tid}")
        except Exception as e:
            log.warning("定向 task#%s 回查失败：%s", tid, e)
            return False
        if task.get("status") not in ("backlog", "todo", "in_progress"):
            return True  # 已结束/不可处理
        log.info("task#%s 定向任务（direct queue）处理", tid)
        return self._process_task(tid, task)

    def handle_workflow_message(self, msg: "mq.WorkflowMessage") -> bool:
        """Workflow 事件分发（Agent MQ 消费）：广播竞争 + 定向任务。"""
        if msg.event == mq.EVENT_TASK_AVAILABLE:
            return self.handle_task_available(msg)
        if msg.event == mq.EVENT_TASK_ASSIGNED:
            return self.handle_direct_task(msg)
        log.info("Agent 忽略非任务事件 %s（entity=%s#%s）",
                 msg.event, msg.entity_type, msg.entity_id)
        return True

    def _wf_broadcast_loop(self, broker: Any, stop: threading.Event) -> None:
        """竞争消费广播队列（task.available）：多 Worker 实例共享同一队列，
        认领经服务端 CAS 仲裁，恰一赢家。MQ 消费为主，轮询兜底为辅。"""
        queue = mq.WorkflowTopology().broadcast_queue
        log.info("Agent 广播竞争线程启动：%s", queue)
        try:
            broker.consume(queue, self.handle_workflow_message, stop=stop)
        except Exception:
            log.exception("广播竞争消费异常退出")
        finally:
            try:
                broker.close()
            except Exception:
                pass

    def _agent_direct_loop(self, broker: Any, agent_id: str, stop: threading.Event) -> None:
        """消费本 agent 的定向队列（direct queue）：接收指定给本 agent 的任务。"""
        queue = mq.WorkflowTopology().agent_queue(agent_id)
        log.info("Agent 定向消费线程启动：%s", queue)
        try:
            broker.consume(queue, self.handle_workflow_message, stop=stop)
        except Exception:
            log.exception("定向队列消费异常退出")
        finally:
            try:
                broker.close()
            except Exception:
                pass

    def run_agent_mq_forever(self, agent_id: str,
                              stop: threading.Event | None = None,
                              max_messages: int | None = None,
                              idle_timeout: float | None = None,
                              broker: Any | None = None,
                              wf_broker: Any | None = None,
                              direct_broker: Any | None = None,
                              publisher: "mq.ProposalPublisher | None" = None) -> dict:
        """Agent MQ 消费模式（2026-08-09）：澄清竞争 + 任务广播竞争 + 定向 direct。

        两个 agent（如 codebuddy / minimax）各跑一个本 Worker 实例
        （``AGENTBOARD_WORKER_AGENT_ID`` 区分身份），共用同一 RabbitMQ：
        - proposal 队列：澄清轮竞争（服务端 CAS 仲裁）；
        - workflow 广播队列：task.available 竞争认领开发任务；
        - 本 agent 的 direct queue（agent_queue(agent_id)）：接收 task.assigned
          指定任务（不经竞争，独享）。
        未配置 MQ 回退 P1 轮询（含 story/ticket 兜底）。
        """
        if broker is None:
            broker = mq.build_broker(self.config.mq)
        if broker is None:
            log.warning("未配置 AGENTBOARD_MQ_URL，回退 P1 轮询模式")
            return {"mode": "poll", "cycles": self.run_forever(stop=stop)}
        stop = stop or threading.Event()
        publisher = publisher or mq.ProposalPublisher(self.config.mq)
        broker.declare_topology()
        # workflow 总线（广播 + 定向）——每消费线程独立 broker 实例（pika 非线程安全）
        wf_topology = mq.WorkflowTopology()
        broadcast_broker = wf_broker
        if broadcast_broker is None:
            broadcast_broker = mq.PikaWorkflowBroker(self.config.mq)
        broadcast_broker.declare_topology()
        direct_b = direct_broker
        if direct_b is None:
            direct_b = mq.PikaWorkflowBroker(self.config.mq)
        direct_b.declare_topology()
        direct_b.declare_agent_queue(agent_id)
        log.info("Agent Worker(%s) MQ 模式启动：澄清竞争 + 广播竞争 + direct=%s",
                 agent_id, wf_topology.agent_queue(agent_id))
        # 澄清主循环 + 维护/兜底线程（复用 run_mq_forever 骨架）
        try:
            self.reclaim_stale()
        except Exception:
            log.exception("启动期回收超租约提案失败，继续消费")
        keeper = threading.Thread(
            target=self._maintenance_loop, args=(publisher, stop),
            name="proposal-worker-maintenance", daemon=True,
        )
        keeper.start()
        ticket_keeper = threading.Thread(
            target=self._ticket_scan_loop, args=(stop,),
            name="proposal-worker-ticket-scan", daemon=True,
        )
        ticket_keeper.start()
        story_keeper = threading.Thread(
            target=self._story_scan_loop, args=(stop,),
            name="proposal-worker-story-scan", daemon=True,
        )
        story_keeper.start()
        heartbeat_keeper = threading.Thread(
            target=self._agent_heartbeat_loop, args=(stop,),
            name="proposal-worker-agent-heartbeat", daemon=True,
        )
        heartbeat_keeper.start()
        # 任务广播竞争线程 + 定向 direct 线程
        wf_threads = [
            threading.Thread(target=self._wf_broadcast_loop,
                             args=(broadcast_broker, stop), daemon=True),
            threading.Thread(target=self._agent_direct_loop,
                             args=(direct_b, agent_id, stop), daemon=True),
        ]
        for t in wf_threads:
            t.start()
        try:
            stats = broker.consume(
                self.handle_message, max_messages=max_messages,
                idle_timeout=idle_timeout, stop=stop,
            )
        finally:
            stop.set()
            for t in ([keeper, ticket_keeper, story_keeper, heartbeat_keeper] + wf_threads):
                t.join(timeout=2)
        stats["mode"] = "agent-mq"
        log.info("Agent Worker(%s) MQ 模式退出：%s", agent_id, stats)
        return stats


# ===================== CLI =====================

def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentboard.worker",
        description="AgentBoard Proposal 澄清 Worker（Epic 96 P1-2）",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="只跑一轮后退出")
    group.add_argument("--loop", action="store_true", help="常驻轮询（默认）")
    group.add_argument("--mq", action="store_true",
                       help="MQ 竞争消费模式（未配置 AGENTBOARD_MQ_URL 时自动回退轮询）")
    parser.add_argument("--agent-id", default=None,
                       help="Agent 身份（MQ 模式）：消费本 agent 定向 direct queue 接收指定任务；"
                            "同时竞争 task.available 广播任务")
    parser.add_argument("--mq-url", default=None, help="覆盖 AGENTBOARD_MQ_URL")
    parser.add_argument("--api-url", default=None, help="覆盖 AGENTBOARD_API_URL")
    parser.add_argument("--agent-cmd", default=None, help="覆盖无头 Agent 命令模板")
    parser.add_argument("--interval", type=float, default=None, help="轮询间隔（秒）")
    parser.add_argument("--max-rounds", type=int, default=None, help="澄清轮次上限")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = WorkerConfig.from_env()
    if args.api_url:
        cfg.api_url = args.api_url.rstrip("/")
    if args.agent_cmd:
        cfg.agent_cmd = args.agent_cmd
    if args.agent_id:
        cfg.agent_id = args.agent_id
    if args.interval is not None:
        cfg.poll_interval = args.interval
    if args.max_rounds is not None:
        cfg.max_rounds = args.max_rounds
    if args.mq_url:
        cfg.mq.url = args.mq_url

    try:
        worker = ProposalWorker(cfg)
    except ValueError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2

    with worker:
        if args.once:
            summary = worker.poll_once()
            print(json.dumps(summary, ensure_ascii=False))
            return 0
        stop = threading.Event()
        try:
            if args.mq:
                if cfg.agent_id:
                    worker.run_agent_mq_forever(cfg.agent_id, stop)
                else:
                    worker.run_mq_forever(stop)
            else:
                worker.run_forever(stop)
        except KeyboardInterrupt:
            stop.set()
            print("收到中断信号，Worker 已停止", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
