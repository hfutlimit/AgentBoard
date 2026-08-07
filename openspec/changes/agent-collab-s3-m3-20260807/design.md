# Design：多数决评审（S3 M3）

> ID: agent-collab-s3-m3-20260807 · Epic 122 / Story 232 / Task 1015
> 上游：文档 #50 §7 决策 #7 / §8 切片 3；S3 M2 design「不含」清单明确列为后续迭代

## 1. 目标与范围

S3 M3 交付切片 3 的「护栏调优·评审强度升级」：多数决评审（N 人投票按多数结算）。

1. `review_votes` 表（迁移 q6r7s8t9u0v1）：一实体多票，一人一票；
2. `service._vote_majority`：投票（upsert）→ 达法定票数结算；
   `_settle_majority_approved` / `_settle_majority_rejected`（CAS）；
3. `review_story` / `review_task` 增加 majority 分支（默认 single 行为不变）；
4. `scan_review_timeouts` 超时兜底结算（票数不足但超时 → 按现有票结，防死锁）；
5. 事件：新增 `review.vote_cast`（mq.py 白名单 + api 结算判定 + worker 日志分支）。

**不含**：SLA 报表导出、阈值按实体粒度配置（后续迭代）。

## 2. 配置（环境变量，服务端统一）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENTBOARD_REVIEW_MODE` | `single` | `single`（1 评审人 approve 即过）/ `majority`（多数决）；非法值回退 single |
| `AGENTBOARD_REVIEW_QUORUM` | 3 | 法定票数（2..9）；非法/缺省回退 3 |

- `service.get_review_mode()` / `service.get_review_quorum()` 读取；
- 单测经 monkeypatch.setenv 切换，无需重启服务。

## 3. 数据模型（迁移 q6r7s8t9u0v1）

`review_votes`：

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| entity_type | String(10) | NOT NULL | story \| task |
| entity_id | Integer | NOT NULL | Story/Task 主键 |
| reviewer_user_id | Integer | FK users NOT NULL | 投票人 |
| verdict | String(10) | NOT NULL | approve \| reject |
| comment_id | Integer | FK comments NULL | 评审意见载体 |
| round | Integer | default 0 | 所属评审轮 |
| created_at / updated_at | DateTime | default/onupdate | |
| UNIQUE(entity_type, entity_id, reviewer_user_id) | | | 一人一票（upsert 改票） |

索引：`ix_review_votes_entity (entity_type, entity_id)`。

## 4. 多数决流程（CAS）

```
review_story / review_task（majority 模式）
  ├─ 权限：投票人 ∈ 项目在线 reviewer 候选（_is_reviewer_candidate，与分配器同源）；
  │        Task 版额外 ≠ assignee（评审人/作者隔离）
  ├─ 状态校验：Story=pending_review / Task=in_review
  ├─ 评论落库（评审意见唯一载体，与 single 一致）
  ├─ _upsert_review_vote（一人一票，改票覆盖）
  ├─ _review_vote_counts → (approve, reject)
  ├─ approve+reject < quorum → 不结算，状态保持（返回 settled=False）
  └─ approve+reject >= quorum：
        ├─ approve > reject → _settle_majority_approved
        │     Story: pending_review → ready
        │     Task:  in_review → done
        └─ reject >= approve（含平局保守驳回）→ _settle_majority_rejected
              Story: round+1 → pending_review（达 MAX_REVIEW_ROUNDS → blocked）
              Task:  round+1 → in_progress（达上限 → blocked）
```

- 结算 CAS：条件 UPDATE 匹配当前状态（pending_review / in_review），rowcount=1；
- 结算后清票（终态 / 驳回开新一轮，MVP 简化不跨轮保留历史票，评论已留存审计）；
- 平局语义：达 quorum 且 approve == reject → 按驳回处理（评审未达成一致，
  退回修复/收敛再投），与超时兜底语义一致，避免无限挂起。

## 5. 超时兜底（scan_review_timeouts 扩展）

majority 模式下超时未决实体（最后活动超时）：

```
票数 > 0 →
    approve > reject → _settle_majority_approved → stories/tasks_settled +1
    reject >= approve → _settle_majority_rejected → settled +1（达上限额外 blocked +1）
票数 == 0 → 走既有重派逻辑（解绑 → 重新指派）
```

- result 新增 `stories_settled` / `tasks_settled` 字段（向后兼容，响应剔除 `_` 内部键）；
- 平局超时保守驳回：防死锁优先级高于"等待更多票"。

## 6. API 事件适配

`POST /api/stories/{sid}/review` / `POST /api/tasks/{tid}/review` 结算判定
（调用前记录 before_round，调用后比较）：

| 调用后状态 | 事件 | 语义 |
|---|---|---|
| ready / done | story.ready / task.reviewed | 多数通过（或 single approve） |
| blocked | review.rejected / task.rejected | 护栏终态 |
| round 增加（状态保持） | review.rejected / task.rejected | single reject / 多数驳回待收敛 |
| round 不变、状态不变 | **review.vote_cast**（ref_id=投票人） | 投票已记录，等待更多票 |

- `review.vote_cast` 加入 mq.WORKFLOW_EVENTS 白名单；
- Webhook 通道同步使用判定后事件名（event 语义与 MQ 同构）；
- worker：vote_cast → 日志（多数决进行中，结算由投票/超时触发）。

## 7. 兼容与安全

- 默认 single：`get_review_mode()` 回退 single → review_story/review_task 走
  既有分支，逐字节行为不变，S1/S2 测试零回归；
- 零新增依赖；迁移纯增量（create_table，SQLite/MariaDB 均支持）；
- 权限收紧方向正确：majority 投票人资格与分配器候选集同源，未扩大攻击面；
- MCP 工具 review_story / review_task 签名不变，语义随模式自动升级。

## 8. 测试策略

`tests/test_epic122_s3m3.py`：

1. 配置：get_review_mode / get_review_quorum（默认值 + env 覆盖 + 非法回退）；
2. majority Story：3 票 2 approve → 结算 ready + 清票 + 评论；未达 quorum 状态保持；
3. majority Task：3 票 2 reject → 结算 in_progress + round+1（或 blocked 上限）；
4. 一人一票：同 reviewer 改票（upsert 覆盖，不重复计数）；
5. 平局：quorum=2 时 1:1 → 保守驳回；
6. 超时兜底：majority + 票数不足超时 → 按现有票结算（approve 多数通过 / 平局驳回）；
   零票超时 → 走重派（stories_reassigned）；
7. single 兼容：mode=single 时 review_story/review_task 走既有逻辑（断言 reviewer_id
   匹配校验仍生效）；
8. 权限：非 reviewer 候选（离线/无 reviewer 角色/非成员/Task 的 assignee）投票被拒；
9. api：投票未结算 → publish vote_cast；结算 approve → ready/reviewed；
   结算 reject → rejected（mock publish 断言事件名与 ref_id）；
10. Epic 97 AST 护栏：mcp_server.py 零 `_api(` 残留；
11. 既有 epic122 全系回归零失败。
