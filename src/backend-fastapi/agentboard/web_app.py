"""AgentBoard Angular 前端静态托管服务。"""
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


_legacy_static_dir = Path(__file__).parent / "web" / "static"
_angular_dist_candidates = (
    Path(__file__).parent.parent / "frontend" / "dist" / "frontend" / "browser",
    Path(__file__).resolve().parents[2] / "frontend" / "dist" / "frontend" / "browser",
)
_angular_dist_dir = next(
    (candidate for candidate in _angular_dist_candidates if candidate.is_dir()),
    _angular_dist_candidates[0],
)
STATIC_DIR = Path(os.getenv(
    "AGENTBOARD_WEB_STATIC_DIR",
    str(_angular_dist_dir if _angular_dist_dir.is_dir() else _legacy_static_dir),
))
# B-A4（Epic 145 / Story 291）：STATIC_DIR resolve 一次缓存，避免每次请求重复解析
# 路径穿越校验依赖该锚点。
STATIC_DIR_RESOLVED = STATIC_DIR.resolve()
# 浏览器端可访问的 API 地址。
# - 优先读 AGENTBOARD_WEB_API_URL（与 .env / docker-compose 对外 key 约定一致，本地 dev 走这个）
# - 兼容读 AGENTBOARD_API_URL（docker-compose 会把对外 key 映射到容器内同名 env）
# - 默认 58124 兜底（生产 .NET BFF 默认端口）
# - 统一 .strip()：cmd.exe 的 `set NAME=VALUE && ...` 会把 set 后面的空格吞进 env 值，
#   注入到前端 <script>window.AGENTBOARD_API = "..."</script> 后 `new URL("...18000 ")`
#   抛 Invalid URL（XMLHttpRequest.open 也会拒绝带空格的 URL）
API_URL = (
    (os.getenv("AGENTBOARD_WEB_API_URL") or os.getenv("AGENTBOARD_API_URL") or "http://127.0.0.1:58124")
    .strip()
)
SIGNALR_URL = (os.getenv("AGENTBOARD_WEB_SIGNALR_URL") or os.getenv("AGENTBOARD_SIGNALR_URL") or "").strip()

app = FastAPI(title="AgentBoard Web (Angular)")


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """Review 2026-08-21：浏览器缓存导致 sync_static 同步新 bundle 后用户还看旧版。

    index.html 没哈希化（永远是 index.html），浏览器可能拿缓存的旧版指向老 chunk 名。
    chunk 虽然哈希化（main-XXXX.js）但浏览器也可能缓存。
    一律加 no-cache 头，强制每次回源确认（Angular dev 默认也是这策略）。
    """
    response: Response = await call_next(request)
    if request.url.path.startswith(("/static", "/")):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


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
    content = content.replace("__SIGNALR_URL__", SIGNALR_URL)
    # 修复 favicon
    content = content.replace('href="favicon.svg"', 'href="/static/favicon.svg"')
    # 修复 JS 引用: src="main-XXX.js" → src="/static/main-XXX.js"
    content = re.sub(r'src="(main-[^"]+\.js)"', r'src="/static/\1"', content)
    # 修复 CSS 引用: href="styles-XXX.css" → href="/static/styles-XXX.css"
    content = re.sub(r'href="(styles-[^"]+\.css)"', r'href="/static/\1"', content)
    # Review 2026-08-21：注入 build fingerprint 到 <head> 末尾，让用户能直接看出浏览器拿的版本
    # 解决"sync_static 同步新 bundle 后用户浏览器还看旧"的排查困难
    bundle = re.search(r'main-([A-Z0-9]+)\.js', content)
    styles = re.search(r'styles-([A-Z0-9]+)\.css', content)
    fp = f'<meta name="agb-build" content="js={bundle.group(1) if bundle else "?"};css={styles.group(1) if styles else "?"};api={API_URL};pid={os.getpid()}"/>'
    content = content.replace('</head>', f'{fp}</head>', 1)
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
