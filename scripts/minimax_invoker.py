r"""MiniMax API direct invoker (方案 E · 2026-08-11).

桥接 Worker 的 SubprocessProcessorInvoker 协议 → MiniMax chat completion API。
绕过 minimax-cli(网络装不上) 和 桌面端(没暴露 LLM 协议)。

协议:
    stdin  → prompt (Worker 注入)
    stdout → {"action":"ask","questions":[...],"summary":"..."}
            | {"action":"finalize","converged_spec":"..."}
            | {"action":"fail","error":"..."}

环境变量:
    MINIMAX_API_KEY       (必填)  sk-cp- 开头的 Token Plan Key
    MINIMAX_BASE_URL      (选填)  默认 https://api.minimaxi.com/v1
    MINIMAX_MODEL         (选填)  默认 MiniMax-M2
    MINIMAX_TIMEOUT       (选填)  默认 300s
    MINIMAX_TEMPERATURE   (选填)  默认 0.4(决策轮需要稳定)

跑法:
    # 1) 临时单次
    echo "请对这个 proposal 提 3 个澄清问题" | python scripts/minimax_invoker.py

    # 2) 接 Worker(填入 .env 或 worker env)
    AGENTBOARD_WORKER_AGENT_CMD = r'"<abs>\.venv\Scripts\python.exe" "<abs>\scripts\minimax_invoker.py"'
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any


# ---- env ----
API_KEY = os.environ.get("MINIMAX_API_KEY", "").strip()
BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M2").strip()
TIMEOUT = float(os.environ.get("MINIMAX_TIMEOUT", "300"))
TEMPERATURE = float(os.environ.get("MINIMAX_TEMPERATURE", "0.4"))


# ---- Worker 协议常量(action 字符串) ----
ACTION_ASK = "ask"
ACTION_FINALIZE = "finalize"
ACTION_FAIL = "fail"
ACTION_STORY_HANDLED = "story_handled"  # 留给 StoryHandler 用,本脚本不直接产


# ---- API 调用 ----

def _strip_thinking(text: str) -> str:
    """M2 会输出 <think>...</think> 块,先剥掉再找 JSON。"""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _extract_decision(content: str) -> dict[str, Any]:
    """从模型输出里抽出 Worker 协议 JSON(与 worker.extract_decision_json 同语义)。

    - 优先花括号配对扫描拿第一个完整对象
    - 字符串/转义态跟踪
    - 容忍噪声日志 + Markdown 包裹
    """
    text = _strip_thinking(content or "")
    # 去掉 markdown 代码块包裹
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text.strip())

    # 找第一个 { 并配对
    start = text.find("{")
    if start < 0:
        return {"action": ACTION_FAIL, "error": "no JSON object in model output",
                "raw": (content or "")[:500]}

    depth = 0
    in_str = False
    escape = False
    quote = ""
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_str = False
        else:
            if ch in ('"', "'"):
                in_str = True
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
    if end < 0:
        return {"action": ACTION_FAIL, "error": "unbalanced JSON braces",
                "raw": (content or "")[:500]}
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError as e:
        return {"action": ACTION_FAIL, "error": f"JSON parse: {e}",
                "raw": (content or "")[:500]}


def _http_post_chat(messages: list[dict], model: str | None = None,
                    temperature: float = TEMPERATURE,
                    max_tokens: int = 4096) -> str:
    """直打 MiniMax chat completions,返回 assistant content。"""
    if not API_KEY:
        raise RuntimeError("MINIMAX_API_KEY 未设置")
    url = f"{BASE_URL}/chat/completions"
    body = {
        "model": model or MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code} from MiniMax: {err_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e}") from e


# ---- prompt 构造 ----

# Worker 注入的 prompt 里通常带 "## 上下文" 段落 + "## 决策协议"。
# 简单做:用 system message 强化协议,user message 透传 Worker 的全文。
SYSTEM_PROTOCOL = """你是 MiniMax-M2,通过 Worker 与 AgentBoard 协同。
输出必须是 JSON 对象,Action 字段三选一:
- ask:       { "action":"ask", "questions": [string, ...], "summary": string }
- finalize:  { "action":"finalize", "converged_spec": markdown_string, "summary": string }
- fail:      { "action":"fail", "error": string }

不要输出 JSON 以外的任何内容,不要解释、不要 Markdown 包裹。
如果实在无法决定,output {"action":"fail","error":"<原因>"}。
"""


def _detect_protocol_intent(prompt: str) -> str:
    """从 Worker 注入的 prompt 头推断决策意图。
    Worker 的 ask/finalize/fail prompt 模板会带 "## 任务:..." 段落,
    简单关键词匹配就够,不需要全 LLM 路由。
    """
    p = prompt.lower()
    if "ask" in p[:200] or "澄清" in p[:200] or "open question" in p[:200] or "open question" in p.lower():
        return ACTION_ASK
    if "finalize" in p or "converged_spec" in p or "收敛" in p[:300] or "需求规格" in p[:300]:
        return ACTION_FINALIZE
    if "fail" in p[:200] or "失败" in p[:200]:
        return ACTION_FAIL
    # 默认:ask(澄清轮最常见)
    return ACTION_ASK


# ---- main ----

def main() -> int:
    if not API_KEY:
        sys.stderr.write("MINIMAX_API_KEY not set\n")
        return 1

    prompt = sys.stdin.read()
    if not prompt.strip():
        sys.stderr.write("empty prompt on stdin\n")
        return 1

    intent = _detect_protocol_intent(prompt)
    sys.stderr.write(f"[minimax-invoker] intent={intent} model={MODEL} base={BASE_URL}\n")

    messages = [
        {"role": "system", "content": SYSTEM_PROTOCOL},
        {"role": "user", "content": prompt},
    ]

    t0 = time.time()
    try:
        content = _http_post_chat(messages)
    except Exception as e:
        decision = {"action": ACTION_FAIL, "error": str(e)}
        sys.stdout.write(json.dumps(decision, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0  # Worker 通过 fail action 接收,不要让进程非零退出被当 crash
    elapsed = time.time() - t0
    sys.stderr.write(f"[minimax-invoker] {elapsed:.1f}s, {len(content)} chars\n")

    decision = _extract_decision(content)
    sys.stdout.write(json.dumps(decision, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
