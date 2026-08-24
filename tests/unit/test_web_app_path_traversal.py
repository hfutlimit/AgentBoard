"""web_app.py SPA 路径穿越回归测试（P0 整改 B-A4 / Story 291 / Epic 145）。

背景：
    ``agentboard/web_app.py`` 历史版本在 ``angular_asset_or_route`` 中直接
    ``STATIC_DIR / path`` 后判断 ``is_file()`` 并返回 ``FileResponse``，
    没有 resolve + 归属校验。由于 FastAPI ``{path:path}`` 允许 ``..`` 段，
    攻击者可构造::

        GET /..%2F..%2F.env            → 读项目根 .env（含密钥）
        GET /..%2F..%2Fagentboard%2Fworker_portal.py → 读源码

    B-A4 修复：先 ``resolve()`` 再 ``is_relative_to(STATIC_DIR_RESOLVED)``，
    任何逃逸出静态根的路径统一 404。

本测试覆盖：
    - 编码 ``%2E%2E`` 穿越变体；
    - 不编码 ``..`` 直接穿越；
    - 混合斜杠（``\\`` / ``/``）；
    - 绝对路径注入（``/etc/passwd`` 风格）；
    - 多层 ``..`` 嵌套；
    - 正常静态文件访问仍 200（回归保护）；
    - 不存在的深链接回退到 index.html（SPA 行为不破坏）。

实现说明：
    Starlette ``TestClient`` 会在路由匹配前对 URL 做 percent-decode，
    所以 ``%2E%2E`` 与 ``..`` 在 ``angular_asset_or_route(path=...)`` 里
    都会以字面 ``..`` 进入函数体 —— 正是漏洞触发路径。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_web_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """构造一个隔离的 STATIC_DIR，并在其中放典型 Angular 资源文件。

    在 STATIC_DIR 之外（tmp_path 根）放 ``.env`` 与 ``worker_portal.py``
    模拟项目根的敏感文件 —— 若路径穿越未收口，这些文件会被读出。
    """
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    # 典型 Angular 资源
    (static_dir / "index.html").write_text(
        "<html><head></head><body>SPA_ROOT</body></html>", encoding="utf-8"
    )
    (static_dir / "main-ABC123.js").write_text(
        "console.log('main');", encoding="utf-8"
    )
    (static_dir / "styles-DEF456.css").write_text(
        "body{color:#000}", encoding="utf-8"
    )
    (static_dir / "favicon.svg").write_text(
        "<svg></svg>", encoding="utf-8"
    )
    # STATIC_DIR 外的敏感文件（模拟项目根 .env / 源码）
    (tmp_path / ".env").write_text(
        "MINIMAX_API_KEY=sk-LEAKED\nAGENTBOARD_WORKER_TOKEN=abk_LEAKED\n",
        encoding="utf-8",
    )
    fake_pkg = tmp_path / "agentboard"
    fake_pkg.mkdir()
    (fake_pkg / "worker_portal.py").write_text(
        'DEFAULT_TOKEN = "abk_LEAKED_FROM_TRAVERSAL"\n', encoding="utf-8"
    )

    # 让 web_app 模块用这个临时 STATIC_DIR
    monkeypatch.setenv("AGENTBOARD_WEB_STATIC_DIR", str(static_dir))

    # 清除已加载的 web_app 模块（强制用新 env 重新 import）
    mods_to_clear = [k for k in sys.modules if k == "agentboard.web_app"]
    for k in mods_to_clear:
        del sys.modules[k]

    import agentboard.web_app as web_app

    importlib.reload(web_app)
    yield web_app


class TestPathTraversalBlocked:
    """B-A4: 任何逃逸出 STATIC_DIR 的路径必须返回 404。"""

    @pytest.mark.parametrize(
        "traversal_url",
        [
            # 编码 %2E%2E（最常见攻击向量，绕过肉眼审查；%2F 解码后进入 {path:path}）
            "/..%2F..%2F.env",
            # 多层嵌套（编码）
            "/..%2F..%2F..%2F..%2F..%2Fetc%2Fpasswd",
            # 混合：编码 + 不编码
            "/..%2F../agentboard/worker_portal.py",
            # 反斜杠变体（Windows 路径分隔符，%5C 解码后进入 {path:path}）
            "/..%5C..%5C.env",
        ],
    )
    def test_traversal_returns_404(
        self, isolated_web_app, traversal_url: str
    ) -> None:
        """所有穿越变体必须 404，不得返回敏感文件内容。

        注意：这里只覆盖 ``%2F`` / ``%5C`` 编码的穿越向量 —— 它们在
        Starlette 路由匹配前不会被规范化（保留为字面 ``..`` 段进入
        ``{path:path}``），因此能真正触达 ``angular_asset_or_route``
        的 resolve 校验。不编码的 ``/../../x`` 会被 Starlette 提前
        规范化，见 ``test_starlette_normalized_paths_dont_leak``。
        """
        with TestClient(isolated_web_app.app) as client:
            r = client.get(traversal_url, follow_redirects=False)
        assert r.status_code == 404, (
            f"B-A4 回归：{traversal_url!r} 应返回 404，"
            f"实际 {r.status_code}（穿越未收口）"
        )
        # 不得泄露敏感内容
        body = r.text
        assert "abk_LEAKED" not in body, (
            f"B-A4 严重回归：{traversal_url!r} 响应体含泄露的 token"
        )
        assert "sk-LEAKED" not in body, (
            f"B-A4 严重回归：{traversal_url!r} 响应体含泄露的 API key"
        )

    @pytest.mark.parametrize(
        "normalized_url",
        [
            # 不编码 ``..``：Starlette 路由前规范化为 ``/.env``，path=".env"
            # → STATIC_DIR/.env 不存在 → 回退 index.html。安全但返回 200。
            "/../../.env",
            # 绝对路径：Starlette 剥前导 /，path="etc/passwd"
            # → STATIC_DIR/etc/passwd 不存在 → 回退 index.html。
            "/etc/passwd",
        ],
    )
    def test_starlette_normalized_paths_dont_leak(
        self, isolated_web_app, normalized_url: str
    ) -> None:
        """Starlette 路由前规范化的路径不会穿越，但可能命中 SPA 回退。

        这些路径在到达 ``angular_asset_or_route`` 前已被 Starlette
        规范化（``..`` 段解析、前导 ``/`` 剥离），``{path:path}`` 收到的
        是相对段（如 ``.env`` / ``etc/passwd``），``resolve()`` 后仍归属
        STATIC_DIR，命中不存在的文件 → 回退 index.html（200）。

        本测试断言：即便返回 200，响应体也不得包含敏感文件内容。
        """
        with TestClient(isolated_web_app.app) as client:
            r = client.get(normalized_url, follow_redirects=False)
        # 200（SPA 回退）或 404 都可接受，关键是 body 不泄露
        assert r.status_code in (200, 404), (
            f"{normalized_url!r} 意外状态码 {r.status_code}"
        )
        body = r.text
        assert "MINIMAX_API_KEY" not in body, (
            f"{normalized_url!r} 响应体含 .env 密钥（穿越成功）"
        )
        assert "abk_LEAKED" not in body, (
            f"{normalized_url!r} 响应体含 worker_portal token"
        )
        assert "sk-LEAKED" not in body, (
            f"{normalized_url!r} 响应体含泄露的 API key"
        )

    def test_root_env_not_readable(self, isolated_web_app) -> None:
        """``GET /..%2F.env`` 必须读不到项目根 .env（即便 STATIC_DIR 就在项目根下）。"""
        with TestClient(isolated_web_app.app) as client:
            r = client.get("/..%2F.env", follow_redirects=False)
        assert r.status_code == 404
        assert "MINIMAX_API_KEY" not in r.text

    def test_source_code_not_readable(self, isolated_web_app) -> None:
        """``GET /..%2Fagentboard%2Fworker_portal.py`` 必须读不到源码。"""
        with TestClient(isolated_web_app.app) as client:
            r = client.get(
                "/..%2Fagentboard%2Fworker_portal.py", follow_redirects=False
            )
        assert r.status_code == 404
        assert "DEFAULT_TOKEN" not in r.text


class TestNormalStaticAccess:
    """B-A4 回归保护：正常静态资源访问与 SPA 回退不得被破坏。"""

    def test_index_html_served_at_root(self, isolated_web_app) -> None:
        """``GET /`` 必须返回 index.html（_fixed_index 路径）。"""
        with TestClient(isolated_web_app.app) as client:
            r = client.get("/")
        assert r.status_code == 200
        assert "SPA_ROOT" in r.text

    def test_static_asset_served(self, isolated_web_app) -> None:
        """``GET /main-ABC123.js`` 必须返回真实 JS 文件（STATIC_DIR 内）。"""
        with TestClient(isolated_web_app.app) as client:
            r = client.get("/main-ABC123.js")
        assert r.status_code == 200
        assert "console.log('main')" in r.text

    def test_static_mounted_assets_served(self, isolated_web_app) -> None:
        """``GET /static/favicon.svg``（StaticFiles 挂载点）仍正常。"""
        with TestClient(isolated_web_app.app) as client:
            r = client.get("/static/favicon.svg")
        assert r.status_code == 200

    def test_unknown_deep_link_falls_back_to_index(self, isolated_web_app) -> None:
        """``GET /projects/123/tasks``（深链接，STATIC_DIR 内不存在）回退到 index.html。"""
        with TestClient(isolated_web_app.app) as client:
            r = client.get("/projects/123/tasks")
        # 路径在 STATIC_DIR 内（resolve 后仍归属），但不是文件 → 回退 index.html
        assert r.status_code == 200
        assert "SPA_ROOT" in r.text

    def test_nested_subpath_in_static_dir(self, isolated_web_app) -> None:
        """STATIC_DIR 子目录内的文件应可访问（无 .. 段）。"""
        sub = isolated_web_app.STATIC_DIR / "assets"
        sub.mkdir(exist_ok=True)
        (sub / "logo.svg").write_text("<svg>logo</svg>", encoding="utf-8")
        with TestClient(isolated_web_app.app) as client:
            r = client.get("/assets/logo.svg")
        assert r.status_code == 200
        assert "logo" in r.text


class TestStaticDirResolvedInvariant:
    """B-A4 实现契约：STATIC_DIR_RESOLVED 必须存在并被使用。"""

    def test_static_dir_resolved_exists(self, isolated_web_app) -> None:
        """模块必须导出 STATIC_DIR_RESOLVED（缓存 resolve 锚点）。"""
        assert hasattr(isolated_web_app, "STATIC_DIR_RESOLVED"), (
            "web_app 必须导出 STATIC_DIR_RESOLVED（B-A4 路径穿越校验锚点）"
        )
        assert isinstance(isolated_web_app.STATIC_DIR_RESOLVED, Path)
        assert isolated_web_app.STATIC_DIR_RESOLVED.is_absolute()

    def test_source_uses_is_relative_to(self) -> None:
        """源码必须用 ``is_relative_to`` 做归属校验（静态扫描防回归）。"""
        src = (REPO_ROOT / "src" / "backend-fastapi" / "agentboard" / "web_app.py").read_text(encoding="utf-8")
        assert "is_relative_to(STATIC_DIR_RESOLVED)" in src, (
            "web_app.py 必须使用 is_relative_to(STATIC_DIR_RESOLVED) 收口路径"
            "（B-A4 修复契约）"
        )
        # 不得保留旧的未校验 is_file 分支
        assert "static_candidate = STATIC_DIR / path" not in src, (
            "web_app.py 不得保留旧的未校验 static_candidate 分支（B-A4 回归）"
        )
