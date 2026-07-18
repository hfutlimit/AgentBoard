#!/usr/bin/env python3
"""打包 AgentBoard 为三个 Windows 部署单元：
  - agentboard-webapi.zip  : REST API（WebAPI 服务）
  - agentboard-mcp.zip     : MCP Streamable-HTTP 服务
  - agentboard-web.zip     : Angular 静态前端（IIS 托管）
依赖 scripts/deploy/ 下的运行时脚本与 web.config。
"""
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
DEPLOY = os.path.join(ROOT, "scripts", "deploy")

# Web 静态来源：优先用新构建产物，回退到已拷贝的 static 目录
WEB_BUILD = os.path.join(ROOT, "frontend", "dist", "frontend", "browser")
WEB_STATIC_FALLBACK = os.path.join(ROOT, "agentboard", "web", "static")


def log(msg):
    print("[package]", msg)


def rmtree(p):
    if os.path.isdir(p):
        shutil.rmtree(p)


def copy_tree_ignore_pycache(src, dst, ignore=None):
    """copytree 但忽略 __pycache__。"""
    if not os.path.exists(dst):
        os.makedirs(dst)
    for entry in os.listdir(src):
        s = os.path.join(src, entry)
        d = os.path.join(dst, entry)
        if entry == "__pycache__":
            continue
        if os.path.isdir(s):
            copy_tree_ignore_pycache(s, d, ignore)
        else:
            shutil.copy2(s, d)


def copy_file(src, dst_dir):
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copy2(src, os.path.join(dst_dir, os.path.basename(src)))


def build_webapi_pkg():
    d = os.path.join(DIST, "agentboard-webapi")
    rmtree(d)
    copy_tree_ignore_pycache(os.path.join(ROOT, "agentboard"), os.path.join(d, "agentboard"))
    copy_tree_ignore_pycache(os.path.join(ROOT, "migrations"), os.path.join(d, "migrations"))
    copy_file(os.path.join(ROOT, "alembic.ini"), d)
    copy_file(os.path.join(ROOT, "requirements.txt"), d)
    for f in ["run-webapi.ps1", "install-service.ps1", "make-mcp-token.py",
              "env.webapi.example", "README.webapi.md"]:
        copy_file(os.path.join(DEPLOY, f), d)
    zip_dir(d, os.path.join(DIST, "agentboard-webapi.zip"))


def build_mcp_pkg():
    d = os.path.join(DIST, "agentboard-mcp")
    rmtree(d)
    copy_tree_ignore_pycache(os.path.join(ROOT, "agentboard"), os.path.join(d, "agentboard"))
    copy_tree_ignore_pycache(os.path.join(ROOT, "migrations"), os.path.join(d, "migrations"))
    copy_file(os.path.join(ROOT, "alembic.ini"), d)
    copy_file(os.path.join(ROOT, "requirements.txt"), d)
    for f in ["run-mcp.ps1", "install-service.ps1", "make-mcp-token.py",
              "env.mcp.example", "README.mcp.md"]:
        copy_file(os.path.join(DEPLOY, f), d)
    zip_dir(d, os.path.join(DIST, "agentboard-mcp.zip"))


def build_web_pkg():
    d = os.path.join(DIST, "agentboard-web")
    rmtree(d)
    src = WEB_BUILD if os.path.isdir(WEB_BUILD) else WEB_STATIC_FALLBACK
    if not os.path.isdir(src):
        log("ERROR: 未找到前端构建产物，请先执行 npm run build。")
        sys.exit(1)
    copy_tree_ignore_pycache(src, d)
    for f in ["web.config", "configure-api-url.ps1", "README.web.md"]:
        copy_file(os.path.join(DEPLOY, f), d)
    zip_dir(d, os.path.join(DIST, "agentboard-web.zip"))


def zip_dir(folder, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(folder):
            for fn in files:
                fp = os.path.join(root, fn)
                z.write(fp, os.path.relpath(fp, folder))
    log(f"已生成 {os.path.relpath(zip_path, ROOT)}  ({os.path.getsize(zip_path)//1024} KB)")


def main():
    rmtree(DIST)
    os.makedirs(DIST, exist_ok=True)
    log("打包 WebAPI ...")
    build_webapi_pkg()
    log("打包 MCP ...")
    build_mcp_pkg()
    log("打包 Web ...")
    build_web_pkg()
    log("完成。产物位于 dist/")


if __name__ == "__main__":
    main()
