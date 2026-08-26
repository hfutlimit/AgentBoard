"""Phase 5 P1 测试（2026-08-26 review）。

覆盖：
1. ``classify_error`` 三态分类（TRANSIENT / PERMANENT / UNKNOWN）
2. ``classify_stderr`` 按关键字分类 non-zero exit（review 明确要求）
3. 4xx HTTP（非 429）→ PERMANENT（client error 不重试）
5xx / 429 / timeout / network → TRANSIENT
3. 未识别错误 → UNKNOWN（默认调用方不重试）
4. ``is_transient_execution_error`` / ``is_permanent_execution_error`` 薄包装
5. Invoker 退出码分类路径走 stderr 关键字（集成验证）
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import subprocess
from unittest import mock

import httpx
import pytest

from agentboard.agent_runtime.config import (
    AgentInvocationError, PermanentAgentError, TransientAgentError,
)
from agentboard.agent_runtime.errors import (
    ErrorCategory,
    classify_error, classify_stderr,
    is_permanent_execution_error, is_transient_execution_error,
)


# =============== 1. classify_stderr 按关键字分类 ===============

@pytest.mark.parametrize("stderr,expected", [
    # Permanent 关键词
    ("Error: unauthorized access", ErrorCategory.PERMANENT),
    ("API key invalid", ErrorCategory.PERMANENT),
    ("permission denied for project", ErrorCategory.PERMANENT),
    ("invalid argument: --model", ErrorCategory.PERMANENT),
    ("malformed request body", ErrorCategory.PERMANENT),
    ("bad arg: missing --api-key", ErrorCategory.PERMANENT),
    ("config error: AGENTBOARD_API_URL not set", ErrorCategory.PERMANENT),
    ("configuration file missing required field", ErrorCategory.PERMANENT),
    ("model not found: gpt-99", ErrorCategory.PERMANENT),
    ("unknown model: claude-5", ErrorCategory.PERMANENT),
    ("unsupported model: gpt-4-turbo", ErrorCategory.PERMANENT),
    ("quota exceeded for this month", ErrorCategory.PERMANENT),
    ("rate limit exceeded (billing)", ErrorCategory.PERMANENT),
    # Transient 关键词
    ("connection refused", ErrorCategory.TRANSIENT),
    ("connection reset by peer", ErrorCategory.TRANSIENT),
    ("upstream server error: 502", ErrorCategory.TRANSIENT),
    ("server error 500", ErrorCategory.TRANSIENT),
    ("too many requests (429)", ErrorCategory.TRANSIENT),
    ("rate limit hit, retry later", ErrorCategory.TRANSIENT),
    ("temporary failure, try again later", ErrorCategory.TRANSIENT),
    ("timeout waiting for response", ErrorCategory.TRANSIENT),
    # Permanent 优先于 transient（如同时出现 "auth" 和 "timeout"）
    ("auth failed: timeout was too short", ErrorCategory.PERMANENT),
    # 未知
    ("some random error we don't know", ErrorCategory.UNKNOWN),
    ("", ErrorCategory.UNKNOWN),
])
def test_classify_stderr(stderr, expected):
    assert classify_stderr(stderr) is expected, (
        f"classify_stderr({stderr!r}) 应={expected.value}，实际结果不同"
    )


# =============== 2. classify_error 三态分类 ===============

def test_classify_explicit_transient_subclass():
    assert classify_error(TransientAgentError("5xx")) is ErrorCategory.TRANSIENT


def test_classify_explicit_permanent_subclass():
    assert classify_error(PermanentAgentError("config")) is ErrorCategory.PERMANENT


def test_classify_agent_invocation_base_class_defaults_transient():
    """Phase 1 P1 修复：AgentInvocationError 基础类默认 transient（向后兼容）。"""
    assert classify_error(AgentInvocationError("cli gone")) is ErrorCategory.TRANSIENT


def test_classify_timeout_transient():
    assert classify_error(TimeoutError("too slow")) is ErrorCategory.TRANSIENT
    assert classify_error(httpx.ConnectError("conn refused")) is ErrorCategory.TRANSIENT


def test_classify_5xx_http_status_transient():
    """5xx HTTPStatusError → TRANSIENT（server 临时挂掉，下次可能好）。"""
    request = httpx.Request("GET", "http://x")
    response = httpx.Response(503, request=request)
    exc = httpx.HTTPStatusError("5xx", request=request, response=response)
    assert classify_error(exc) is ErrorCategory.TRANSIENT


def test_classify_429_http_status_transient():
    """429 → TRANSIENT（rate limit，重试即可）。"""
    request = httpx.Request("GET", "http://x")
    response = httpx.Response(429, request=request)
    exc = httpx.HTTPStatusError("429", request=request, response=response)
    assert classify_error(exc) is ErrorCategory.TRANSIENT


def test_classify_4xx_http_status_permanent():
    """4xx（非 429）→ PERMANENT（client error，重试也没用）。"""
    for code in (400, 401, 403, 404, 422):
        request = httpx.Request("GET", "http://x")
        response = httpx.Response(code, request=request)
        exc = httpx.HTTPStatusError(str(code), request=request, response=response)
        assert classify_error(exc) is ErrorCategory.PERMANENT, (
            f"4xx HTTP {code} 应归 PERMANENT"
        )


def test_classify_unknown_error_unknown_category():
    """未识别的 ValueError → UNKNOWN（默认不重试，让调用方决策）。"""
    assert classify_error(ValueError("nonsense")) is ErrorCategory.UNKNOWN


def test_classify_unknown_does_not_default_to_transient():
    """Phase 5 P1 关键：UNKNOWN ≠ TRANSIENT。\"未识别 = 不重试\"原则。"""
    cat = classify_error(ValueError("foo"))
    assert cat is not ErrorCategory.TRANSIENT, (
        "UNKNOWN 错误不应被默认归为 transient（旧 'return False' 行为）"
    )


