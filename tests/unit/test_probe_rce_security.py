"""B-A2 整改回归测试：/api/agents/{id}/probe 任意 cli_command 执行（P0-关键 RCE）。

背景（Epic 145 / Story 291 / task 1192）：
    ``POST /api/agents/{id}/probe`` 原实现 ``subprocess.run(<cli_command> --version)``
    且 dev 默认 ``REQUIRE_AUTH=0`` 匿名可调。攻击链：
      1. ``POST /api/agents/register`` body ``{"agent_id":"x","cli_command":"cmd /c calc.exe"}``
      2. ``POST /api/agents/x/probe`` → 服务端执行
    prod ``REQUIRE_AUTH=1`` 但 B-A1 泄露的 key 同样可达。

B-A2 修复要点（三处防御）：
    1. ``core/service_helpers.validate_cli_command`` — 拒绝 shell 启动器 + 元字符
       （register_agent / update_agent 入口拦截）；
    2. ``api_helpers._probe_cli_sync`` 改 dry-run — 不执行子进程，仅返回命令预览；
    3. ``features/scheduling/router.probe_agent`` 强制鉴权 — dev 模式也要求登录。

本测试覆盖：
    - ``validate_cli_command`` 拒绝 ``cmd /c`` / ``;`` / ``|`` / ``$()`` / 换行等；
    - ``validate_cli_command`` 放行合法 CLI 模板（含 ``{model}`` 占位符）；
    - ``_probe_cli_sync`` dry-run 行为（ok=True + 预览，不执行子进程）；
    - ``_probe_cli_sync`` 元字符拦截（ok=False + blocked 消息）；
    - 源码静态扫描：``_probe_cli_sync`` 函数体不再含 ``subprocess.run`` 调用。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTBOARD_PKG = REPO_ROOT / "agentboard"


# ---------------------------------------------------------------------------
# validate_cli_command
# ---------------------------------------------------------------------------

class TestValidateCliCommand:
    """B-A2: cli_command 安全校验（service 层入口）。"""

    @pytest.mark.parametrize("malicious", [
        "cmd /c calc.exe",
        "cmd /k whoami",
        "cmd.exe /c echo pwned",
        "powershell -Command Get-Process",
        "pwsh -c whoami",
        "bash -c 'rm -rf /'",
        "sh -c whoami",
        "/bin/sh -c id",
        "wscript evil.vbs",
        "cscript evil.wsf",
        "nohup ./backdoor &",
    ])
    def test_rejects_shell_launchers(self, malicious: str) -> None:
        from agentboard.core.service_helpers import validate_cli_command
        with pytest.raises(Exception) as exc_info:
            validate_cli_command(malicious)
        assert "shell 启动器" in str(exc_info.value) or "B-A2" in str(exc_info.value)

    @pytest.mark.parametrize("malicious", [
        "codebuddy; rm -rf /",
        "codebuddy | whoami",
        "codebuddy && echo pwned",
        "codebuddy || echo fail",
        "codebuddy > /etc/passwd",
        "codebuddy < /dev/null",
        "codebuddy `whoami`",
        "codebuddy $(whoami)",
        "codebuddy ${HOME}",
        "codebuddy\necho injected",
        "codebuddy\r\necho injected",
    ])
    def test_rejects_shell_metacharacters(self, malicious: str) -> None:
        from agentboard.core.service_helpers import validate_cli_command
        with pytest.raises(Exception) as exc_info:
            validate_cli_command(malicious)
        assert "元字符" in str(exc_info.value) or "B-A2" in str(exc_info.value)

    @pytest.mark.parametrize("valid", [
        "",
        None,
        "codebuddy",
        "codebuddy --version",
        "codebuddy --model {model}",
        "minimax --model {model} --stream",
        '"C:\\Program Files\\Some CLI\\agent.exe" --version',
        "/usr/local/bin/codebuddy --model hy3",
    ])
    def test_accepts_valid_commands(self, valid: str | None) -> None:
        from agentboard.core.service_helpers import validate_cli_command
        # 不抛异常即通过
        validate_cli_command(valid)

    def test_empty_and_none_are_allowed(self) -> None:
        """空命令/None 不触发校验（业务层允许不配置 cli_command）。"""
        from agentboard.core.service_helpers import validate_cli_command
        validate_cli_command("")
        validate_cli_command(None)

    def test_model_placeholder_is_not_dangerous(self) -> None:
        """{model} 占位符本身不是危险字符，模板阶段放行。"""
        from agentboard.core.service_helpers import validate_cli_command
        validate_cli_command("codebuddy --model {model}")


# ---------------------------------------------------------------------------
# _probe_cli_sync（dry-run 行为）
# ---------------------------------------------------------------------------

class TestProbeCliSyncDryRun:
    """B-A2: _probe_cli_sync 改 dry-run，不执行子进程。"""

    def test_empty_command_returns_false(self) -> None:
        from agentboard.api_helpers import _probe_cli_sync
        ok, msg = _probe_cli_sync("")
        assert ok is False
        assert "未配置" in msg

    def test_none_command_returns_false(self) -> None:
        from agentboard.api_helpers import _probe_cli_sync
        ok, msg = _probe_cli_sync(None)  # type: ignore[arg-type]
        assert ok is False
        assert "未配置" in msg

    def test_valid_command_returns_dry_run_preview(self) -> None:
        from agentboard.api_helpers import _probe_cli_sync
        ok, msg = _probe_cli_sync("codebuddy --model hy3", model="hy3")
        assert ok is True
        assert msg.startswith("dry-run: ")
        assert "codebuddy" in msg
        assert "--version" in msg  # 自动追加的 --version

    def test_model_placeholder_replaced_in_preview(self) -> None:
        from agentboard.api_helpers import _probe_cli_sync
        ok, msg = _probe_cli_sync("minimax --model {model}", model="hy3")
        assert ok is True
        assert "hy3" in msg
        assert "{model}" not in msg  # 占位符已替换

    def test_empty_model_removes_placeholder(self) -> None:
        """model 为空时 {model} 占位符被移除（与 worker._probe_cli 同语义）。"""
        from agentboard.api_helpers import _probe_cli_sync
        ok, msg = _probe_cli_sync("minimax --model {model}", model="")
        assert ok is True
        assert "{model}" not in msg

    @pytest.mark.parametrize("malicious", [
        "cmd /c calc.exe",
        "codebuddy; rm -rf /",
        "codebuddy | whoami",
        "codebuddy && echo pwned",
        "codebuddy $(whoami)",
        "codebuddy`whoami`",
    ])
    def test_blocks_malicious_commands(self, malicious: str) -> None:
        from agentboard.api_helpers import _probe_cli_sync
        ok, msg = _probe_cli_sync(malicious)
        assert ok is False, f"应拦截: {malicious!r}"
        assert "blocked" in msg

    def test_model_injection_is_blocked(self) -> None:
        """model 字段注入 shell 元字符时，替换后再校验应拦截。"""
        from agentboard.api_helpers import _probe_cli_sync
        # cli_command 模板合法，但 model 含 ; 注入
        ok, msg = _probe_cli_sync("codebuddy --model {model}",
                                  model="hy3; rm -rf /")
        assert ok is False
        assert "blocked" in msg

    def test_timeout_param_is_ignored(self) -> None:
        """dry-run 不耗时，timeout 入参保留向后兼容但被忽略。"""
        from agentboard.api_helpers import _probe_cli_sync
        ok1, _ = _probe_cli_sync("codebuddy", timeout=1)
        ok2, _ = _probe_cli_sync("codebuddy", timeout=30)
        assert ok1 == ok2 is True


# ---------------------------------------------------------------------------
# 源码静态扫描（防回归）
# ---------------------------------------------------------------------------

class TestSourceCodeGuards:
    """B-A2: 静态扫描确保 _probe_cli_sync 不再真执行子进程。"""

    @staticmethod
    def _func_source(filename: str, func_name: str) -> str:
        """用 AST 精确提取函数源码段（含 docstring，供后续过滤）。"""
        import ast
        src = (AGENTBOARD_PKG / filename).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                return ast.get_source_segment(src, node) or ""
        return ""

    @staticmethod
    def _func_code_lines(filename: str, func_name: str) -> list[str]:
        """提取函数体的非 docstring/非注释代码行（精确判定实际调用）。"""
        import ast
        src = (AGENTBOARD_PKG / filename).read_text(encoding="utf-8")
        tree = ast.parse(src)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                target = node
                break
        if target is None:
            return []
        lines = src.splitlines()
        # 跳过 def 行 + 装饰器；跳过首条 docstring（Expr(Constant)）
        start = target.lineno  # 1-based; def 行
        body_start = target.body[0].lineno
        # 若首条语句是 docstring，从第二条开始
        if (target.body and isinstance(target.body[0], ast.Expr)
                and isinstance(target.body[0].value, ast.Constant)
                and isinstance(target.body[0].value.value, str)):
            body_start = target.body[1].lineno if len(target.body) > 1 else target.body[0].end_lineno + 1
        end = target.end_lineno or len(lines)
        return lines[body_start - 1: end]

    def test_probe_cli_sync_does_not_call_subprocess_run(self) -> None:
        """``_probe_cli_sync`` 不得调用 ``subprocess.run`` / ``Popen``（防回退到 RCE）。"""
        import ast
        src = (AGENTBOARD_PKG / "api_helpers.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_probe_cli_sync":
                target = node
                break
        assert target is not None, "_probe_cli_sync 函数未找到"
        # 遍历函数体内所有 Call 节点，检查 func 是否为 subprocess.run / subprocess.Popen
        for sub in ast.walk(target):
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    if func.value.id == "subprocess" and func.attr in ("run", "Popen", "call", "check_call", "check_output"):
                        pytest.fail(
                            f"B-A2 回归：_probe_cli_sync 仍调用 subprocess.{func.attr}（RCE 风险）"
                        )

    def test_api_helpers_no_longer_imports_subprocess(self) -> None:
        """B-A2: api_helpers.py 不再需要 subprocess（dead import 已清理）。"""
        src = (AGENTBOARD_PKG / "api_helpers.py").read_text(encoding="utf-8")
        import_lines = [ln for ln in src.splitlines()
                        if ln.strip().startswith(("import subprocess", "from subprocess"))]
        assert not import_lines, (
            f"B-A2 回归：api_helpers.py 仍有 subprocess import: {import_lines}"
        )

    def test_probe_endpoint_requires_auth_unconditionally(self) -> None:
        """B-A2: probe 端点代码行不得用 _auth_is_required() 软判定，必须 if uid is None: 401。"""
        # 只检查实际代码行（排除 docstring/注释），避免误匹配说明文字
        code_lines = self._func_code_lines(
            "features/scheduling/router.py", "probe_agent"
        )
        assert code_lines, "probe_agent 函数未找到"
        code_text = "\n".join(code_lines)
        # 代码行里不应出现 _auth_is_required 调用（注释里的不算）
        for ln in code_lines:
            # 剥离行内注释（# 及之后），只检查代码部分
            code_part = ln.split("#", 1)[0] if "#" in ln else ln
            assert "_auth_is_required" not in code_part, (
                f"B-A2 回归：probe 端点代码仍用 _auth_is_required() 软判定: {ln!r}"
            )
        assert "if uid is None:" in code_text, (
            "B-A2 回归：probe 端点未强制鉴权（缺 if uid is None: raise 401）"
        )

    def test_register_agent_calls_validate_cli_command(self) -> None:
        """B-A2: register_agent 必须调用 validate_cli_command（入口拦截）。"""
        import ast
        src = (AGENTBOARD_PKG / "features" / "scheduling" / "service.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "register_agent":
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                            and sub.func.id == "validate_cli_command"):
                        return
                pytest.fail("B-A2 回归：register_agent 未调用 validate_cli_command")
        pytest.fail("register_agent 函数未找到")

    def test_update_agent_calls_validate_cli_command(self) -> None:
        """B-A2: update_agent 必须调用 validate_cli_command（入口拦截）。"""
        import ast
        src = (AGENTBOARD_PKG / "service.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "update_agent":
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                            and sub.func.id == "validate_cli_command"):
                        return
                pytest.fail("B-A2 回归：update_agent 未调用 validate_cli_command")
        pytest.fail("update_agent 函数未找到")
