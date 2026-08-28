"""validate_runtime_security() 安全检查回归测试（P0 整改 B-A5 / Story 291 / Epic 145）。

背景：
    ``agentboard/core/infrastructure/auth.py`` 的 ``validate_runtime_security()``
    在 ``AGENTBOARD_ENV=production`` 时 fail-fast，拒绝弱 SECRET / REQUIRE_AUTH=0 /
    CORS=*；dev/staging 模式仅记录 WARNING 日志（向后兼容，不阻断本地开发）。

    B-A5 强化点：
    1. dev/staging 模式检测到不安全默认值时记录 WARNING（可见性提升，非阻断）；
    2. production 模式新增 ``ALLOW_REGISTRATION=1`` 的 WARNING（维护窗口非阻断，
       避免破坏 README 文档化的临时注册流程）；
    3. 提取 ``_has_wildcard_cors()`` 辅助函数消除重复。

本测试覆盖：
    - dev 模式（默认）不 raise，无论默认值多不安全；
    - dev 模式检测到不安全默认值时记录 WARNING（logger.warning spy 断言）；
    - production + 弱 SECRET → raise；
    - production + REQUIRE_AUTH=0 → raise；
    - production + CORS=* → raise；
    - production + 全安全值 → 不 raise；
    - production + ALLOW_REGISTRATION=1 → 不 raise（仅 WARNING）。

注：日志断言用 monkeypatch spy 直接拦截 ``logger.warning``，不依赖 caplog 的
propagation（全量套件中其他测试可能修改 logging 配置导致 caplog 捕获失败）。
"""
from __future__ import annotations

from typing import Callable, List

import pytest

from agentboard.core.infrastructure.auth import (
    _has_wildcard_cors,
    validate_runtime_security,
)
import agentboard.core.infrastructure.auth as _auth_mod

# 强 SECRET（>= 32 字节，非默认占位符）
_STRONG_SECRET = b"x" * 64


