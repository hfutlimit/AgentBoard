# Design: Epic 96 P3 — 定稿转化 Story/Task

## 决策

### 转化入口：服务端 REST 端点（人工终审闸门）

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| A. WorkBuddy 直接 `POST /api/stories` + `POST /api/tasks` | 链路最短 | 绕过终审，与「保留人类最后一道闸」冲突；WorkBuddy 需自行处理幂等/归属/状态推进，易与 Worker 职责重叠 | ✗ |
| B. 服务端专用转化端点 `POST /api/proposals/{pid}/convert` | 终审闸门内建；幂等内建；converged_spec 解析与子 Task 生成集中一处；MCP 侧只是薄代理 | 多一层端点 | ✓（采用） |

选择 B：转化是**业务事务**（创建 Story + 解析生成 Tasks + 回填 story_id + 状态推进必须原子落库），不是若干零散 REST 调用的组合。端点内实现保证了事务性与幂等性，人工终审通过 UI/MCP 确认一次即可。

### 子 Task 解析规则：复用 `generate_tasks_from_spec` 语义

`converged_spec` 中的 `- [ ]` / `- [x]` 清单行生成子 Task（正则与既有 `generate_tasks_from_spec` 完全一致），普通行忽略。重复标题去重（`seen` 集合），避免同一需求被拆成两个相同 Task。

### 幂等策略：story_id 回填即「已转化」标记

P1/P2 已确立 at-least-once 投递哲学（`(proposal_id, round_no)` 唯一约束兜底重投）。P3 沿用：**`proposal.story_id` 非空且 Story 仍存在 → 直接返回既有结果**，绝不重复创建。重复调用（如 MQ 重放、用户双击）返回同一 Story，无副作用。

## 状态机

`converged → story_created` 已存在于 `PROPOSAL_TRANSITIONS`（P0 定义）。本变更不修改状态机表，仅新增**唯一的消费该迁移的入口**。`story_created` 保持终态（`set()`）。

## 数据流

```
[人工终审] -- POST /api/proposals/{pid}/convert {epic_id, title?}
    → service.convert_proposal_to_story()
        1. _proposal_or_404；story_id 幂等短路
        2. 校验 status == converged（否则 400）
        3. 校验 converged_spec 非空（否则 400）
        4. 校验 epic 存在且 epic.project_id == proposal.project_id（否则 404/400）
        5. create_story(title=显式 title 或提案标题, description=converged_spec 原文)
        6. 解析 spec 清单行 → create_task × N（同 project/story, backlog, medium）
        7. 回填 p.story_id + p.status = story_created（单事务）
    → 200 {proposal, story, tasks}
```

## 事务与一致性

- 第 5-7 步在同一 `Session` 内，`_commit` 一次提交 → 要么全部落库，要么全部回滚。
- 并发重放：SQLite 写锁全局串行化 / MariaDB 行锁保证「两个并发 convert 恰好一个创建 Story」；后到者读到 `story_id` 已回填走幂等短路。
- 租约字段：`converged` 态本无租约（租约只在 analyzing 有效），转化时无需维护 claimed_by/claimed_at。

## 安全

- 端点复用 `project_access_middleware`（`/api/proposals/{pid}` 前缀已在白名单解析器中解析到 project_id），私有项目仅成员可写，公开项目写入需成员；系统管理员绕过。
- MCP `proposal_convert` 走既有 `_http` + 服务端鉴权，无新权限面。
