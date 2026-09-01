r"""千问办公 (Qwen) API direct invoker — mirrors scripts/minimax_invoker.py.

桥接 .NET Worker 的 SubprocessAgentInvoker 协议 -> OpenAI 兼容的 Qwen chat
completion API（默认 阿里云百炼 / DashScope compatible-mode）。与 minimax_invoker
同协议：Worker 从 stdin 注入 prompt，本脚本从 stdout 吐一个决策 JSON。

"完全访问" (unattended / full access)：单次 completion，无交互式工具审批门，
prompt 即唯一约束 —— 与仓库里 minimax 的无人值守姿态一致（见 appsettings 注释）。

协议:
    stdin  -> prompt (Worker 注入)
    stdout -> {"action":"ask","questions":[...],"summary":"..."}
            | {"action":"finalize","converged_spec":"..."}
            | {"action":"fail","error":"..."}

环境变量:
    QWEN_API_KEY     (必填)  DashScope / 兼容网关的 Bearer key
    QWEN_BASE_URL    (选填)  默认 https://dashscope.aliyuncs.com/compatible-mode/v1
    QWEN_MODEL       (选填)  默认 qwen3.8-flash
    QWEN_TIMEOUT     (选填)  默认 300s
    QWEN_TEMPERATURE (选填)  默认 0.4（决策轮需要稳定）

跑法:
    echo "请对这个 proposal 提 3 个澄清问题" | python scripts/qwen_invoker.py

    # 接 Worker（appsettings 里）：
    Agents:Qwen:Command   = "<abs>\\.venv\\Scripts\\python.exe"
    Agents:Qwen:Arguments = ["<abs>\\scripts\\qwen_invoker.py"]
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
API_KEY = os.environ.get("QWEN_API_KEY", "").strip()
BASE_URL = os.environ.get(
    "QWEN_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
).rstrip("/")
MODEL = os.environ.get("QWEN_MODEL", "qwen3.8-flash").strip()
TIMEOUT = float(os.environ.get("QWEN_TIMEOUT", "300"))
TEMPERATURE = float(os.environ.get("QWEN_TEMPERATURE", "0.4"))


# ---- Worker 协议常量 (action 字符串) ----
ACTION_ASK = "ask"
ACTION_FINALIZE = "finalize"
ACTION_FAIL = "fail"


def _strip_thinking(text: str) -> str:
    """思考型模型会输出  /think 块，先剥掉再找 JSON。"""
    text = re.sub(r"", "", text, flags=re.DOTALL)
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def _extract_decision(content: str) -> dict[str, Any]:
    """从模型输出里抽出 Worker 协议 JSON（与 worker.extract_decision_json 同语义）。

    - 去掉 markdown 代码块包裹
    - 花括号配对扫描拿第一个完整对象
    - 字符串/转义态跟踪，容忍噪声日志
    """
    text = _strip_thinking(content or "")
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text.strip())

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


def _http_post_chat(messages: list[dict], temperature: float = TEMPERATURE,
                    max_tokens: int = 4096) -> str:
    """直打 Qwen chat completions（OpenAI 兼容），返回 assistant content。"""
    if not API_KEY:
        raise RuntimeError("QWEN_API_KEY 未设置")
    url = f"{BASE_URL}/chat/completions"
    body = {
        "model": MODEL,
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
        raise RuntimeError(f"HTTP {e.code} from Qwen: {err_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e}") from e


# ---- prompt 构造 ----
SYSTEM_PROTOCOL = f"""你是 {MODEL}，通过 Worker 与 AgentBoard 协同。
输出必须是 JSON 对象，Action 字段三选一：
- ask:       {{ "action":"ask", "questions": [string, ...], "summary": string }}
- finalize:  {{ "action":"finalize", "converged_spec": markdown_string, "summary": string }}
- fail:      {{ "action":"fail", "error": string }}

不要输出 JSON 以外的任何内容，不要解释、不要 Markdown 包裹。
如果实在无法决定，output {{"action":"fail","error":"<原因>"}}。
"""


def _detect_protocol_intent(prompt: str) -> str:
    """从 Worker 注入的 prompt 头推断决策意图（关键词匹配足够，不做全 LLM 路由）。"""
    p = prompt.lower()
    if "ask" in p[:200] or "澄清" in p[:200] or "open question" in p:
        return ACTION_ASK
    if "finalize" in p or "converged_spec" in p or "收敛" in p[:300] or "需求规格" in p[:300]:
        return ACTION_FINALIZE
    if "fail" in p[:200] or "失败" in p[:200]:
        return ACTION_FAIL
    return ACTION_ASK


def main() -> int:
    if not API_KEY:
        sys.stderr.write("QWEN_API_KEY not set\n")
        return 1

    prompt = sys.stdin.read()
    if not prompt.strip():
        sys.stderr.write("empty prompt on stdin\n")
        return 1

    intent = _detect_protocol_intent(prompt)
    sys.stderr.write(f"[qwen-invoker] intent={intent} model={MODEL} base={BASE_URL}\n")

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
        return 0  # Worker 通过 fail action 接收，不要让进程非零退出被当 crash
    elapsed = time.time() - t0
    sys.stderr.write(f"[qwen-invoker] {elapsed:.1f}s, {len(content)} chars\n")

    decision = _extract_decision(content)
    sys.stdout.write(json.dumps(decision, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