def _spy_logger_warning(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """用 monkeypatch 替换 auth 模块的 ``logger.warning`` 为收集器，返回 messages 列表。

    比 caplog 更可靠：不依赖 logging propagation / handler 配置，
    全量套件中其他测试修改 logging 也不受影响。
    """
    messages: List[str] = []

    def _capture(msg: str, *args, **kwargs) -> None:
        messages.append(msg % args if args else msg)

    monkeypatch.setattr(_auth_mod.logger, "warning", _capture)
    return messages


def _set_prod_secure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置全安全的 production 环境变量。

    MCP transport security is validated by ``validate_mcp_runtime_security``
    in the independent MCP process, not by this REST API validator.
    """
    monkeypatch.setenv("AGENTBOARD_ENV", "production")
    monkeypatch.setenv("AGENTBOARD_REQUIRE_AUTH", "1")
    monkeypatch.setenv("AGENTBOARD_ALLOW_REGISTRATION", "0")
    monkeypatch.setenv("AGENTBOARD_CORS_ORIGINS", "https://agentboard.example.com")
    monkeypatch.setattr(_auth_mod, "_SECRET", _STRONG_SECRET)


class TestDevModeNoRaise:
    """dev / staging 模式：不 raise（向后兼容）。"""

    def test_dev_default_env_no_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AGENTBOARD_ENV 未设（默认 development）+ 全部不安全默认值 → 不 raise。"""
        monkeypatch.delenv("AGENTBOARD_ENV", raising=False)
        monkeypatch.delenv("AGENTBOARD_REQUIRE_AUTH", raising=False)
        monkeypatch.delenv("AGENTBOARD_ALLOW_REGISTRATION", raising=False)
        monkeypatch.delenv("AGENTBOARD_CORS_ORIGINS", raising=False)
        # 不 raise 即通过
        validate_runtime_security()

    def test_staging_env_no_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AGENTBOARD_ENV=staging + 不安全值 → 不 raise（仅 WARNING）。"""
        monkeypatch.setenv("AGENTBOARD_ENV", "staging")
        monkeypatch.setenv("AGENTBOARD_REQUIRE_AUTH", "0")
        monkeypatch.setenv("AGENTBOARD_ALLOW_REGISTRATION", "1")
        monkeypatch.setenv("AGENTBOARD_CORS_ORIGINS", "*")
        validate_runtime_security()

    def test_dev_logs_warning_for_insecure_defaults(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dev 模式 + 不安全默认值 → logger.warning 被调用且消息含具体变量名。"""
        monkeypatch.setenv("AGENTBOARD_ENV", "development")
        monkeypatch.delenv("AGENTBOARD_REQUIRE_AUTH", raising=False)
        monkeypatch.delenv("AGENTBOARD_ALLOW_REGISTRATION", raising=False)
        monkeypatch.delenv("AGENTBOARD_CORS_ORIGINS", raising=False)

        messages = _spy_logger_warning(monkeypatch)
        validate_runtime_security()

        joined = " ".join(messages)
        assert "insecure defaults" in joined, f"WARNING 未提及 insecure defaults: {joined!r}"
        assert "AGENTBOARD_REQUIRE_AUTH" in joined
        assert "AGENTBOARD_ALLOW_REGISTRATION" in joined
        assert "AGENTBOARD_CORS_ORIGINS" in joined

    def test_dev_no_warning_when_all_secure(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dev 模式 + 全安全值（含强 SECRET）→ logger.warning 不被调用（避免噪音）。"""
        monkeypatch.setenv("AGENTBOARD_ENV", "development")
        monkeypatch.setenv("AGENTBOARD_REQUIRE_AUTH", "1")
        monkeypatch.setenv("AGENTBOARD_ALLOW_REGISTRATION", "0")
        monkeypatch.setenv("AGENTBOARD_CORS_ORIGINS", "https://localhost:28080")
        monkeypatch.setenv("AGENTBOARD_MCP_REQUIRE_AUTH", "1")
        monkeypatch.setattr(_auth_mod, "_SECRET", _STRONG_SECRET)

        messages = _spy_logger_warning(monkeypatch)
        validate_runtime_security()

        assert not messages, (
            f"全安全值时不应记录 WARNING: {messages}"
        )


class TestProductionFailFast:
    """production 模式：fail-fast 检查。"""

    def test_prod_weak_secret_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """production + 默认 dev SECRET → raise RuntimeError。"""
        _set_prod_secure_env(monkeypatch)
        monkeypatch.setattr(_auth_mod, "_SECRET", b"dev-insecure-secret-change-me")
        with pytest.raises(RuntimeError, match="AGENTBOARD_SECRET"):
            validate_runtime_security()

    def test_prod_short_secret_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """production + < 32 字节 SECRET → raise。"""
        _set_prod_secure_env(monkeypatch)
        monkeypatch.setattr(_auth_mod, "_SECRET", b"short")
        with pytest.raises(RuntimeError, match="AGENTBOARD_SECRET"):
            validate_runtime_security()

    def test_prod_require_auth_zero_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """production + REQUIRE_AUTH=0 → raise。"""
        _set_prod_secure_env(monkeypatch)
        monkeypatch.setenv("AGENTBOARD_REQUIRE_AUTH", "0")
        with pytest.raises(RuntimeError, match="REQUIRE_AUTH"):
            validate_runtime_security()

    def test_prod_wildcard_cors_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """production + CORS=* → raise。"""
        _set_prod_secure_env(monkeypatch)
        monkeypatch.setenv("AGENTBOARD_CORS_ORIGINS", "*")
        with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
            validate_runtime_security()

    def test_prod_all_secure_no_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """production + 全安全值 → 不 raise。"""
        _set_prod_secure_env(monkeypatch)
        validate_runtime_security()

    def test_prod_allow_registration_one_no_raise(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """production + ALLOW_REGISTRATION=1 → 不 raise，仅 WARNING（维护窗口）。"""
        _set_prod_secure_env(monkeypatch)
        monkeypatch.setattr(
            "agentboard.core.infrastructure.auth._SECRET", _STRONG_SECRET,
        )
        monkeypatch.setenv("AGENTBOARD_ALLOW_REGISTRATION", "1")

        messages = _spy_logger_warning(monkeypatch)
        validate_runtime_security()

        # 不 raise 即通过；且必须记录 WARNING 提醒事后恢复
        joined = " ".join(messages)
        assert "ALLOW_REGISTRATION" in joined, (
            f"production ALLOW_REGISTRATION=1 未记录 WARNING: {joined!r}"
        )
        assert "maintenance" in joined.lower() or "恢复" in joined or "back to 0" in joined


class TestHasWildcardCors:
    """``_has_wildcard_cors()`` 辅助函数单测。"""

    def test_wildcard_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTBOARD_CORS_ORIGINS", "*")
        assert _has_wildcard_cors() is True

    def test_wildcard_in_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """逗号分隔列表中含 * → True（防御混合配置）。"""
        monkeypatch.setenv("AGENTBOARD_CORS_ORIGINS", "https://a.com, *, https://b.com")
        assert _has_wildcard_cors() is True

    def test_explicit_origins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTBOARD_CORS_ORIGINS", "https://a.com,https://b.com")
        assert _has_wildcard_cors() is False

    def test_default_wildcard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """env 未设 → 默认 * → True。"""
        monkeypatch.delenv("AGENTBOARD_CORS_ORIGINS", raising=False)
        assert _has_wildcard_cors() is True

    def test_whitespace_tolerant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """带空格的 * → True。"""
        monkeypatch.setenv("AGENTBOARD_CORS_ORIGINS", " * ")
        assert _has_wildcard_cors() is True
