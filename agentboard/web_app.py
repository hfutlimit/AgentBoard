"""AgentBoard Angular 前端静态托管服务。"""
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


STATIC_DIR = Path(__file__).parent / "web" / "static"
API_URL = os.getenv("AGENTBOARD_API_URL", "http://127.0.0.1:58124")

app = FastAPI(title="AgentBoard Web (Angular)")

# 挂载静态文件（StaticFiles 自动处理 MIME 类型）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _fixed_index() -> str:
    """读取 index.html 并修复资源路径为 /static/ 前缀。"""
    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=503, detail="index.html not found")
    content = index_file.read_text(encoding="utf-8")
    # 注入 API URL
    content = content.replace("__API_URL__", API_URL)
    # 修复 favicon
    content = content.replace('href="favicon.svg"', 'href="/static/favicon.svg"')
    # 修复 JS 引用: src="main-XXX.js" → src="/static/main-XXX.js"
    content = re.sub(r'src="(main-[^"]+\.js)"', r'src="/static/\1"', content)
    # 修复 CSS 引用: href="styles-XXX.css" → href="/static/styles-XXX.css"
    content = re.sub(r'href="(styles-[^"]+\.css)"', r'href="/static/\1"', content)
    return content


@app.get("/")
def root():
    return _fixed_index()


@app.get("/{path:path}")
def angular_asset_or_route(path: str):
    """提供 Angular 资源文件，并把浏览器深链接回退到 index.html。"""
    # 先尝试 /static/ 路径
    static_candidate = STATIC_DIR / path
    if static_candidate.is_file():
        return FileResponse(static_candidate)
    # 回退到 index.html
    return HTMLResponse(_fixed_index())
