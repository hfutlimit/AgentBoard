"""Epic 122 (新增) — minimax_invoker.py 单元测试

目标：把 ``scripts/minimax_invoker.py`` 的关键逻辑（决策 JSON 抽取 + HTTP 调用 +
进程入口）从「靠 2026-08-09 真实端到端跑一次」提到「pytest 跑一遍全分支」，覆盖：

1. ``_extract_decision``:
   - 正常 ask 决策（纯文本）;
   - 剥离 ``<think>...</think>`` 块（M2 系列模型特征）;
   - 容忍 Markdown ```json 包裹;
   - 无 JSON → fail action + 错误说明;
   - JSON 截断/不平衡花括号 → fail action。
2. ``_http_post_chat``:
   - HTTP 4xx/5xx → 抛 RuntimeError（含 err_body 摘要）;
   - 网络错误（URLError）→ 抛 RuntimeError。
3. ``main()``:
   - 缺 ``MINIMAX_API_KEY`` → 进程退出码 1 + stderr ``MINIMAX_API_KEY not set``;
   - 正常路径 → stdout 一行 JSON decision，进程退出码 0。

自包含：mock urllib.request.urlopen,不起真实 API,不依赖网络。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
INVOKER = ROOT / "scripts" / "minimax_invoker.py"


# ---- fixtures -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """每个用例隔离 env,避免 MINIMAX_* 跨用例污染。"""
    for k in (
        "MINIMAX_API_KEY", "MINIMAX_BASE_URL", "MINIMAX_MODEL",
        "MINIMAX_TIMEOUT", "MINIMAX_TEMPERATURE",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


def _import_invoker(**pre_env):
    """动态 import minimax_invoker.py（不在 agentboard 包内,直接 importlib）。

    注意：minimax_invoker.py 在 import 时从 os.environ 读 API_KEY / BASE_URL
    等常量,后续 monkeypatch.setenv 改不到模块级常量。所以需要在 import 之前
    把 env 准备好（pre_env 字典塞进 os.environ）。
    """
    for k, v in pre_env.items():
        os.environ[k] = v
    import importlib.util
    spec = importlib.util.spec_from_file_location("minimax_invoker", str(INVOKER))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ok_response(payload: dict) -> mock.Mock:
    r = mock.Mock()
    r.read.return_value = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    r.__enter__ = mock.Mock(return_value=r)
    r.__exit__ = mock.Mock(return_value=False)
    return r


# ---- 1. _extract_decision -----------------------------------------------


def test_extract_decision_plain_ask():
    inv = _import_invoker()
    out = inv._extract_decision(
        '{"action":"ask","questions":["q1","q2"],"summary":"summary"}'
    )
    assert out["action"] == "ask"
    assert out["questions"] == ["q1", "q2"]
    assert out["summary"] == "summary"


def test_extract_decision_strips_think_block():
    """M2 会输出 <think>...</think> 推理块,必须先剥。"""
    inv = _import_invoker()
    content = (
        "<think>用户在问澄清,先想清楚再问 3 个。</think>\n"
        '{"action":"ask","questions":["a","b"],"summary":"x"}'
    )
    out = inv._extract_decision(content)
    assert out["action"] == "ask"
    assert out["questions"] == ["a", "b"]


def test_extract_decision_tolerates_markdown_wrap():
    """模型偶尔会用 ```json ... ``` 包裹决策。"""
    inv = _import_invoker()
    content = (
        '```json\n{"action":"finalize","converged_spec":"## spec","summary":"s"}\n```'
    )
    out = inv._extract_decision(content)
    assert out["action"] == "finalize"
    assert "## spec" in out["converged_spec"]


def test_extract_decision_no_json_returns_fail():
    inv = _import_invoker()
    out = inv._extract_decision("这是普通文本,不是 JSON")
    assert out["action"] == "fail"
    assert "no JSON object" in out["error"] or "raw" in out


def test_extract_decision_unbalanced_braces_returns_fail():
    inv = _import_invoker()
    out = inv._extract_decision('{"action":"ask","questions":[}')  # 括号不闭合
    assert out["action"] == "fail"
    assert "unbalanced" in out["error"] or "raw" in out


def test_extract_decision_chinese_in_think_block():
    """think 块里有中文,剥离后正文必须能正确解出。"""
    inv = _import_invoker()
    content = (
        "<think>用户在问 X,需要先想清楚 3 个角度：技术/业务/用户。</think>"
        '{"action":"ask","questions":["技术栈?","业务目标?","用户群?"],"summary":"总结"}'
    )
    out = inv._extract_decision(content)
    assert out["action"] == "ask"
    assert "技术栈?" in out["questions"]
    assert "业务目标?" in out["questions"]
    assert "用户群?" in out["questions"]


# ---- 2. _http_post_chat 错误分支 ----------------------------------------


def test_http_post_chat_4xx_raises_with_err_body():
    inv = _import_invoker(
        MINIMAX_API_KEY="sk-test-fake",
        MINIMAX_BASE_URL="https://api.minimaxi.com/v1",
    )
    # urllib.error.HTTPError 构造时需要 (url, code, msg, hdrs, fp)
    import urllib.error
    err = urllib.error.HTTPError(
        "https://api.minimaxi.com/v1/chat/completions", 402, "Insufficient Balance",
        {"Content-Type": "application/json"},
        io.BytesIO(b'{"error":"insufficient balance"}'),
    )
    with mock.patch.object(inv.urllib.request, "urlopen", mock.Mock(side_effect=err)):
        with pytest.raises(RuntimeError) as exc:
            inv._http_post_chat(
                [{"role": "user", "content": "hi"}], model="MiniMax-M2",
            )
    assert "402" in str(exc.value)
    assert "insufficient balance" in str(exc.value)


def test_http_post_chat_5xx_raises():
    inv = _import_invoker(MINIMAX_API_KEY="sk-test-fake")
    import urllib.error
    err = urllib.error.HTTPError(
        "https://api.minimaxi.com/v1/chat/completions", 500, "Server Error",
        {}, io.BytesIO(b"internal error"),
    )
    with mock.patch.object(inv.urllib.request, "urlopen", mock.Mock(side_effect=err)):
        with pytest.raises(RuntimeError) as exc:
            inv._http_post_chat([{"role": "user", "content": "x"}])
    assert "500" in str(exc.value)


def test_http_post_chat_network_error_raises():
    inv = _import_invoker(MINIMAX_API_KEY="sk-test-fake")
    import urllib.error
    err = urllib.error.URLError("Name or service not known")
    with mock.patch.object(inv.urllib.request, "urlopen", mock.Mock(side_effect=err)):
        with pytest.raises(RuntimeError) as exc:
            inv._http_post_chat([{"role": "user", "content": "x"}])
    assert "network error" in str(exc.value).lower()


def test_http_post_chat_no_api_key_raises():
    inv = _import_invoker()  # 不传 MINIMAX_API_KEY
    with pytest.raises(RuntimeError) as exc:
        inv._http_post_chat([{"role": "user", "content": "x"}])
    assert "MINIMAX_API_KEY" in str(exc.value)


def test_http_post_chat_success_returns_assistant_content():
    inv = _import_invoker(MINIMAX_API_KEY="sk-test-fake")
    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": "hello from minimax"}}
        ]
    }
    with mock.patch.object(inv.urllib.request, "urlopen",
                           return_value=_ok_response(payload)):
        out = inv._http_post_chat([{"role": "user", "content": "hi"}])
    assert out == "hello from minimax"


# ---- 3. main() 进程入口 --------------------------------------------------


def test_main_no_api_key_exits_1():
    """缺 MINIMAX_API_KEY → 进程退出码 1 + stderr 含 'MINIMAX_API_KEY not set'。"""
    env = {k: v for k, v in os.environ.items() if k != "MINIMAX_API_KEY"}
    out = subprocess.run(
        [sys.executable, str(INVOKER)],
        input="", capture_output=True, text=True, timeout=15, env=env,
    )
    assert out.returncode == 1
    assert "MINIMAX_API_KEY not set" in out.stderr


def test_main_empty_prompt_exits_1():
    env = dict(os.environ)
    env["MINIMAX_API_KEY"] = "sk-test-fake"
    out = subprocess.run(
        [sys.executable, str(INVOKER)],
        input="", capture_output=True, text=True, timeout=15, env=env,
    )
    assert out.returncode == 1
    assert "empty prompt" in out.stderr


def test_main_success_writes_decision_json():
    """正常路径：起本地 fake HTTP server → API 返回决策 → stdout 一行 JSON + exit 0。

    走真实 subprocess + 本地 HTTP server,模拟 minimax_invoker.py 子进程
    完整跑通一遍协议（不要走 monkeypatch,因为 mock 跟子进程不在同一进程空间）。
    """
    import http.server
    import threading

    payload = {
        "choices": [
            {"message": {
                "role": "assistant",
                "content": '{"action":"ask","questions":["x","y"],"summary":"ok"}',
            }}
        ]
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **kw):
            pass  # 静默

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)  # 消费 request body,避免客户端写阻塞
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload_bytes)))
            self.end_headers()
            self.wfile.write(payload_bytes)

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = dict(os.environ)
        env["MINIMAX_API_KEY"] = "sk-test-fake"
        env["MINIMAX_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
        out = subprocess.run(
            [sys.executable, str(INVOKER)],
            input="请提 2 个澄清问题",
            capture_output=True, text=True, timeout=15, env=env,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert out.returncode == 0, (out.stdout, out.stderr)
    # stdout 一行 JSON
    line = out.stdout.strip().splitlines()[-1]
    decision = json.loads(line)
    assert decision["action"] == "ask"
    assert decision["questions"] == ["x", "y"]
    assert decision["summary"] == "ok"


def test_main_api_error_writes_fail_decision():
    """API 4xx：进程仍 exit 0（Worker 协议用 fail action 通信,不要 crash）。"""
    import http.server
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **kw):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = b'{"error":"insufficient balance"}'
            self.send_response(402)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = dict(os.environ)
        env["MINIMAX_API_KEY"] = "sk-test-fake"
        env["MINIMAX_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
        out = subprocess.run(
            [sys.executable, str(INVOKER)],
            input="请提 2 个澄清问题",
            capture_output=True, text=True, timeout=15, env=env,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert out.returncode == 0  # Worker 协议用 fail action,非 crash
    line = out.stdout.strip().splitlines()[-1]
    decision = json.loads(line)
    assert decision["action"] == "fail"
    assert "402" in decision["error"] or "insufficient" in decision["error"]
