"""task 102: MCP 工具补全 — 成员管理 / 通知 / 管理员 工具注册断言。

轻量级回归测试：直接导入 agentboard.mcp_server，断言本次新增的
13 个 MCP 工具已正确注册（不启动完整 MCP/API 服务）。
"""
import asyncio

import agentboard.mcp_server as mcp_mod

EXPECTED_TOOLS = {
    # 成员管理
    "list_members", "add_member", "remove_member", "update_member_role",
    # 通知
    "list_notifications", "notification_unread_count", "mark_notification_read",
    "mark_all_notifications_read", "delete_notification",
    # 管理员
    "admin_list_users", "admin_set_user_admin", "admin_list_projects", "admin_delete_project",
}


def test_task102_mcp_tools_registered():
    names = {t.name for t in asyncio.run(mcp_mod.mcp.list_tools())}
    missing = EXPECTED_TOOLS - names
    assert not missing, f"缺失 MCP 工具: {missing}"