# =============== 3. 薄包装向后兼容 ===============

def test_is_transient_execution_error_backward_compat():
    """is_transient_execution_error 是 classify_error 的薄包装（向后兼容）。"""
    assert is_transient_execution_error(TransientAgentError("x")) is True
    assert is_transient_execution_error(PermanentAgentError("x")) is False
    assert is_transient_execution_error(TimeoutError("x")) is True
    # UNKNOWN → False（保守不重试）
    assert is_transient_execution_error(ValueError("unknown")) is False


def test_is_permanent_execution_error():
    assert is_permanent_execution_error(PermanentAgentError("x")) is True
    # UNKNOWN 不视为 permanent（避免误把未知当永久）
    assert is_permanent_execution_error(ValueError("unknown")) is False


# =============== 4. Invoker 退出码分类（集成验证）===============

def test_invoker_classifies_non_zero_exit_via_stderr_keywords():
    """Phase 5 P1 关键场景：invoker 收到非零退出码 + 含 permanent 关键词的 stderr
    → 抛 PermanentAgentError（不再"非零 = 一律 retry"）。"""
    from agentboard.agent_runtime.invokers import SubprocessAgentInvoker

    inv = SubprocessAgentInvoker(cmd="dummy", timeout=5)
    fake_proc = mock.MagicMock()
    fake_proc.returncode = 1
    fake_proc.stdout = ""
    fake_proc.stderr = "API key invalid"

    with mock.patch("subprocess.run", return_value=fake_proc):
        with pytest.raises(PermanentAgentError) as exc_info:
            inv.invoke({"prompt": "test", "project_id": 1})
    assert "invalid" in str(exc_info.value).lower() or "permanent" in str(exc_info.value).lower()


def test_invoker_classifies_5xx_stderr_as_transient():
    """stderr 含 5xx 关键词 → TransientAgentError（仍可重试）。"""
    from agentboard.agent_runtime.invokers import SubprocessAgentInvoker

    inv = SubprocessAgentInvoker(cmd="dummy", timeout=5)
    fake_proc = mock.MagicMock()
    fake_proc.returncode = 1
    fake_proc.stdout = ""
    fake_proc.stderr = "upstream server error 502"

    with mock.patch("subprocess.run", return_value=fake_proc):
        with pytest.raises(TransientAgentError):
            inv.invoke({"prompt": "test", "project_id": 1})


def test_invoker_classifies_unknown_stderr_as_permanent():
    """stderr 没匹配任何关键词 → 默认 PermanentAgentError（保守不重试）。"""
    from agentboard.agent_runtime.invokers import SubprocessAgentInvoker

    inv = SubprocessAgentInvoker(cmd="dummy", timeout=5)
    fake_proc = mock.MagicMock()
    fake_proc.returncode = 1
    fake_proc.stdout = ""
    fake_proc.stderr = "weird error we don't know about"

    with mock.patch("subprocess.run", return_value=fake_proc):
        # Phase 5 P1 关键：未知 stderr → PermanentAgentError（不再 TransientAgentError）
        with pytest.raises(PermanentAgentError):
            inv.invoke({"prompt": "test", "project_id": 1})


def test_invoker_classifies_empty_stderr_as_permanent():
    """stderr 空 → UNKNOWN → PermanentAgentError。"""
    from agentboard.agent_runtime.invokers import SubprocessAgentInvoker

    inv = SubprocessAgentInvoker(cmd="dummy", timeout=5)
    fake_proc = mock.MagicMock()
    fake_proc.returncode = 1
    fake_proc.stdout = ""
    fake_proc.stderr = ""

    with mock.patch("subprocess.run", return_value=fake_proc):
        with pytest.raises(PermanentAgentError):
            inv.invoke({"prompt": "test", "project_id": 1})


# =============== 5. 真实 subprocess 验证（冒烟）===============

def test_real_subprocess_nonzero_exit_classified_correctly():
    """真实 subprocess 跑一个非零退出 + stderr 关键词的脚本，验证分类。"""
    from agentboard.agent_runtime.invokers import SubprocessAgentInvoker

    # 用一个简单的 Python 脚本作为 "agent"：输出 stderr 含 "invalid" + exit 1
    script = (
        'import sys; '
        'sys.stderr.write("API key invalid"); '
        'sys.exit(1)'
    )
    inv = SubprocessAgentInvoker(
        cmd=f'python -c "{script}"', timeout=5,
    )
    with pytest.raises(PermanentAgentError):
        inv.invoke({"prompt": "test", "project_id": 1})


def test_real_subprocess_zero_exit_returns_decision():
    """真实 subprocess 跑零退出 → 返回 AgentDecision。"""
    import tempfile
    from agentboard.agent_runtime.invokers import SubprocessAgentInvoker

    # 写到临时文件避免 shell 转义问题
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8",
    ) as f:
        f.write(
            'import json, sys\n'
            'sys.stdout.write(json.dumps({"action":"ask","questions":["q?"]}))\n'
        )
        script_path = f.name
    try:
        inv = SubprocessAgentInvoker(
            cmd=f'python "{script_path}"', timeout=5,
        )
        decision = inv.invoke({"prompt": "test", "project_id": 1})
        assert decision.action == "ask"
        assert decision.questions == ["q?"]
    finally:
        os.unlink(script_path)
