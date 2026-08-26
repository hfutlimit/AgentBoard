"""Worker 配置与核心数据类型（Epic 123 Step 2 · 拆分自原 worker.py）。

独立模块承载：异常体系、``WorkerConfig``、``AgentDecision``、Agent 决策
协议与常量。不依赖 HTTP / 子进程实现，便于单独测试。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from agentboard.core.infrastructure import messaging as mq

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
ACTION_REVIEW_APPROVE = "approve"
ACTION_REVIEW_REJECT = "reject"
VALID_ACTIONS = {ACTION_ASK, ACTION_FINALIZE, ACTION_FAIL,
                 ACTION_TICKET_CREATED, ACTION_STORY_HANDLED,
                 ACTION_REVIEW_APPROVE, ACTION_REVIEW_REJECT}

# Worker 会主动认领的状态：queued=首轮，answered=用户答完进入下一轮
CLAIMABLE_STATUSES = ("queued", "answered")


# ===================== 异常 =====================

class WorkerError(Exception):
    """Worker 侧可预期的失败基类。"""


class AgentInvocationError(WorkerError):
    """无头 Agent 调用失败（进程退出码非零 / 超时 / 无法启动）。"""


class TransientAgentError(AgentInvocationError):
    """Agent invocation failure that is safe to retry."""


class PermanentAgentError(AgentInvocationError):
    """Agent invocation failure that should not be retried."""


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
    # Worker 机器身份（2026-08-26 P1 修复：多 Worker 部署隔离）。设置后：
    # - ``agent_heartbeat_once`` 改走 ``/api/workers/{worker_id}/instances`` 拉
    #   本机 instances 探测，**绝不**触达其他 Worker 的 instance；
    # - 探测结果通过 ``/api/workers/{worker_id}/agent-instances/{id}/{heartbeat,deregister}``
    #   上报（URL path worker_id 强校验 ownership，防 A 覆盖 B）。
    # 留空 = 旧单 Worker 路径（``GET /api/agents`` + 失败 deregister，
    # 与 ``/api/agents`` 不返回 cli_command 兼容不修；已知行为，单独 P 跟进）。
    worker_id: str = ""
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
    # Story 后台异步执行（2026-08-26 根治）。开启后 process_story 提交到后台
    # 线程池，main loop 不再被 600s 长任务阻塞，能继续拉取 proposal / answered
    # / ticket_request。默认 off 向后兼容；生产建议开。
    async_story_executor: bool = False
    # 后台 Story 线程最大并发（同 invoker 不假设线程安全；默认 1）
    async_story_max_concurrent: int = 1
    # close() 等待后台 Story 完成的最多秒数（超过则强制收尾）
    async_story_join_timeout: float = 30.0
    # P1 架构收口（2026-08-26 review）：统一执行内核
    # - True  → ProposalWorker 把 ExecutionCommand 转给 WorkerCoordinator.dispatch()
    #          所有路径（polling / MQ / async）走同一入口，错误分类一致
    # - False → 走旧 ``handler.handle()`` 路径（保留到所有调用方迁移完）
    # 生产建议开；灰度期可以一台 worker 开 + 一台不开对比
    use_coordinator: bool = True

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
            worker_id=os.getenv("AGENTBOARD_WORKER_ID", ""),
            poll_interval=float(_env_int("AGENTBOARD_WORKER_INTERVAL", 10)),
            batch_size=_env_int("AGENTBOARD_WORKER_BATCH", 5),
            lease_seconds=_env_int("AGENTBOARD_WORKER_LEASE", 1800),
            max_rounds=_env_int("AGENTBOARD_WORKER_MAX_ROUNDS", 5),
            agent_cmd=os.getenv("AGENTBOARD_WORKER_AGENT_CMD", ""),
            agent_timeout=_env_int("AGENTBOARD_WORKER_AGENT_TIMEOUT", 900),
            async_story_executor=_env_int("AGENTBOARD_WORKER_ASYNC_STORY", 0) == 1,
            async_story_max_concurrent=_env_int("AGENTBOARD_WORKER_ASYNC_STORY_CONCURRENCY", 1),
            async_story_join_timeout=float(
                _env_int("AGENTBOARD_WORKER_ASYNC_STORY_JOIN_TIMEOUT", 30)),
            use_coordinator=_env_int("AGENTBOARD_WORKER_USE_COORDINATOR", 1) == 1,
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
    comment: str = ""
    converged_spec: str = ""
    error: str = ""
    round: int | None = None
    # 2026-08-26 增强：agent 自报本次决策前实际看过的项目文件（相对路径数组）。
    # 任何 agent 不强制要求非空；仅做审计与提示，handler 拿到后会 log 一行。
    # 该字段不参与 action 校验，缺失/为 [] 都不影响决策落库。
    inspected_files: list[str] = field(default_factory=list)

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
        raw_files = data.get("inspected_files") or []
        if isinstance(raw_files, str):
            raw_files = [raw_files]
        inspected = [str(f).strip() for f in raw_files if str(f).strip()]
        return cls(
            action=action,
            questions=questions,
            summary=str(data.get("summary") or "").strip(),
            comment=str(data.get("comment") or "").strip(),
            converged_spec=spec,
            error=str(data.get("error") or "").strip(),
            round=int(rnd) if isinstance(rnd, (int, float, str)) and str(rnd).strip().isdigit() else None,
            inspected_files=inspected,
        )


# ===================== 无头 Agent 调用协议 =====================

class AgentInvoker(Protocol):
    """无头 Agent 适配器。实现方只需把上下文变成一个决策。

    Review 2026-08-26 新增 ``invoke_with_prompt`` 协议：
    Coordinator / Worker 在 dispatch 前经过 prepare_execution 算出最终 prompt，
    handler 可以跳过 ``_prompt_builder`` 直接调 ``invoke_with_prompt`` 喂入。
    实现类必须支持这两个方法。
    """

    def invoke(self, context: dict) -> AgentDecision:  # pragma: no cover - 协议声明
        ...

    def invoke_with_prompt(self, prompt: str, context: dict) -> AgentDecision:  # pragma: no cover
        ...
