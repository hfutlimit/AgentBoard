"""AgentBoard Angular 前端静态托管服务。"""
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).parent / "web" / "static"
API_URL = os.getenv("AGENTBOARD_API_URL", "http://127.0.0.1:8000")

app = FastAPI(title="AgentBoard Web (Angular)")


def _find_css() -> Path | None:
    """查找实际存在的 CSS 文件（beasties 生成的哈希文件名）。"""
    static_dir = STATIC_DIR
    if not static_dir.is_dir():
        return None
    for f in static_dir.iterdir():
        if f.name.startswith("styles-") and f.name.endswith(".css"):
            return f
    return None


class CSSStaticFiles(StaticFiles):
    """StaticFiles 的包装：如果请求 /static/style.css 但不存在，
    则尝试提供实际的哈希 CSS 文件。"""

    def __init__(self, *args, **kwargs):
        self._fallback_css = None
        super().__init__(*args, **kwargs)

    def get_path(self, path: str) -> str:
        full_path = super().get_path(path)
        if path == "style.css" and not os.path.exists(full_path):
            # 尝试查找哈希 CSS
            for f in Path(self.directory).iterdir():
                if f.name.startswith("styles-") and f.name.endswith(".css"):
                    self._fallback_css = f.name
                    return str(f)
        return full_path


# 挂载静态文件目录
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


@app.get("/static/style.css")
def style_css():
    """如果 style.css 不存在，尝试提供哈希 CSS 文件。"""
    css_file = _find_css()
    if css_file:
        return FileResponse(str(css_file), media_type="text/css")
    raise HTTPException(status_code=404, detail="style.css not found")


@app.get("/{path:path}")
def angular_asset_or_route(path: str):
    """提供 Angular 资源文件，并把浏览器深链接回退到 index.html。"""
    candidate = (STATIC_DIR / path).resolve()
    if candidate.is_relative_to(STATIC_DIR) and candidate.is_file():
        return FileResponse(candidate)
    return HTMLResponse(_index_html())
