## ADDED Requirements

### Requirement: Epic 支持已阻塞状态持久化

系统 SHALL 将 `blocked` 作为 Epic 的可持久化状态。`PATCH /api/epics/{id}` 使用
`{"status":"blocked"}` 更新既有 Epic 时 SHALL 返回 HTTP 200，并在响应与后续读取中
返回 `status: "blocked"`。SQLite 与 MariaDB 的 `ck_epics_status` 约束 SHALL 与 ORM
模型一致，允许 `backlog`、`todo`、`in_progress`、`in_review`、`verifying`、`done` 和
`blocked`，不得将该合法请求泄漏为数据库完整性错误或 HTTP 500。

#### Scenario: 通过 API 将 Epic 标记为已阻塞

- **GIVEN** 一个已持久化的 Epic
- **WHEN** 客户端调用 `PATCH /api/epics/{id}` 并提交 `{"status":"blocked"}`
- **THEN** 服务返回 HTTP 200 和 `status: "blocked"`
- **AND** 后续读取该 Epic 时状态仍为 `blocked`
