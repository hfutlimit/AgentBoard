# Design — 遗留任务状态同步验收

## 背景

本轮为**验收型交付**：4 个遗留任务的实现代码已存在于仓库（历史提交），本次只做状态同步，无新增代码。

## 验证路径

```
MCP 不可用（连接器断连）→ 用轻量 MCP (Streamable HTTP) 客户端直连生产 /mcp 端点
  → tools/list 确认工具注册（103 个）
  → 实测关键工具（list_members / get_project_stats / list_notifications / admin_list_users /
     list_attachments / create_schedule / delete_schedule）
  → set_status 逐级推进状态
```

## 关键决策

1. **数据源**：生产 MCP（http://124.220.44.12/mcp，AgentBoard v3.4.4）是唯一权威源，本机 docker API（18000/MariaDB 测试库）与生产库数据不同，不采用。
2. **编码坑**：MCP 响应 Content-Type 无 charset，requests 默认 ISO-8859-1 解码会破坏 UTF-8 JSON → 改用 `resp.content.decode("utf-8")`。
3. **状态机**：生产版本 `set_status` 仅接受单步合法迁移（backlog→in_review 报"不合法"）→ 对 Task 102 逐级推进。
4. **环境约束**：未触碰端口 18001（本机 MCP 容器）；未重启任何容器；零代码改动（状态同步 + 文档）。

## 验收结果

| 检查项 | 结果 |
|---|---|
| test_scheduler.py | 11 passed |
| 回归（5 个模块） | 16 passed / 9 skipped / 0 failed |
| 生产 MCP 工具实测 | 全部正常返回 |
| Task 状态 | 87/88/89/102 → in_review |
