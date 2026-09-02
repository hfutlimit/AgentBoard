"""Worker 本机配置台（Epic 122 · Story 243 S5）。

本机轻量 FastAPI 服务（默认 127.0.0.1:18240，**免登录**——仅本机绑定）：

- ``GET  /api/agents``           读取当前 Worker 的 AgentInstance；
- ``POST /api/agents``           挂载/更新当前 Worker 的 AgentInstance；
- ``PUT  /api/agents/{id}``      更新当前 Worker 的 AgentInstance；
- ``GET  /api/cli-presets``      CLI 预设模板 + 模型下拉数据源；
- ``GET  /api/projects``         服务器项目列表（供项目映射选择）；
- ``GET/PUT /api/mappings``      本机「服务器项目 → 本地目录」映射（JSON 文件）；
- ``GET  /api/executions``       服务器任务执行记录（支持按 Agent 过滤）；
- ``GET  /api/records``          本机 Worker 原始运行日志（worker-mq.log 摘要）。

设计要点
--------
- 服务器交互复用 ``AGENTBOARD_API_URL`` / ``AGENTBOARD_WORKER_TOKEN``（与 worker 同凭据）；
- 项目映射存本机 JSON（``AGENTBOARD_LOCAL_MAPPINGS``，默认 AgentBoard 仓库 tmp 下），
  Worker 执行任务时按 ``project_id`` 解析本地 cwd（Story 243 验收 4）；
- Agent 配置按 ``AGENTBOARD_WORKER_ID`` 隔离，不读取或修改服务器全局 Agent 池；
- CLI/模型下拉为内置预设（codex / codebuddy / minimax-cli），保存时渲染命令模板；
- 免登录约束：uvicorn 仅绑定 127.0.0.1；生产使用须经反向代理 + 访问控制。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .agent_registry_cache import ephemeral_agents_enabled  # agent-ephemeral-2026-09 P4
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

log = logging.getLogger("agentboard.worker_portal")

# ---------- 环境配置 ----------
# B-A1 整改（Epic 145 / Story 291）：禁止源码硬编码生产凭据。
# 必须由环境变量 AGENTBOARD_API_URL / AGENTBOARD_WORKER_TOKEN 注入；
# 缺失时 create_app() fail-fast（抛 SystemExit），不再回退到默认值。
DEFAULT_API_URL = ""
DEFAULT_TOKEN = ""
DEFAULT_PORT = 18240
DEFAULT_MAPPINGS_FILE = "tmp/project-mappings.json"


def _discover_codebuddy_paths() -> tuple[str, str]:
    """Resolve the locally installed WorkBuddy Node runtime and CodeBuddy entrypoint.

    The desktop application may be installed on any drive.  Keeping the old
    ``E:/Program Files`` path in the preset made healthy CodeBuddy installs look
    offline after the application was moved or reinstalled.
    """
    configured_node = os.getenv("AGENTBOARD_CODEBUDDY_NODE", "").strip()
    configured_cli = os.getenv("AGENTBOARD_CODEBUDDY_CLI", "").strip()

    node_candidates: list[Path] = []
    versions_root = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    if versions_root.is_dir():
        node_candidates.extend(
            sorted(versions_root.glob("*/node.exe"), reverse=True)
        )
    node_candidates.extend(Path(p) for p in filter(None, (shutil.which("node"),)))

    cli_candidates: list[Path] = []
    if os.name == "nt":
        try:
            import winreg

            uninstall_roots = (
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            )
            for hive, root in uninstall_roots:
                try:
                    with winreg.OpenKey(hive, root) as parent:
                        for index in range(winreg.QueryInfoKey(parent)[0]):
                            with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as item:
                                try:
                                    name = str(winreg.QueryValueEx(item, "DisplayName")[0])
                                except OSError:
                                    continue
                                if name.lower() != "workbuddy":
                                    continue
                                try:
                                    icon = str(winreg.QueryValueEx(item, "DisplayIcon")[0])
                                except OSError:
                                    continue
                                install_dir = Path(icon.split(",", 1)[0].strip('"')).parent
                                cli_candidates.append(
                                    install_dir / "resources" / "app.asar.unpacked" / "cli" / "bin" / "codebuddy"
                                )
                except OSError:
                    continue
        except ImportError:  # pragma: no cover - non-Windows runtime
            pass
    cli_candidates.extend(
        Path(drive) / "Program Files" / "WorkBuddy" / "resources" /
        "app.asar.unpacked" / "cli" / "bin" / "codebuddy"
        for drive in ("C:/", "D:/", "E:/")
    )

    node = configured_node or next(
        (str(path) for path in node_candidates if path.is_file()), "node"
    )
    cli = configured_cli or next(
        (str(path) for path in cli_candidates if path.is_file()), "codebuddy"
    )
    return node, cli


_CODEBUDDY_NODE, _CODEBUDDY_CLI = _discover_codebuddy_paths()

# CLI 预设：key → {label, template(含 {model} 占位), models: [...]}
CLI_PRESETS: dict[str, dict[str, Any]] = {
    "codex": {
        "label": "OpenAI Codex CLI",
        "models": ["gpt-5.6-sol"],
        "template": '"{command}" exec --model "{model}"{full_access} --color never',
        "full_access_arg": " --dangerously-bypass-approvals-and-sandbox",
        "supports_full_access": True,
        "default_full_access": True,
    },
    "codebuddy": {
        "label": "WorkBuddy CodeBuddy CLI",
        "node": _CODEBUDDY_NODE,
        "cli": _CODEBUDDY_CLI,
        "mcp_config": None,  # 由页面/默认 mcp-prod.json 填充
        "models": [
            "hy3", "hy3-preview-agent", "hy4-preview",
            "deepseek-v4-flash", "deepseek-v4-pro",
            "kimi-k3-2", "kimi-k2-7", "kimi-k2-6",
            "glm-5.2", "glm-5.1", "glm-5v-turbo", "glm-5.3-flash",
            "minimax-m3-pay", "minimax-m2.7",
        ],
        "template": ('"{node}" "{cli}" -p{full_access} --model "{model}" '
                      '--mcp-config "{mcp}" --output-format text'),
        "full_access_arg": " -y",
        "supports_full_access": True,
        "default_full_access": True,
    },
    "minimax": {
        "label": "MiniMax CLI（适配器）",
        "python": sys.executable,
        "adapter": "",  # scripts/minimax_adapter.py 绝对路径
        "models": ["MiniMax-M2.7-highspeed", "MiniMax-M2.7", "MiniMax-M3"],
        "template": '"{python}" "{adapter}"',
        "full_access_arg": "",
        "supports_full_access": False,
        "default_full_access": False,
    },
}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip() or default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root() -> Path:
    application_root = Path(__file__).resolve().parent.parent
    if (application_root / "scripts").is_dir():
        return application_root
    return application_root.parent.parent


def _web_dist() -> Path:
    """Angular 构建产物目录（src/frontend/dist/worker-portal/browser）。"""
    root = _repo_root()
    candidates = (
        root / "src" / "frontend" / "dist" / "worker-portal" / "browser",
        root / "frontend" / "dist" / "worker-portal" / "browser",
    )
    p = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    if not p.exists():
        # 兼容直接部署时把 dist 放在本模块旁
        p = _repo_root() / "worker-portal-dist"
    return p


def _mappings_path() -> Path:
    raw = _env("AGENTBOARD_LOCAL_MAPPINGS")
    p = Path(raw) if raw else _repo_root() / DEFAULT_MAPPINGS_FILE
    if not p.is_absolute():
        p = _repo_root() / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_mappings() -> dict[str, Any]:
    p = _mappings_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("项目映射解析失败（%s），按空映射处理", e)
    return {"version": 1, "projects": {}}


def _save_mappings(data: dict[str, Any]) -> None:
    p = _mappings_path()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- 服务器代理 ----------

class AgentBoardProxy:
    """瘦代理：用 worker 同款凭据访问服务器 REST。"""

    def __init__(self, api_url: str, token: str):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self._client = httpx.Client(timeout=30.0)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, path: str, **kw: Any) -> Any:
        try:
            r = self._client.get(f"{self.api_url}{path}", headers=self._headers(), **kw)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"服务器不可达：{e}") from e
        if r.status_code == 401:
            raise HTTPException(401, "Token 无权限（检查 AGENTBOARD_WORKER_TOKEN）")
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"服务器 {path} 返回 {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def post(self, path: str, payload: dict[str, Any], status_code: int = 201) -> Any:
        try:
            r = self._client.post(f"{self.api_url}{path}", json=payload,
                                  headers=self._headers())
        except httpx.HTTPError as e:
            raise HTTPException(502, f"服务器不可达：{e}") from e
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"服务器 {path} 返回 {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {"status": r.status_code}

    def put(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            r = self._client.put(f"{self.api_url}{path}", json=payload,
                                 headers=self._headers())
        except httpx.HTTPError as e:
            raise HTTPException(502, f"服务器不可达：{e}") from e
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"服务器 {path} 返回 {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {"status": r.status_code}

    def delete(self, path: str) -> Any:
        try:
            r = self._client.delete(f"{self.api_url}{path}", headers=self._headers())
        except httpx.HTTPError as e:
            raise HTTPException(502, f"服务器不可达：{e}") from e
        if r.status_code == 404:
            # 不幂等：UI 期望明确信号确认"没这个 instance"
            raise HTTPException(404, f"服务器 {path}: instance not found")
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"服务器 {path} 返回 {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {"status": r.status_code}

    def close(self) -> None:
        self._client.close()


# ---------- 请求体 ----------

class AgentBody(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=100)
    roles: list[str] = Field(default_factory=lambda: ["developer", "reviewer"])
    cli_type: str = "codex"
    model: str = ""
    enabled: bool = True
    full_access: bool = True
    mcp_config: str | None = None  # 覆盖默认 mcp-prod.json


class AgentUpdateBody(BaseModel):
    name: str | None = None
    roles: list[str] | None = None
    cli_type: str | None = None
    model: str | None = None
    enabled: bool | None = None
    full_access: bool | None = None
    mcp_config: str | None = None


class MappingBody(BaseModel):
    """单条项目映射：{project_id: {local_dir, name}}。"""
    project_id: int
    local_dir: str
    name: str = ""


class MappingsBody(BaseModel):
    projects: dict[str, MappingBody]  # key=str(project_id)


# ---------- 应用 ----------

def create_app(
    api_url: str | None = None,
    token: str | None = None,
    worker_id: str | None = None,
) -> FastAPI:
    # B-A1 整改：凭据必须显式提供（env 或参数），缺一即 fail-fast。
    # 不再回退到源码硬编码默认值（已移除，防 git 历史泄漏）。
    api = (api_url or _env("AGENTBOARD_API_URL", DEFAULT_API_URL)).strip()
    tok = (token or _env("AGENTBOARD_WORKER_TOKEN", DEFAULT_TOKEN)).strip()
    if not api or not tok:
        missing = [
            name for name, val in (
                ("AGENTBOARD_API_URL", api),
                ("AGENTBOARD_WORKER_TOKEN", tok),
            ) if not val
        ]
        raise SystemExit(
            "[worker_portal] 启动失败：缺少必需凭据 "
            + ", ".join(missing)
            + "。请通过环境变量或 .env 注入（禁止源码硬编码，B-A1/Epic 145 整改）。"
        )
    proxy = AgentBoardProxy(api, tok)
    local_worker_id = (
        worker_id or _env("AGENTBOARD_WORKER_ID") or socket.gethostname()
    ).strip()
    if not local_worker_id:
        raise SystemExit(
            "[worker_portal] 启动失败：无法确定本机 Worker ID；"
            "请设置 AGENTBOARD_WORKER_ID"
        )

    app = FastAPI(title="AgentBoard Worker 本机配置台", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.middleware("http")
    async def _no_cache_assets(request: Request, call_next):
        """开发模式防浏览器死 cache 老 HTML / main bundle。

        Angular 改源码后 dist 重建但 URL 路径不变（index.html / main-*.js / styles-*.css），
        浏览器默认强 cache 会让用户卡在老版本。开发期统一发 no-store 让任何
        刷新都拿到最新 dist，prod 部署换成 nginx / CDN cache header 即可。
        """
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.on_event("shutdown")
    def _shutdown() -> None:  # pragma: no cover
        proxy.close()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok", "api": api, "worker_id": local_worker_id,
            "ts": _now_iso(),
        }

    # ---- CLI 预设 ----
    @app.get("/api/cli-presets")
    def cli_presets() -> dict[str, Any]:
        presets = {}
        for key, p in CLI_PRESETS.items():
            presets[key] = {
                "label": p["label"],
                "models": p["models"],
                "template": p["template"],
                "supports_full_access": p.get("supports_full_access", False),
                "default_full_access": p.get("default_full_access", True),
            }
        return {"presets": presets}

    def _render_cli_command(
        cli_type: str,
        model: str,
        mcp_config: str | None,
        full_access: bool = True,
    ) -> str:
        p = CLI_PRESETS.get(cli_type)
        if not p:
            raise HTTPException(400, f"未知 CLI 类型：{cli_type}")
        access_arg = p.get("full_access_arg", "") if full_access else ""
        if cli_type == "codex":
            command = _env("AGENTBOARD_CODEX_COMMAND", shutil.which("codex") or "codex")
            selected_model = model or p["models"][0]
            return p["template"].format(
                command=command,
                model=selected_model,
                full_access=access_arg,
            )
        if cli_type == "codebuddy":
            mcp = mcp_config or _env("AGENTBOARD_MCP_CONFIG")
            if not mcp:
                mcp = str(_repo_root() / "tmp" / "mcp-prod.json")
            if not Path(mcp).is_absolute():
                mcp = str(_repo_root() / mcp)
            return p["template"].format(
                node=p["node"], cli=p["cli"], model=model or p["models"][0], mcp=mcp,
                full_access=access_arg,
            )
        if cli_type == "minimax":
            adapter = p.get("adapter") or str(
                _repo_root() / "scripts" / "minimax_plan_invoker.py"
            )
            return p["template"].format(python=p["python"], adapter=adapter)
        return p["template"]

    def _ensure_worker_registered() -> Any:
        return proxy.post("/api/workers/register", {
            "worker_id": local_worker_id,
            "hostname": socket.gethostname(),
            "status": "active",
        })

    # ---- 本机 Worker AgentInstance（不读写服务器全局 Agent 池） ----

    # Phase 4 (agent-ephemeral-2026-09): local SQLite registry.
    # When the feature flag is on, the portal reads/writes the local
    # SQLite (worker is the source of truth) and pushes DELTA
    # frames to the server via WebSocket. When the flag is off, the
    # portal falls back to the original server proxy path (P5
    # graceful rollback story).
    from .worker.local_registry import LocalAgentRegistry  # noqa: E402
    from .worker.ws_client import ServerWebSocketClient  # noqa: E402
    # Test seam: tests can pre-set AGENTBOARD_LOCAL_AGENT_DB to a
    # temp file before import, so the test runs against a fresh DB
    # instead of the operator's real ~/.codebuddy/agents.db.
    _db_path = _env("AGENTBOARD_LOCAL_AGENT_DB", "") or None
    local_registry = LocalAgentRegistry(db_path=_db_path) if _db_path else LocalAgentRegistry()
    # WSS server URL is the same as the HTTP API URL with scheme swap.
    wss_url = (api or "").replace("http://", "ws://").replace("https://", "wss://")
    wss_client = (
        ServerWebSocketClient(
            server_url=wss_url,
            token=tok,
            worker_id=local_worker_id,
            registry=local_registry,
        )
        if ephemeral_agents_enabled() and wss_url
        else None
    )
    if wss_client is not None:
        wss_client.start()
        # Try to prime the server cache. If the WSS push fails
        # immediately (or later) the cache will catch up the next
        # time _push_delta_or_log is called.
        wss_client.enqueue_hello()

    def _local_agent_to_dict(a) -> dict[str, Any]:
        """Render a LocalAgent in the shape the existing UI expects
        (so the Angular side needs no change)."""
        return {
            "agent_id": a.agent_id,
            "worker_id": local_worker_id,
            "cli_command": a.cli_command,
            "model": a.model,
            "enabled": bool(a.enabled),
            "online": True,
            "roles": json.dumps(list(a.roles), ensure_ascii=False),
            "last_heartbeat": 0.0,
            "last_probe_at": None,
            "probe_message": "",
            "executor_type": None,
            "id": None,
            "created_at": a.updated_at,
            "updated_at": a.updated_at,
            # Legacy logical-agent fields the UI may look at; absent
            # on the local path so the UI treats each row as its own
            # primary entity. ``name`` falls back to agent_id.
            "name": a.agent_id,
        }

    def _push_hello_or_log() -> None:
        """Send a HELLO frame to the server so the cache reflects
        this worker's full state. Same WSS-first / HTTP-fallback
        strategy as ``_push_delta_or_log``."""
        if wss_client is not None:
            try:
                wss_client.enqueue_hello()
                return
            except Exception as e:  # pragma: no cover
                log.warning("worker_portal: wss hello push failed: %s; "
                            "falling back to HTTP sync", e)
        # HTTP fallback
        try:
            agents = [a.to_frame() for a in local_registry.list_agents()]
            proxy.post(
                "/api/agent-cache/sync",
                {"type": "HELLO",
                 "worker_id": local_worker_id,
                 "agents": agents},
            )
        except Exception as e:  # pragma: no cover
            log.warning("worker_portal: http sync hello failed: %s", e)

    # Fire a HELLO via the HTTP fallback path immediately, so a
    # deployment whose WSS is being eaten by a proxy still gets
    # the cache populated. The WSS thread will keep retrying
    # and eventually take over once the proxy is fixed.
    _push_hello_or_log()

    # Periodic HTTP PING loop (P3.1 hardening).
    #
    # The WSS client (worker/ws_client.py) sends PING every 15s to
    # keep the server-side cache fresh. But when the reverse proxy
    # in front of the server does not forward the WebSocket upgrade
    # (IIS ARR without WebSocket passthrough, nginx missing
    # `proxy_set_header Upgrade $http_upgrade`, Cloudflare Free, etc.)
    # the WSS client never reaches the ``while True: recv`` loop, so
    # no PING is delivered. The HTTP fallback path (P3.1) fires
    # HELLO at portal startup and DELTA on every edit — but does not
    # self-refresh. Between edits the cache's 60s staleness sweep
    # marks entries offline, and ``/api/agent-cache/pick`` starts
    # returning 503 for the ephemeral dispatch path.
    #
    # This loop closes that gap: it POSTs a ``{"type":"PING"}`` frame
    # to the same sync endpoint every ``interval`` seconds so the
    # server keeps this worker's cache entries fresh regardless of
    # WSS health. When WSS *is* working, PINGs arrive twice as often
    # as needed — harmless (server just bumps last_heartbeat).
    #
    # Env knobs:
    #   AGENTBOARD_HTTP_PING_INTERVAL (float seconds, default 20;
    #                                 <= 0 disables the loop)
    def _http_ping_loop() -> None:  # pragma: no cover — background thread
        try:
            interval = float(os.environ.get(
                "AGENTBOARD_HTTP_PING_INTERVAL", "20",
            ).strip() or "20")
        except ValueError:
            interval = 20.0
        if interval <= 0:
            log.info("worker_portal: HTTP PING loop disabled "
                     "(AGENTBOARD_HTTP_PING_INTERVAL<=0)")
            return
        # Give WSS a chance to establish first so we don't double-ping
        # on healthy deployments during the initial connect window.
        _stop_event.wait(timeout=min(interval, 10.0))
        log.info("worker_portal: HTTP PING loop starting (interval=%.1fs)",
                 interval)
        while not _stop_event.wait(timeout=interval):
            try:
                proxy.post("/api/agent-cache/sync",
                           {"type": "PING", "worker_id": local_worker_id})
            except Exception as e:  # noqa: BLE001 — never crash the loop
                log.warning("worker_portal: http ping failed: %s", e)

    import threading as _threading
    _stop_event = _threading.Event()
    _http_ping_thread = _threading.Thread(
        target=_http_ping_loop,
        name=f"worker-portal-http-ping[{local_worker_id}]",
        daemon=True,
    )
    if ephemeral_agents_enabled():
        _http_ping_thread.start()

    @app.on_event("shutdown")
    def _stop_http_ping() -> None:  # pragma: no cover
        _stop_event.set()

    def _push_delta_or_log(*, add=None, remove=None) -> None:
        """Push a frame to the server so the cache reflects this
        worker's state. Tries WSS first; falls back to an HTTP
        sync endpoint on the server when WSS connect keeps
        failing (common when an nginx / IIS ARR in front of the
        server doesn't proxy the WebSocket upgrade). Falls back
        silently — the local SQLite is the source of truth, so
        a missed push only means the cache is stale until the
        next successful frame.
        """
        adds = list(add or [])
        rems = list(remove or [])
        if not adds and not rems:
            return
        if wss_client is not None:
            try:
                wss_client.enqueue_delta(add_or_update=adds, remove=rems)
                return
            except Exception as e:  # pragma: no cover
                log.warning("worker_portal: wss delta push failed: %s; "
                            "falling back to HTTP sync", e)
        # HTTP fallback — POST to /api/agent-cache/sync with the
        # same body shape the WSS handler accepts. This works
        # through any plain HTTP proxy.
        try:
            proxy.post(
                "/api/agent-cache/sync",
                {"type": "DELTA",
                 "worker_id": local_worker_id,
                 "add_or_update": adds, "remove": rems},
            )
        except Exception as e:  # pragma: no cover
            log.warning("worker_portal: http sync fallback failed: %s", e)

    @app.get("/api/agents")
    def list_agents() -> Any:
        if local_registry is not None:
            # Local path: read the SQLite, no server roundtrip.
            return [_local_agent_to_dict(a) for a in local_registry.list_agents()]
        # Legacy path (flag off): roundtrip to the server.
        _ensure_worker_registered()
        instances = proxy.get(f"/api/workers/{local_worker_id}/instances") or []
        logical_agents = proxy.get("/api/agents") or []
        profiles = {item.get("agent_id"): item for item in logical_agents}
        for instance in instances:
            profile = profiles.get(instance.get("agent_id")) or {}
            for field in ("name", "roles", "capabilities"):
                if field in profile:
                    instance[field] = profile[field]
        return instances

    @app.post("/api/agents", status_code=201)
    def create_agent(body: AgentBody) -> Any:
        if local_registry is not None:
            cli_cmd = _render_cli_command(
                body.cli_type, body.model, body.mcp_config, body.full_access,
            )
            saved = local_registry.upsert(
                agent_id=body.agent_id.strip(),
                cli_command=cli_cmd,
                model=(body.model or "").strip(),
                enabled=bool(body.enabled),
                roles=body.roles or ["developer", "reviewer"],
            )
            _push_delta_or_log(add=[saved.to_frame()])
            return _local_agent_to_dict(saved)
        # Legacy path (flag off).
        _ensure_worker_registered()
        cli_cmd = _render_cli_command(
            body.cli_type, body.model, body.mcp_config, body.full_access,
        )
        roles = json.dumps(list(dict.fromkeys(body.roles)), ensure_ascii=False)
        logical_agents = proxy.get("/api/agents") or []
        existing_profile = next(
            (item for item in logical_agents if item.get("agent_id") == body.agent_id),
            None,
        )
        if existing_profile is None:
            # 首次注册绑定当前凭据用户；后续保存绝不能再次 register，否则会把
            # 已配置的项目服务账号 user_id 覆盖成 Portal 的 admin 用户。
            proxy.post(
                "/api/agents/register",
                {
                    "agent_id": body.agent_id,
                    "name": body.name or body.agent_id,
                    "roles": roles,
                },
            )
        else:
            profile_update: dict[str, Any] = {"roles": roles}
            if body.name:
                profile_update["name"] = body.name
            proxy.put(f"/api/agents/{body.agent_id}", profile_update)
        # Step 2: 给本 worker 挂 instance（prod 校验 agent_id 必须已注册）
        return proxy.post(
            f"/api/agents/{body.agent_id}/instances",
            {
                "worker_id": local_worker_id,
                "cli_command": cli_cmd,
                "model": body.model,
                "auth_key": "",
                "enabled": body.enabled,
            },
        )

    @app.put("/api/agents/{agent_id}")
    def update_agent(agent_id: str, body: AgentUpdateBody) -> Any:
        if local_registry is not None:
            # Local path: read existing, apply patches, write back.
            existing = local_registry.get(agent_id)
            if existing is None:
                raise HTTPException(404, f"agent {agent_id} not found in local registry")
            new_model = body.model if body.model is not None else existing.model
            new_enabled = body.enabled if body.enabled is not None else existing.enabled
            new_roles = body.roles if body.roles is not None else list(existing.roles)
            if body.cli_type is not None or body.model is not None or body.full_access is not None:
                # Re-render cli_command if any of these changed
                cli_type = body.cli_type or (
                    "codex" if "codex" in existing.cli_command.lower()
                    else "codebuddy"
                )
                full_access = (
                    body.full_access
                    if body.full_access is not None
                    else ("--dangerously-bypass-approvals-and-sandbox" in existing.cli_command
                          or " -y " in f" {existing.cli_command} ")
                )
                new_cli = _render_cli_command(
                    cli_type, new_model, body.mcp_config, full_access,
                )
            else:
                new_cli = existing.cli_command
            saved = local_registry.upsert(
                agent_id=agent_id,
                cli_command=new_cli,
                model=new_model,
                enabled=new_enabled,
                roles=new_roles,
            )
            _push_delta_or_log(add=[saved.to_frame()])
            return _local_agent_to_dict(saved)
        # Legacy path (flag off).
        _ensure_worker_registered()
        instances = proxy.get(f"/api/workers/{local_worker_id}/instances") or []
        existing = next((item for item in instances if item.get("agent_id") == agent_id), {})
        selected_model = body.model if body.model is not None else existing.get("model", "")
        cli_type = body.cli_type or (
            "codex" if "codex" in str(existing.get("cli_command", "")).lower()
            else "codebuddy"
        )
        existing_cmd = str(existing.get("cli_command", ""))
        existing_full_access = (
            cli_type == "minimax"
            or "--dangerously-bypass-approvals-and-sandbox" in existing_cmd
            or " -y " in f" {existing_cmd} "
        )
        full_access = (
            body.full_access
            if body.full_access is not None
            else existing_full_access
        )
        cli_cmd = _render_cli_command(
            cli_type, selected_model, body.mcp_config, full_access,
        )
        payload = {
            "worker_id": local_worker_id,
            "cli_command": cli_cmd,
            "model": selected_model,
            "auth_key": "",
            "enabled": body.enabled if body.enabled is not None else existing.get("enabled", True),
        }
        profile_update: dict[str, Any] = {}
        if body.name is not None:
            profile_update["name"] = body.name
        if body.roles is not None:
            profile_update["roles"] = json.dumps(
                list(dict.fromkeys(body.roles)), ensure_ascii=False,
            )
        if profile_update:
            proxy.put(f"/api/agents/{agent_id}", profile_update)
        return proxy.post(f"/api/agents/{agent_id}/instances", payload)

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str) -> Any:
        """删除本 Worker 上某 agent。

        Phase 4 path (flag on): delete from the local SQLite and
        emit a DELTA to the server. Phase 3 path (flag off):
        forward to the server's DELETE /api/agents/{id}/instances
        endpoint (unchanged).
        """
        if local_registry is not None:
            if not local_registry.delete(agent_id):
                raise HTTPException(
                    404, f"agent {agent_id} not found in local registry"
                )
            _push_delta_or_log(remove=[agent_id])
            return {"ok": True, "deleted_id": agent_id,
                    "worker_id": local_worker_id}
        # Legacy path (flag off).
        _ensure_worker_registered()
        return proxy.delete(
            f"/api/agents/{agent_id}/instances?worker_id={local_worker_id}"
        )

    # ---- 项目列表 ----
    @app.get("/api/projects")
    def list_projects() -> Any:
        return proxy.get("/api/projects")

    # ---- 项目映射（本机 JSON） ----
    @app.get("/api/mappings")
    def get_mappings() -> dict[str, Any]:
        return _load_mappings()

    @app.put("/api/mappings")
    def put_mappings(body: MappingsBody) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for pid_str, m in body.projects.items():
            normalized[str(int(pid_str))] = {
                "project_id": int(pid_str),
                "name": m.name or "",
                "local_dir": m.local_dir,
            }
        data = {"version": 1, "projects": normalized, "updated_at": _now_iso()}
        _save_mappings(data)
        return data

    # ---- 任务执行记录（服务器 AgentRun） ----
    @app.get("/api/executions")
    def executions(
        agent: str | None = Query(None, max_length=64),
        status: str | None = Query(None, max_length=20),
        q: str | None = Query(None, max_length=200),
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> Any:
        params = {
            key: value for key, value in {
                "agent": agent, "status": status, "q": q,
                "limit": limit, "offset": offset,
            }.items() if value not in (None, "")
        }
        return proxy.get("/api/runs", params=params)

    @app.get("/api/executions/{run_id}")
    def execution_detail(run_id: int) -> Any:
        return proxy.get(f"/api/runs/{run_id}")

    # ---- 原始运行日志（worker-mq.log 摘要） ----
    @app.get("/api/records")
    def records(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        log_path = _env("AGENTBOARD_WORKER_LOG", str(_repo_root() / "tmp" / "worker-mq.log"))
        if not Path(log_path).exists():
            return {"records": [], "log": str(log_path)}
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        items: list[dict[str, Any]] = []
        pat = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
        for ln in reversed(lines[-2000:]):
            m = pat.search(ln)
            ts = m.group(1) if m else ""
            text = ln[m.end() + 1:] if m else ln
            text = text.strip()
            if not text:
                continue
            level = "INFO"
            for lv in ("DEBUG", "WARNING", "ERROR", "CRITICAL"):
                if lv in text:
                    level = lv
                    break
            items.append({"ts": ts, "level": level, "message": text[:500]})
            if len(items) >= limit:
                break
        return {"records": items, "log": str(log_path)}

    # ---- 静态前端（Angular 构建产物，免登录本机页面） ----
    # 注意：必须放在所有 /api/* 路由之后注册，否则 catch-all 会抢先匹配 API。
    web_dist = _web_dist()
    if web_dist.exists():
        assets_dir = web_dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(web_dist / "index.html")

        @app.get("/{path:path}", include_in_schema=False)
        def spa_fallback(path: str) -> FileResponse:
            # 非 /api 的路径回退 index.html（Angular SPA 路由）
            f = web_dist / path
            if path and f.exists() and f.is_file():
                return FileResponse(f)
            return FileResponse(web_dist / "index.html")

    return app


# 模块级 app：仅在环境变量齐全时创建，便于 `uvicorn agentboard.worker_portal:app` 直跑。
# 缺凭据时 app=None —— 真正的 fail-fast 由 `python -m agentboard.worker_portal` 的 main()
# 经 create_app() 抛 SystemExit 触发（非零退出码 + 明确错误信息）。
# 这样可保证 import worker_portal 不会因缺凭据崩溃（测试/工具兼容）。
_module_api_url = _env("AGENTBOARD_API_URL", "")
_module_token = _env("AGENTBOARD_WORKER_TOKEN", "")
if _module_api_url and _module_token:
    app = create_app(_module_api_url, _module_token)
else:
    app = None  # type: ignore[assignment]
    log.warning(
        "worker_portal 模块级 app 未创建：缺少 AGENTBOARD_API_URL/AGENTBOARD_WORKER_TOKEN。"
        "通过 `python -m agentboard.worker_portal` 启动会 fail-fast（B-A1 整改）。"
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(prog="python -m agentboard.worker_portal",
                                     description="Worker 本机配置台（免登录，仅本机）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(_env("AGENTBOARD_PORTAL_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--token", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # B-A1：create_app() 在缺凭据时抛 SystemExit（fail-fast，非零退出码）
    application = create_app(args.api_url, args.token)
    print(f"Worker 配置台启动：http://{args.host}:{args.port} （免登录，仅本机）", flush=True)
    uvicorn.run(application, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
