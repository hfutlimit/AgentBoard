"""AgentBoard Angular 前端静态托管服务。"""
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).parent / "web" / "static"
API_URL = os.getenv("AGENTBOARD_API_URL", "http://127.0.0.1:8000")

app = FastAPI(title="AgentBoard Web (Angular)")

# 所有静态文件统一从 static 目录提供
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _index_html() -> str:
    """从 static 目录读取 Angular index.html 并注入 API URL。"""
    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(
            status_code=503,
            detail="index.html not found in static directory",
        )
    content = index_file.read_text(encoding="utf-8")
    return content.replace("__API_URL__", API_URL)


@app.get("/", response_class=HTMLResponse)
def index():
    return _index_html()


@app.get("/{path:path}")
def angular_asset_or_route(path: str):
    """提供 Angular 资源文件，并把浏览器深链接回退到 index.html。"""
    candidate = (STATIC_DIR / path).resolve()
    if candidate.is_relative_to(STATIC_DIR) and candidate.is_file():
        return FileResponse(candidate)
    return HTMLResponse(_index_html())
