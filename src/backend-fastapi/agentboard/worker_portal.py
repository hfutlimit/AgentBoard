"""Worker 本机配置台（Epic 122 · Story 243 S5）。

本机轻量 FastAPI 服务（默认 127.0.0.1:18240，**免登录**——仅本机绑定）：

- ``GET  /api/agents``           代理服务器 Agent 池（list_agents）；
- ``POST /api/agents``           注册/更新本机 Agent（写服务器 agents 表）；
- ``PUT  /api/agents/{id}``      更新 Agent（CLI 类型/模型/enabled）；
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
- CLI/模型下拉为内置预设（codebuddy / minimax-cli），保存时渲染成 cli_command 模板；
- 免登录约束：uvicorn 仅绑定 127.0.0.1；生产使用须经反向代理 + 访问控制。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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

# CLI 预设：key → {label, template(含 {model} 占位), models: [...]}
CLI_PRESETS: dict[str, dict[str, Any]] = {
    "codebuddy": {
        "label": "WorkBuddy CodeBuddy CLI",
        "node": "C:/Users/jason/.workbuddy/binaries/node/versions/22.22.2/node.exe",
        "cli": "E:/Program Files/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy",
        "mcp_config": None,  # 由页面/默认 mcp-prod.json 填充
        "models": [
            "hy3", "hy3-preview-agent", "deepseek-v4-flash", "deepseek-v4-pro",
            "kimi-k3-2", "kimi-k2-7", "kimi-k2-6", "glm-5.2", "glm-5.1",
            "glm-5v-turbo", "minimax-m3-pay",
        ],
        "template": ('"{node}" "{cli}" -p -y --mcp-config "{mcp}" --output-format text'),
    },
    "minimax": {
        "label": "MiniMax CLI（适配器）",
        "python": sys.executable,
        "adapter": "",  # scripts/minimax_adapter.py 绝对路径
        "models": ["MiniMax-M2", "MiniMax-M2.5", "MiniMax-M2.7"],
        "template": '"{python}" "{adapter}"',
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

    def close(self) -> None:
        self._client.close()


# ---------- 请求体 ----------

class AgentBody(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    cli_type: str = "codebuddy"
    model: str = ""
    enabled: bool = True
    mcp_config: str | None = None  # 覆盖默认 mcp-prod.json


class AgentUpdateBody(BaseModel):
    name: str | None = None
    cli_type: str | None = None
    model: str | None = None
    enabled: bool | None = None
    mcp_config: str | None = None


class MappingBody(BaseModel):
    """单条项目映射：{project_id: {local_dir, name}}。"""
    project_id: int
    local_dir: str
    name: str = ""


class MappingsBody(BaseModel):
    projects: dict[str, MappingBody]  # key=str(project_id)


# ---------- 应用 ----------

def create_app(api_url: str | None = None, token: str | None = None) -> FastAPI:
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

    app = FastAPI(title="AgentBoard Worker 本机配置台", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.on_event("shutdown")
    def _shutdown() -> None:  # pragma: no cover
        proxy.close()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "api": api, "ts": _now_iso()}

    # ---- CLI 预设 ----
    @app.get("/api/cli-presets")
    def cli_presets() -> dict[str, Any]:
        presets = {}
        for key, p in CLI_PRESETS.items():
            presets[key] = {
                "label": p["label"],
                "models": p["models"],
                "template": p["template"],
            }
        return {"presets": presets}

    def _render_cli_command(cli_type: str, model: str, mcp_config: str | None) -> str:
        p = CLI_PRESETS.get(cli_type)
        if not p:
            raise HTTPException(400, f"未知 CLI 类型：{cli_type}")
        if cli_type == "codebuddy":
            mcp = mcp_config or _env("AGENTBOARD_MCP_CONFIG")
            if not mcp:
                mcp = str(_repo_root() / "tmp" / "mcp-prod.json")
            if not Path(mcp).is_absolute():
                mcp = str(_repo_root() / mcp)
            return p["template"].format(node=p["node"], cli=p["cli"], mcp=mcp)
        if cli_type == "minimax":
            adapter = p.get("adapter") or str(_repo_root() / "scripts" / "minimax_adapter.py")
            return p["template"].format(python=p["python"], adapter=adapter)
        return p["template"]

    # ---- Agent 池 ----
    @app.get("/api/agents")
    def list_agents() -> Any:
        return proxy.get("/api/agents")

    @app.post("/api/agents", status_code=201)
    def create_agent(body: AgentBody) -> Any:
        cli_cmd = _render_cli_command(body.cli_type, body.model, body.mcp_config)
        payload = {
            "agent_id": body.agent_id,
            "name": body.name,
            "roles": "[]",
            "capabilities": "[]",
            "cli_command": cli_cmd,
            "model": body.model,
            "enabled": body.enabled,
        }
        try:
            return proxy.post("/api/agents/register", payload)
        except HTTPException as e:
            # 已存在 → 走 PUT 更新（幂等语义）
            if e.status_code in (409, 400):
                upd = {k: v for k, v in {
                    "name": body.name, "cli_command": cli_cmd,
                    "model": body.model, "enabled": body.enabled,
                }.items() if v is not None}
                return proxy.put(f"/api/agents/{body.agent_id}", upd)
            raise

    @app.put("/api/agents/{agent_id}")
    def update_agent(agent_id: str, body: AgentUpdateBody) -> Any:
        upd: dict[str, Any] = {}
        if body.name is not None:
            upd["name"] = body.name
        if body.enabled is not None:
            upd["enabled"] = body.enabled
        if body.model is not None:
            upd["model"] = body.model
        if body.cli_type is not None:
            existing = None
            try:
                agents = proxy.get("/api/agents") or []
                existing = next((a for a in agents if a.get("agent_id") == agent_id), None)
            except HTTPException:
                pass
            cli_cmd = _render_cli_command(body.cli_type, body.model or (existing or {}).get("model", ""),
                                          body.mcp_config)
            upd["cli_command"] = cli_cmd
        if not upd:
            raise HTTPException(400, "无更新字段")
        return proxy.put(f"/api/agents/{agent_id}", upd)

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
