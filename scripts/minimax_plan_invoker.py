r"""MiniMax Coding Plan 直连 invoker（Anthropic /messages 协议）。

背景（2026-08-25 部署实测）：
    minimax-cli npm 包本机不可用；MiniMax Code 桌面版无无头 LLM 协议；
    api.minimaxi.com OpenAI 协议对当前账号报 503 direct_route_not_configured。
    但桌面登录态 JWT 对 coding plan 网关的 **Anthropic 风格 /messages** 端点
    直接可用（实测 HTTP 200，模型 MiniMax-M2.7-highspeed）。
    本脚本与 scripts/minimax_invoker.py 同协议（stdin→prompt，stdout→决策 JSON），
    仅把传输层换成 Anthropic messages + 登录态 Bearer。

协议（与 Worker 的 SubprocessAgentInvoker 对齐）：
    stdin  → prompt（Worker 注入）
    stdout → {"action":"ask","questions":[...],"summary":"..."}
            | {"action":"finalize","converged_spec":"..."}
            | {"action":"fail","error":"..."}

环境变量：
    MINIMAX_PLAN_AUTH_FILE  登录态文件（默认 %USERPROFILE%/.minimax/local-runtime.auth.json，
                            每次**调用时现读**——桌面端刷新 token 后无需重启 worker）
    MINIMAX_PLAN_BASE_URL   网关 base（默认 https://agent.minimaxi.com/mavis/api/v1/llm/v1）
    MINIMAX_PLAN_MODEL      模型名（默认 MiniMax-M2.7-highspeed；可选 MiniMax-M2.7 / MiniMax-M3）
    MINIMAX_PLAN_TIMEOUT    单次调用超时秒数（默认 300）
    MINIMAX_PLAN_MAX_TOKENS 输出上限（默认 8192）

心跳兼容：
    AgentBoard worker 心跳探测会执行 ``<agent_cmd> --version``；
    本脚本识别 --version 直接输出版本号退出（不读 stdin、不调 API）。

跑法：
    echo "<prompt>" | python scripts/minimax_plan_invoker.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from minimax_invoker import ACTION_FAIL, _extract_decision  # noqa: E402

VERSION = "1.0.0"

AUTH_FILE = os.environ.get(
    "MINIMAX_PLAN_AUTH_FILE",
    str(Path.home() / ".minimax" / "local-runtime.auth.json"),
).strip()
BASE_URL = os.environ.get(
    "MINIMAX_PLAN_BASE_URL",
    "https://agent.minimaxi.com/mavis/api/v1/llm/v1",
).strip().rstrip("/")
MODEL = os.environ.get("MINIMAX_PLAN_MODEL", "MiniMax-M2.7-highspeed").strip()
TIMEOUT = float(os.environ.get("MINIMAX_PLAN_TIMEOUT", "300"))
MAX_TOKENS = int(os.environ.get("MINIMAX_PLAN_MAX_TOKENS", "8192"))

SYSTEM_PROTOCOL = """你是接入 AgentBoard Worker 的无头决策模型。
只输出一个 JSON 对象，action 三选一：
- ask:       { "action":"ask", "questions": [string, ...], "summary": string }
- finalize:  { "action":"finalize", "converged_spec": markdown_string, "summary": string }
- fail:      { "action":"fail", "error": string }

不要输出 JSON 以外的任何内容：不解释、不包裹 Markdown 代码块。
如果实在无法决定，输出 {"action":"fail","error":"<原因>"}。
"""


def load_access_token(auth_file: str) -> str:
    """从 MiniMax Code 登录态文件读取 accessToken（每次调用现读）。"""
    p = Path(auth_file)
    if not p.exists():
        raise RuntimeError(f"登录态文件不存在：{auth_file}")
    data = json.loads(p.read_text(encoding="utf-8"))
    token = ((data.get("auth") or {}).get("accessToken") or "").strip()
    if not token:
        raise RuntimeError(f"登录态文件中没有 auth.accessToken：{auth_file}")
    return token


def http_post_messages(prompt: str) -> str:
    """Anthropic /messages 协议调用，返回拼接后的纯文本输出。"""
    token = load_access_token(AUTH_FILE)
    url = f"{BASE_URL}/messages"
    body = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROTOCOL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "x-api-key": token,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code} from coding-plan gateway: {err_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e}") from e

    blocks = data.get("content")
    parts: list[str] = []
    if isinstance(blocks, str):
        parts.append(blocks)
    elif isinstance(blocks, list):
        for b in blocks:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                # 只要 text 块；thinking 块的内容不是最终答案
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    parts.append(b["text"])
                elif "text" in b and b.get("type") is None:
                    parts.append(str(b["text"]))
    return "".join(parts)


def main() -> int:
    if "--version" in sys.argv or "-V" in sys.argv:
        sys.stdout.write(f"minimax_plan_invoker {VERSION}\n")
        return 0

    prompt = sys.stdin.read()
    if not prompt.strip():
        sys.stderr.write("empty prompt on stdin\n")
        return 1

    t0 = time.time()
    try:
        content = http_post_messages(prompt)
    except Exception as e:  # noqa: BLE001 —— fail 决策走 stdout，进程保持 0 退出
        decision = {"action": ACTION_FAIL, "error": str(e)}
        sys.stdout.write(json.dumps(decision, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0
    elapsed = time.time() - t0
    sys.stderr.write(
        f"[minimax-plan-invoker] model={MODEL} base={BASE_URL} "
        f"{elapsed:.1f}s, {len(content)} chars\n"
    )

    decision = _extract_decision(content)
    sys.stdout.write(json.dumps(decision, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
