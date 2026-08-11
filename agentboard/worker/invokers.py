"""无头 Agent 调用器（Epic 123 Step 2 · 拆分自原 worker.py）。

- ``CallableAgentInvoker``：测试与内嵌策略用（任意可调用 → 决策）；
- ``SubprocessAgentInvoker``：真实 CLI（WorkBuddy / Claude Code / Codex）子进程；
- ``extract_decision_json`` / ``split_command``：子进程输出解析与命令行拆分。

Story 243（Epic 122 S5）：``SubprocessAgentInvoker`` 支持按 ``project_id``
解析本机工作目录（``AGENTBOARD_LOCAL_MAPPINGS`` JSON，由本机配置台
``worker_portal.py`` 写入）——任务属于哪个项目，Agent 就在映射的本地目录里跑。
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import (
    ACTION_ASK,
    ACTION_FINALIZE,
    AgentDecision,
    AgentInvocationError,
    AgentInvoker,
    AgentOutputError,
)

# 运行时从 handlers 惰性导入 build_prompt（避免 config 层反向依赖 prompt 实现）
# 由 ProposalWorker 在构造时注入 prompt_builder，解耦提示词与调用器。
_prompt_builder: Callable[[dict], str] | None = None


def set_prompt_builder(fn: Callable[[dict], str]) -> None:
    """注入全局 prompt 构建函数（ProposalWorker 构造时调用一次）。"""
    global _prompt_builder
    _prompt_builder = fn


def _default_build_prompt(context: dict) -> str:
    """无注入时的兜底（不应在生产路径出现）。"""
    lines = ["## 上下文"]
    for k, v in (context or {}).items():
        if k in ("history", "tasks", "rounds"):
            continue
        lines.append(f"- {k}: {v}")
    lines += ["", '{"action":"fail","error":"prompt builder 未注入"}']
    return "\n".join(lines)


def build_prompt(context: dict) -> str:
    """把全量重放上下文渲染成给无头 Agent 的提示词（委派注入的实现）。"""
    builder = _prompt_builder or _default_build_prompt
    return builder(context)


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


def _resolve_project_cwd(context: dict, fallback: str | None) -> str | None:
    """按 ``context["project_id"]`` 查本机项目映射，返回本地工作目录。

    Story 243（Epic 122 S5）：映射由本机配置台（``worker_portal.py``
    ``AGENTBOARD_LOCAL_MAPPINGS``）写入；无映射/未配置时返回 fallback。
    """
    pid = (context or {}).get("project_id")
    if not pid:
        return fallback
    raw = os.getenv("AGENTBOARD_LOCAL_MAPPINGS")
    if not raw:
        # 默认取 AgentBoard 仓库 tmp/project-mappings.json
        raw = str(Path(__file__).resolve().parent.parent / "tmp" / "project-mappings.json")
    p = Path(raw)
    if not p.exists():
        return fallback
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    proj = (data.get("projects") or {}).get(str(pid))
    if not proj:
        return fallback
    local_dir = str(proj.get("local_dir") or "").strip()
    return local_dir or fallback


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
        # Story 243：按 project_id 解析本机工作目录（无映射回退构造时 cwd）
        cwd = _resolve_project_cwd(context, self.cwd)
        try:
            proc = subprocess.run(
                self.argv, input=prompt, capture_output=True, text=True,
                timeout=self.timeout, cwd=cwd, env=self.env,
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
