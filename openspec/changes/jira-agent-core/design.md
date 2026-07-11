# 设计：轻量 Jira 核心与 Agent 开发闭环

## 领域模型
- `Task.priority` 使用稳定字符串枚举：`highest/high/medium/low/lowest`，默认 `medium`。
- `Comment` 从属于 Task，保存作者显示名与 markdown 内容；后续接入用户/Agent 身份时可增加不可变主体 ID。
- `Sprint` 从属于 Project；Task 通过可空 `sprint_id` 进入 Sprint，空值代表 Backlog。
- `Attachment` 只保存元数据和不可猜测的 `storage_key`，文件内容放在配置目录。
- `AgentSchedule` 描述何时触发，`AgentRun` 描述每次执行；Run 以唯一幂等键和租约字段支持多实例调度。

## 接口原则
- REST 是 Web 与默认 MCP API 后端的共同契约；MCP db 后端复用同一 service 规则。
- 新增枚举由 `/api/meta` 暴露，旧客户端忽略新字段仍可继续工作。
- 评论端点为 `GET/POST /api/tasks/{id}/comments` 与 `DELETE /api/comments/{id}`。
- 搜索接口增加 `priority`；后续增加 `sprint_id`、`due_before` 等过滤条件。

## Agent 调度边界
调度扫描器只把到期 Schedule 原子地转换为 Run；执行器获取 Run 后调用明确配置的 Agent 适配器。适配器获得任务、Sprint、附件和评论上下文，通过 MCP/REST 回写进度。运行时设置超时、工作目录白名单和最小权限；push/merge 属于任务级显式策略。

## Web 体验
任务列表与看板用紧凑徽章表达优先级；详情页顶部集中显示类型、优先级、状态，正文和 spec 双栏，评论以时间线呈现。后续 Sprint 采用 Backlog + Sprint 分组，而不是堆叠额外层级表单。

## 迁移与兼容
每个纵向切片独立 Alembic 迁移。已有 Task 的 priority 迁移为 `medium`；新增字段均提供安全默认值或允许为空。删除 Task 及其父级时显式清理关联记录，保持 SQLite 与 MariaDB 行为一致。
