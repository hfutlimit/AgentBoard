"""Root pytest configuration (repo-level).

三件事（2026-09-01 P0 修复轮顺手治理）：

1. sys.path bootstrap：让 tests/ 下任何文件在收集期都能 import
   `agentboard`，不再依赖外部 PYTHONPATH。个别 legacy 文件自己的
   bootstrap 有 path 计算 bug（如 tests/e2e/multi_agent 下的
   parents[2] 算成了 tests/ 而不是 repo root）。

2. collect_ignore：直连 dev server / Playwright 的手工评审脚本
   （见 deliverables/AgentBoard-本地部署测试报告-20260815.md「已知
   flaky 干扰」一节，commit 9514b8b 前后混入 tests/ 根目录）在模块
   import 期直接执行 requests -> localhost + sys.exit(1)，没有 live
   stack 时 pytest 一收集就 INTERNALERROR。它们是手工评审产物，
   这里永久移出默认收集（等价于每次手工 --ignore，但不再依赖记忆）。
   文件保留在磁盘上，想跑就配合 live dev server 手工执行。

3. 根级 pytest.ini 见同目录（默认 -m "not e2e and not manual and not legacy"）。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

collect_ignore_glob = [
    # 直连 dev server 的手工 smoke / review 脚本（import 期即发请求 + sys.exit）
    "tests/test_crud_smoke.py",
    "tests/test_review_84_85.py",
    "tests/test_review_87_92.py",
    "tests/test_sprint_api_review.py",
    "tests/test_sprint_ui_review.py",
    "tests/test_story_filter_fast.py",
    "tests/test_story_fix_verify.py",
    "tests/test_a22_e2e.py",            # sys.path.insert(0, "/tmp") Linux 路径
    "tests/admin_portal/*.py",           # _harness + Playwright 手工 UI 巡检
]
