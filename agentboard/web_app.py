"""AgentBoard Web 前端（独立服务，前后端分离）。

只负责托管静态 SPA；所有数据通过浏览器 fetch 调用 REST API。
独立运行：uvicorn agentboard.web_app:app --port 8080

前端通过 window.AGENTBOARD_API 指向 API 地址（默认 http://127.0.0.1:8000）。
可用环境变量 AGENTBOARD_API_URL 覆盖，注入到页面。
"""
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).parent / "web" / "static"
API_URL = os.getenv("AGENTBOARD_API_URL", "http://127.0.0.1:8000")

app = FastAPI(title="AgentBoard Web")


@app.get("/", response_class=HTMLResponse)
def index():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return html.replace("__API_URL__", API_URL)


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
