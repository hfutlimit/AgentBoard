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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Protocol

import httpx

from . import mq

log = logging.getLogger("agentboard.worker")

# Agent 可以给出的三种决策
ACTION_ASK = "ask"
ACTION_FINALIZE = "finalize"
ACTION_FAIL = "fail"
VALID_ACTIONS = {ACTION_ASK, ACTION_FINALIZE, ACTION_FAIL}

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

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        return cls(
            mq=mq.MQConfig.from_env(),
            maintenance_interval=float(
                _env_int("AGENTBOARD_WORKER_MAINTENANCE_INTERVAL", 60)),
            api_url=os.getenv("AGENTBOARD_API_URL", cls.api_url).rstrip("/"),
            token=os.getenv("AGENTBOARD_WORKER_TOKEN")
            or os.getenv("AGENTBOARD_MCP_TOKEN"),
            agent=os.getenv("AGENTBOARD_WORKER_AGENT", cls.agent),
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
    """
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
        self.env = env

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

    # ---------- 轮询 ----------

    def poll_once(self) -> dict:
        """执行一轮：先做崩溃恢复，再消费一批工作项。"""
        reclaimed = self.reclaim_stale()
        results: dict[str, int] = {}
        handled: list[dict] = []
        for proposal in self.fetch_work():
            outcome = self.handle(proposal)
            results[outcome] = results.get(outcome, 0) + 1
            handled.append({"proposal_id": proposal.get("id"), "outcome": outcome})
        return {"reclaimed": reclaimed, "handled": handled, "counts": results}

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
        """后台维护：回收超租约 + 自愈重投。与消费主循环解耦，互不阻塞。"""
        while not stop.wait(self.config.maintenance_interval):
            try:
                self.reclaim_stale()
                self.sweep(publisher)
            except Exception:
                log.exception("维护周期异常，将在下个周期重试")

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
        try:
            stats = broker.consume(
                self.handle_message, max_messages=max_messages,
                idle_timeout=idle_timeout, stop=stop,
            )
        finally:
            stop.set()
            keeper.join(timeout=2)
        stats["mode"] = "mq"
        log.info("Worker MQ 模式退出：%s", stats)
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
                worker.run_mq_forever(stop)
            else:
                worker.run_forever(stop)
        except KeyboardInterrupt:
            stop.set()
            print("收到中断信号，Worker 已停止", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
