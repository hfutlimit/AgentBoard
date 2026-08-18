"""AgentBoard Angular 前端静态托管服务。"""
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


_legacy_static_dir = Path(__file__).parent / "web" / "static"
_angular_dist_dir = Path(__file__).parent.parent / "frontend" / "dist" / "frontend" / "browser"
STATIC_DIR = Path(os.getenv(
    "AGENTBOARD_WEB_STATIC_DIR",
    str(_angular_dist_dir if _angular_dist_dir.is_dir() else _legacy_static_dir),
))
# B-A4（Epic 145 / Story 291）：STATIC_DIR resolve 一次缓存，避免每次请求重复解析
# 路径穿越校验依赖该锚点。
STATIC_DIR_RESOLVED = STATIC_DIR.resolve()
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
    return HTMLResponse(_fixed_index())


@app.get("/{path:path}")
def angular_asset_or_route(path: str):
    """提供 Angular 资源文件，并把浏览器深链接回退到 index.html。

    安全（B-A4 / Epic 145 / Story 291）：
        ``{path:path}`` 会原样接收含 ``..`` 段的路径（FastAPI 不剥离）。
        历史实现对 ``STATIC_DIR / path`` 直接 ``is_file()`` 判断后返回，
        导致 ``GET /..%2F..%2F.env`` 等可绕过到任意文件（读 .env / 源码）。
        这里先 ``resolve()`` 再用 ``is_relative_to(STATIC_DIR_RESOLVED)`` 收口，
        任何逃逸出静态根的路径统一 404，不泄露文件是否存在。
    """
    # B-A4：先 resolve 再校验归属，拒绝任何穿越出 STATIC_DIR 的路径。
    # 即使路径含 ``..``、符号链接、编码变体，resolve() 都会归一为绝对真实路径。
    resolved = (STATIC_DIR / path).resolve()
    if not resolved.is_relative_to(STATIC_DIR_RESOLVED):
        # 统一 404（不区分「不存在」与「越权」）避免信息泄露
        raise HTTPException(status_code=404)
    if resolved.is_file():
        return FileResponse(resolved)
    # 回退到 index.html
    return HTMLResponse(_fixed_index())
