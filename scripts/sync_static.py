"""Sync ``frontend/dist/frontend/browser/*`` → ``agentboard/web/static/``.

Build 之后调用一次：
    python scripts/sync_static.py
效果：把刚 build 的 Angular 产物落到 ``web_app.py`` 默认 serve 的目录里。
- 备份旧 static/ 为 ``agentboard/web/static.bak.<YYYYmmdd_HHMMSS>/``（已 gitignore）
- 复制 dist 内容（顶层文件 + 子目录）
- 打印最终大小

为什么需要：Angular 源码和随仓静态包分离（``web_app.py`` 优先 serve
``frontend/dist/frontend/browser/``，否则回退 ``agentboard/web/static/``）。
CI / 后端 Dockerfile 不会 rebuild 前端 → 静态包是 release 的产物。

2026-08-20 created for Task 1296 + 1297。
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\AI\Projects\AgentBoard")
DIST = ROOT / "frontend" / "dist" / "frontend" / "browser"
STATIC = ROOT / "agentboard" / "web" / "static"


def main() -> int:
    if not DIST.is_dir():
        print(f"FAIL: dist not found: {DIST}", file=sys.stderr)
        return 1
    if not (DIST / "index.html").exists():
        print(f"FAIL: dist/index.html missing: {DIST}", file=sys.stderr)
        return 1

    # 1) 备份现有 static/
    if STATIC.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = STATIC.parent / f"static.bak.{ts}"
        shutil.move(str(STATIC), str(bak))
        print(f"[sync] moved static -> {bak.name}")
    STATIC.mkdir(parents=True, exist_ok=True)

    # 2) 复制 dist 内容
    for src in DIST.iterdir():
        dst = STATIC / src.name
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    print(f"[sync] copied {sum(1 for _ in STATIC.iterdir())} entries -> static/")

    # 3) 摘要
    total = sum(p.stat().st_size for p in STATIC.rglob("*") if p.is_file())
    print(f"[sync] static/ total size: {total / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
