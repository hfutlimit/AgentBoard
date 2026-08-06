# Design：S1 M1 数据模型 + 状态机 + REST API

> ID: agent-collab-s1-m1-20260807 · 上游：文档 #50/#51/#52

## 1. 数据模型

### agents 表（新建）

| 列 | 类型 | 说明 |
|---|---|---|
| id | Integer PK | 自增 |
| agent_id | String(64) UNIQUE | 外部 Agent 自报标识（幂等键） |
| name | String(100) | 显示名 |
| roles | String(200) JSON | reviewer/developer/requester 多选 |
| capabilities | String(500) JSON | 能力标签 |
| cli_command | String(500) | CLI 拉起命令模板 |
| auth_key | String(100) | 绑定 abk_ key 指纹 |
| user_id | FK users.id NULL | 绑定的服务账号（经 ProjectMember 授权） |
| online | Boolean | 在线态（心跳维护） |
| last_heartbeat | DateTime NULL | 心跳时间 |

### stories 表（增量列）

| 列 | 类型 | 说明 |
|---|---|---|
| reviewer_id | FK users.id NULL + index | 被指派评审人 |
| review_round | Integer default 0 | 评审轮次（护栏上限 5） |

Story CHECK 约束扩展：`+ 'pending_review','ready','blocked'`（blocked 为护栏终态）。

## 2. 状态机

```
backlog ──assign-reviewer──► pending_review ──reviewer approve──► ready
    ▲                            │
    │                            └─reject（评论 + round+1）──► pending_review（循环）
    │                                 round ≥ 5 ──► blocked（护栏，待人工）
    └── 既有 Story 创建默认 backlog（评审流显式触发）
```

- 仅被指派 reviewer（`reviewer_id` 匹配）可评审 pending_review 的 Story；
- `pending_review`/`ready` 独立常量 `STORY_REVIEW_STATUSES`，不入共用 `Status` 枚举；
- `update_story` 校验扩展；`update_task`/`update_epic` 零影响（Task 走 `ALL_STATUSES` + `TRANSITIONS`）。

## 3. REST API（增量）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | /api/agents/register | 注册/更新（幂等） | 任意已认证，绑定当前用户 |
| POST | /api/agents/{agent_id}/heartbeat | 心跳保活 | Agent 自身（user_id 归属校验）/admin |
| POST | /api/agents/{agent_id}/deregister | 注销下线 | Agent 自身 / admin |
| GET | /api/agents?online=&role= | 列表过滤 | 任意已认证 |
| POST | /api/stories/{sid}/assign-reviewer | 随机指派（CAS 幂等） | 项目成员写（中间件） |
| POST | /api/stories/{sid}/review | approve/reject + 评论（CAS） | 被指派 reviewer |
| GET | /api/stories?status=&reviewer_id=me&project_id= | 全局列表/评审任务 | 项目成员 |

- 项目级路由由 `project_access_middleware` 自动覆盖（`/api/stories/{sid}/...` 前缀已解析项目）；
- agents 为全局资源（不属具体项目），端点内做登录校验。

## 4. 并发与安全

- **CAS**：`assign_reviewer` / `review_story` 用 SQLAlchemy `update()` 条件 UPDATE，`rowcount == 1` 才提交；并发写者获胜时回查现态或报 `review conflict`。
- **评审意见唯一载体**：approve/reject 必须伴随 comment（`create_comment`，story_id 评论）。
- **归属校验**：heartbeat/deregister 校验 `agent.user_id` 归属（admin 绕过）。

## 5. 兼容与回退

- Story 创建路径默认 backlog，Epic 96 `proposal_convert` 零影响；
- 前端仅补展示映射（statusLabel/statusColor/statusSemanticClass），不新增组件；
- 零新增第三方依赖；双后端迁移幂等（SQLite batch_alter_table / MariaDB drop+create constraint）。
