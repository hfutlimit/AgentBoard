# Design：Task 评审闭环（S2 M2）

> ID: agent-collab-s2-m2-20260807 · Epic 122 / Story 231 / Task 1012 · 参照文档 #50 §4-§5、#51 §3-§6

## 1. 数据模型（tasks 表增量）

| 列 | 类型 | 说明 |
|---|---|---|
| `reviewer_id` | FK users, nullable, index | 被指派评审人（CAS 回填；reject 退回后保留，不换人） |
| `review_round` | int, default 0 | 评审轮次计数（护栏：达 `MAX_REVIEW_ROUNDS=5` → blocked） |

与 S1 Story 评审列完全对齐。迁移 `p5q6r7s8t9u0_add_tasks_review_fields`（纯增量 add_column，
SQLite/MariaDB 均直接支持，无需 batch/重建表；Task CHECK 约束不动 → Task 状态机零污染）。

## 2. 状态机（Task 既有状态，无新增枚举）

```
backlog/todo --claim(CAS)--> in_progress --submit-review--> in_review
in_review --assign(CAS)--> in_review(+reviewer_id)
in_review --review approve(CAS)--> done
in_review --review reject(CAS)--> in_progress(+review_round) --submit-review--> in_review(复用同 reviewer)
in_review --review reject 达5轮--> blocked（护栏）
```

- 复用既有 `Status` 枚举与 `set_status` TRANSITIONS（approve/reject 走条件 UPDATE 直写，
  不依赖常规迁移表，与 review_story 同构）；
- reject 的 `in_review→in_progress` 本就在 TRANSITIONS 内，二次 submit-review 合法。

## 3. service.py

### `assign_task_reviewer(s, task_id, *, user_id=None, is_admin=False) -> Task`
1. 幂等短路：`reviewer_id` 非空 → 返回现态；
2. 校验 `status == in_review`（非评审态不可指派）→ InvalidValue；
3. 候选 = `_online_reviewer_candidates(s, project_id)`（在线 ∩ 角色 reviewer ∩ 项目成员）
   过滤 `user_id != assignee_id`（评审人 ≠ 作者）；
4. 无候选 → InvalidValue("no online reviewer available ...")；
5. CAS：`UPDATE tasks SET reviewer_id=候选 WHERE id=task_id AND reviewer_id IS NULL
   AND status='in_review'`；rowcount==1 → commit；否则 rollback 回查现态（并发后到者幂等）。

### `review_task(s, *, task_id, reviewer_user_id, verdict, comment) -> Task`
1. 校验：verdict ∈ {approve, reject}；comment 必填（trim 后非空）；
   `reviewer_id == reviewer_user_id`；`status == in_review`；
2. approve：CAS `UPDATE ... SET status='done' WHERE reviewer_id=? AND status='in_review'`；
3. reject：`new_round = review_round + 1`；target = `blocked` if new_round >= 5 else `in_progress`；
   CAS `SET review_round=new_round, status=target`；
4. 评审意见落评论（`create_comment(author=reviewer 显示名, task_id=...)`，唯一载体）；
5. 清项目统计缓存。

### `list_task_review_tasks(s, user_id, *, status=None)`
`WHERE reviewer_id == user_id`（可选 status 过滤），按 `status desc, id desc` 排序
（in_review 优先于 done/blocked —— in_review 字典序大于 done，同 S1 Story 语义）。

### `search_tasks` 扩展
新增 keyword-only `reviewer_id: int | None` 过滤（API 层把 me/int 解析为 int）。

## 4. api.py

| 端点 | 行为 |
|---|---|
| `POST /api/tasks/{tid}/assign-reviewer` | 指派（幂等）；成功 → `publish_workflow_event(EVENT_REVIEW_REQUESTED, "task", tid, ref_id=reviewer_id, agent_id=绑定的Agent)`；404/422 映射 |
| `POST /api/tasks/{tid}/review` | body 复用 `AgentReviewIn`(verdict/comment)；approve → `EVENT_TASK_REVIEWED`；reject → `EVENT_TASK_REJECTED`(ref_id=round)；404/422 映射 |
| `GET /api/tasks?reviewer_id=me\|int` | 新增过滤参数（me 解析当前登录用户，未登录 422） |

- 项目级路由自动被 `project_access_middleware` 覆盖（写权限 → 成员）；
- 事件源复用 `publish_workflow_event`（best-effort 不抛异常）。

## 5. workflow_worker.py

- `_assign_task_reviewer(task_id)`：POST assign-reviewer；200/201 → info；404 → 忽略；
  422（无 reviewer/非 in_review）→ warn + True（ack，轮询兜底）；网络异常 → False（重投）；
- `handle_message`: `EVENT_TASK_READY_FOR_REVIEW` → `_assign_task_reviewer(entity_id)`；
  `EVENT_TASK_REVIEWED/EVENT_TASK_REJECTED` → 日志（assignee/reviewer 经定向队列感知）；
- `run_poll_once` 追加：`GET /api/tasks?status=in_review` → 逐条跳过已指派、指派未指派
  （MQ 未配置时兜底，与 Story 轮询并存）。

## 6. mcp_server.py

| 工具 | REST |
|---|---|
| `assign_task_reviewer(task_id)` | `POST /api/tasks/{tid}/assign-reviewer` |
| `review_task(task_id, verdict, comment)` | `POST /api/tasks/{tid}/review` |
| `list_task_review_tasks(status=None)` | `GET /api/tasks?reviewer_id=me[&status=]` |

全部走 `_http`（零 `_api` 残留），服务端 token 身份即 reviewer。

## 7. 权限边界

- 指派/评审均为项目写操作 → project_access_middleware（成员+以上）；
- 评审动作本身校验「仅被指派 reviewer」→ 服务层 CAS + reviewer_id 匹配双护栏；
- `reviewer_id=me` 查询天然用户隔离（未登录 422）。

## 8. 兼容性

- Task 状态机/CHECK/`_ser` 序列化零变更（新列随模型自动序列化）；
- `GET /api/tasks` 既有调用方（前端搜索、worker、MCP）不受新可选参数影响；
- S1 的 Story 评审链路零改动；Epic 96 proposal 转化链路零影响。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 随机指派导致测试/链路不可预期 | 服务层 CAS 恰一赢家；测试固定 reviewer_id 断言 |
| 无在线 reviewer 断链 | assign 422 + worker 轮询兜底 + 开发者轮询 `list_task_review_tasks` |
| reject 循环不收敛 | review_round 5 轮 → blocked 护栏（与 Story/Proposal 对齐） |
| 18001 MCP 容器旧代码 | 零容器依赖；工具经 REST 调用（部署后生效） |
