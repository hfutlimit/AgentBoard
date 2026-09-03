"""processor_portal 凭据安全回归测试（P0 整改 B-A1 / Story 291 / Epic 145）。

背景：
    ``agentboard/processor_portal.py`` 历史版本在源码中硬编码生产 API key
    （``DEFAULT_TOKEN = "abk_Lv493r01Pi4ue5gZo7RAS7vyciEmqzeOVLR7LNPmAHg"``），
    已提交进 git 历史。任何 clone 仓库者持有该 key 即可调任意 API。

B-A1 修复要点：
    1. ``DEFAULT_API_URL`` / ``DEFAULT_TOKEN`` 改为空字符串（无硬编码回退）；
    2. ``create_app()`` 在缺凭据时抛 ``SystemExit``（fail-fast，非零退出码）；
    3. 模块级 ``app`` 仅在环境变量齐全时创建，import 不会崩溃（测试兼容）。

本测试覆盖：
    - ``create_app()`` 无凭据时抛 ``SystemExit`` 且错误信息含缺失变量名；
    - ``create_app("http://x", "tok")`` 显式传参时正常返回 FastAPI；
    - ``agentboard/`` 源码包内不再出现硬编码 ``abk_`` 前缀 token（防回归）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

# 仓库根：tests/unit/xx.py -> 上两级
REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTBOARD_PKG = REPO_ROOT / "src" / "backend-fastapi" / "agentboard"


class TestCreateAppFailFast:
    """B-A1: create_app() 在缺凭据时 fail-fast。"""

    def test_create_app_raises_systemexit_without_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无 env 变量、无显式参数 → 抛 SystemExit。"""
        # 清除可能存在的 env 变量（本机开发环境可能已设置）
        monkeypatch.delenv("AGENTBOARD_API_URL", raising=False)
        monkeypatch.delenv("AGENTBOARD_WORKER_TOKEN", raising=False)

        from agentboard.processor_portal import create_app

        with pytest.raises(SystemExit) as exc_info:
            create_app()

        # 错误信息必须明确指出缺失的变量
        msg = str(exc_info.value)
        assert "AGENTBOARD_API_URL" in msg, f"错误信息未提及 AGENTBOARD_API_URL: {msg!r}"
        assert "AGENTBOARD_WORKER_TOKEN" in msg, f"错误信息未提及 AGENTBOARD_WORKER_TOKEN: {msg!r}"
        assert "B-A1" in msg or "硬编码" in msg or "凭据" in msg, f"错误信息缺少整改指引: {msg!r}"

    def test_create_app_raises_with_only_api_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """只传 api_url、缺 token → 抛 SystemExit 且只提 AGENTBOARD_WORKER_TOKEN。"""
        monkeypatch.delenv("AGENTBOARD_API_URL", raising=False)
        monkeypatch.delenv("AGENTBOARD_WORKER_TOKEN", raising=False)

        from agentboard.processor_portal import create_app

        with pytest.raises(SystemExit) as exc_info:
            create_app(api_url="http://example.com", token=None)

        msg = str(exc_info.value)
        assert "AGENTBOARD_WORKER_TOKEN" in msg
        # 不应再抱怨 AGENTBOARD_API_URL（已提供）
        assert "AGENTBOARD_API_URL" not in msg

    def test_create_app_raises_with_only_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """只传 token、缺 api_url → 抛 SystemExit 且只提 AGENTBOARD_API_URL。"""
        monkeypatch.delenv("AGENTBOARD_API_URL", raising=False)
        monkeypatch.delenv("AGENTBOARD_WORKER_TOKEN", raising=False)

        from agentboard.processor_portal import create_app

        with pytest.raises(SystemExit) as exc_info:
            create_app(api_url=None, token="abk_test_token")

        msg = str(exc_info.value)
        assert "AGENTBOARD_API_URL" in msg
        assert "AGENTBOARD_WORKER_TOKEN" not in msg

    def test_create_app_works_with_explicit_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """显式传 api_url + token → 正常返回 FastAPI 实例。"""
        # 即使 env 变量缺失，显式参数也应工作
        monkeypatch.delenv("AGENTBOARD_API_URL", raising=False)
        monkeypatch.delenv("AGENTBOARD_WORKER_TOKEN", raising=False)

        from agentboard.processor_portal import create_app

        app = create_app(api_url="http://example.com", token="abk_test_token")
        assert isinstance(app, FastAPI)
        # 健康检查端点应可用
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            r = client.get("/api/health")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ok"
            assert data["api"] == "http://example.com"

    def test_create_app_works_with_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过 env 变量提供凭据 → 正常返回 FastAPI 实例。"""
        monkeypatch.setenv("AGENTBOARD_API_URL", "http://env.example.com")
        monkeypatch.setenv("AGENTBOARD_WORKER_TOKEN", "abk_env_token")

        from agentboard.processor_portal import create_app

        app = create_app()
        assert isinstance(app, FastAPI)

    def test_create_app_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """凭据含空白 → strip 后正常工作（防误粘贴换行）。"""
        monkeypatch.delenv("AGENTBOARD_API_URL", raising=False)
        monkeypatch.delenv("AGENTBOARD_WORKER_TOKEN", raising=False)

        from agentboard.processor_portal import create_app

        app = create_app(api_url="  http://example.com  ", token="  abk_test_token  ")
        assert isinstance(app, FastAPI)


class TestNoHardcodedKeyInSource:
    """B-A1 关联：agentboard/ 源码包内不得出现硬编码 abk_ token。"""

    # 已知泄露的 key 前缀（B-A1 修复目标）
    LEAKED_KEY_PREFIX = "abk_Lv493r01Pi4ue5gZo7RAS7vyciEmqzeOVLR7LNPmAHg"

    def test_no_leaked_key_in_agentboard_package(self) -> None:
        """agentboard/ 下所有 .py 文件不得包含已知泄露的 key。"""
        offenders: list[str] = []
        for py_file in AGENTBOARD_PKG.rglob("*.py"):
            try:
                text = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if self.LEAKED_KEY_PREFIX in text:
                offenders.append(str(py_file.relative_to(REPO_ROOT)))
        assert not offenders, (
            "agentboard/ 源码仍含已泄露的生产 key（B-A1 未完成）：\n"
            + "\n".join(offenders)
        )

    @pytest.mark.parametrize(
        "pattern,description",
        [
            (r'DEFAULT_TOKEN\s*=\s*"abk_', "DEFAULT_TOKEN 不得硬编码 abk_ 前缀 token"),
            (r'DEFAULT_API_URL\s*=\s*"http://\d+\.\d+\.\d+\.\d+"', "DEFAULT_API_URL 不得硬编码 IP 地址"),
        ],
    )
    def test_no_hardcoded_defaults_in_processor_portal(self, pattern: str, description: str) -> None:
        """processor_portal.py 的 DEFAULT_* 常量不得回退到硬编码生产值。"""
        import re

        wp_file = AGENTBOARD_PKG / "processor_portal.py"
        text = wp_file.read_text(encoding="utf-8")
        matches = re.findall(pattern, text)
        assert not matches, (
            f"{description}（B-A1 回归）：发现 {matches!r}"
        )

    def test_default_constants_are_empty(self) -> None:
        """DEFAULT_API_URL / DEFAULT_TOKEN 必须为空字符串（无回退值）。"""
        from agentboard.processor_portal import DEFAULT_API_URL, DEFAULT_TOKEN

        assert DEFAULT_API_URL == "", (
            f"DEFAULT_API_URL 必须为空字符串（B-A1），实际: {DEFAULT_API_URL!r}"
        )
        assert DEFAULT_TOKEN == "", (
            f"DEFAULT_TOKEN 必须为空字符串（B-A1），实际: {DEFAULT_TOKEN!r}"
        )


class TestModuleLevelAppSafeImport:
    """B-A1: 模块级 app 在缺凭据时为 None，import 不崩溃。"""

    def test_module_importable_without_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无 env 变量时 import processor_portal 不应抛异常。"""
        monkeypatch.delenv("AGENTBOARD_API_URL", raising=False)
        monkeypatch.delenv("AGENTBOARD_WORKER_TOKEN", raising=False)

        # 清除可能已加载的模块（强制重新 import）
        mods_to_clear = [k for k in sys.modules if k.startswith("agentboard.processor_portal")]
        for k in mods_to_clear:
            del sys.modules[k]

        # import 应成功（不抛 SystemExit）
        import agentboard.processor_portal as wp

        # 模块级 app 应为 None（缺凭据）
        assert wp.app is None, (
            "缺凭据时模块级 app 应为 None（B-A1 fail-fast 留给 main()）"
        )

    def test_module_app_created_with_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """env 变量齐全时模块级 app 应为 FastAPI 实例。"""
        monkeypatch.setenv("AGENTBOARD_API_URL", "http://env.example.com")
        monkeypatch.setenv("AGENTBOARD_WORKER_TOKEN", "abk_env_token")

        # 清除可能已加载的模块
        mods_to_clear = [k for k in sys.modules if k.startswith("agentboard.processor_portal")]
        for k in mods_to_clear:
            del sys.modules[k]

        import agentboard.processor_portal as wp

        assert isinstance(wp.app, FastAPI), (
            "env 变量齐全时模块级 app 应为 FastAPI 实例"
        )
