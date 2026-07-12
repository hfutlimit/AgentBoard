"""AgentBoard Angular 前端静态托管服务。"""
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parent.parent
LEGACY_ASSETS = Path(__file__).parent / "web" / "static"
ANGULAR_DIST = Path(
    os.getenv("AGENTBOARD_WEB_DIST", ROOT / "frontend" / "dist" / "frontend" / "browser")
).resolve()
API_URL = os.getenv("AGENTBOARD_API_URL", "http://127.0.0.1:8000")

app = FastAPI(title="AgentBoard Web (Angular)")
app.mount("/static", StaticFiles(directory=str(LEGACY_ASSETS)), name="static")


def _index_html() -> str:
    index_file = ANGULAR_DIST / "index.html"
    if not index_file.is_file():
        raise HTTPException(
            status_code=503,
            detail="Angular build not found; run `npm ci && npm run build` in frontend/",
        )
    return index_file.read_text(encoding="utf-8").replace("__API_URL__", API_URL)


@app.get("/", response_class=HTMLResponse)
def index():
    return _index_html()


@app.get("/{path:path}")
def angular_asset_or_route(path: str):
    """提供带哈希的 Angular 资源，并把浏览器深链接回退到 index.html。"""
    candidate = (ANGULAR_DIST / path).resolve()
    if candidate.is_relative_to(ANGULAR_DIST) and candidate.is_file():
        return FileResponse(candidate)
    return HTMLResponse(_index_html())
