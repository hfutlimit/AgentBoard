"""Admin Portal E2E 测试骨架包。

统一承载 admin-portal 全部端到端验证（登录 / 用户管理 / 项目管理 / 统计页）。
通过 `scripts/serve_admin_portal.py` 将静态产物 + /api 反向代理到本地 58125，
使 E2E 无需手动 `ng serve` 即可独立运行。

运行:
    python scripts/serve_admin_portal.py --port 4321 &
    python tests/admin_portal/run_all.py
"""
