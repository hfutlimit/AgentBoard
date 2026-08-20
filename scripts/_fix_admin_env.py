"""One-shot helper: 替换 E2E 脚本里的硬编码 admin/admin123 为 env var 读取。

不推荐长期使用；本脚本只用于 Task 1324 一次性迁移。
"""
import re
from pathlib import Path

TARGETS = [
    "test_review_all_views.py",
    "test_story317_e2e.py",
    "test_story318_319_e2e.py",
    "test_story320_e2e.py",
    "test_x1_pr3_route_switch.py",
    "test_x2_pr1_workspace_topbar.py",
    "test_x2_pr3_heading_settings.py",
    "test_x3_pr1_list_views.py",
    "test_x3_pr2_detail_views.py",
]

ROOT = Path(r"D:\AI\Projects\AgentBoard\tests\e2e_epic149")

for name in TARGETS:
    p = ROOT / name
    c = p.read_text(encoding="utf-8")
    orig = c

    # 替换 ADMIN_USER = "admin" → os.environ.get(...)
    c = re.sub(
        r'^(ADMIN_USER\s*=\s*)"admin"\s*$',
        r'\1os.environ.get("AGENTBOARD_E2E_USER", "admin")',
        c,
        flags=re.M,
    )
    # 替换 ADMIN_PASS = "admin123"
    c = re.sub(
        r'^(ADMIN_PASS\s*=\s*)"admin123"\s*$',
        r'\1os.environ.get("AGENTBOARD_E2E_PASS", "admin123")',
        c,
        flags=re.M,
    )
    # 替换 USER = "admin"
    c = re.sub(
        r'^(USER\s*=\s*)"admin"\s*$',
        r'\1os.environ.get("AGENTBOARD_E2E_USER", "admin")',
        c,
        flags=re.M,
    )
    # 替换 PASS = "admin123"
    c = re.sub(
        r'^(PASS\s*=\s*)"admin123"\s*$',
        r'\1os.environ.get("AGENTBOARD_E2E_PASS", "admin123")',
        c,
        flags=re.M,
    )

    if c != orig:
        p.write_text(c, encoding="utf-8")
        print(f"FIXED: {name}")
    else:
        print(f"NO-CHANGE: {name}")
