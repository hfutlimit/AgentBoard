# Design：评审统计与超时护栏（S3 M2）

> ID: agent-collab-s3-m2-20260807 · Epic 122 / Story 232 / Task 1014
> 上游：文档 #50「多 Agent 自动协作闭环：需求与方案」§8 切片 3

## 1. 目标与范围

S3 M2 交付切片 3 的「评审统计运营视图」与「护栏调优·超时重派」：

1. `get_review_stats`：项目级评审统计（总量 / 通过 / 驳回 / 进行中 / 阻塞、平均轮次、
   驳回率、超时未决数、按 reviewer 聚合工作量），`days` / `user_id` 过滤；
2. `scan_review_timeouts`：超时评审任务自愈 —— 轮次上限 → `blocked`；否则解绑重派；
3. `GET /api/review-stats` + `POST /api/review-stats/reassign-timeout`；
4. `workflow_worker` 轮询自动触发超时扫描；
5. MCP `get_review_stats` / `scan_review_timeouts`。

**不含**：多数决评审、SLA 报表导出、阈值按实体粒度配置（后续迭代）。

## 2. 超时定义（零迁移）

| 实体 | 状态约束 | 最后活动指标 | 说明 |
|---|---|---|---|
| Story | `pending_review` 且 `reviewer_id` 非空 | `max(created_at, 最新 story 评论 created_at)` | Story 无 updated_at；评论是评审意见唯一载体，评论往返即活动 |
| Task | `in_review` 且 `reviewer_id` 非空 | `updated_at` | Task 已有 updated_at 列 |

阈值：`timeout_minutes`（默认 30），`now - last_activity > timeout` 判定超时。
`now` 可注入（测试用），默认 `utc_now()`。

## 3. 重派动作序列（并发安全）

```
scan_review_timeouts(s, *, timeout_minutes=30, max_per_run=20, now=None)
  ├─ 候选列表：pending_review Story（最后活动超时）→ 逐个处理
  │     ├─ review_round >= MAX_REVIEW_ROUNDS → status=blocked（护栏终态）→ blocked++
  │     └─ 否则：CAS 解绑（UPDATE SET reviewer_id=NULL WHERE id=? AND reviewer_id=旧值，
  │           rowcount=1 才继续）→ 重新随机指派
  │             ├─ 候选非空且 ≠ 旧 reviewer → 指派成功 → stories_reassigned++
  │             └─ 候选为空 / 只剩旧 reviewer → 保持解绑 → no_candidate++（下轮补派）
  └─ 候选列表：in_review Task（updated_at 超时）→ 同上（Task 版）
```

- 解绑与重派之间**不提交中间态**？否 —— 解绑即提交（否则重派失败会带死 reviewer
  卡住）。设计为：CAS 解绑成功 → 立即调 `assign_reviewer` / `assign_task_reviewer`
  重新指派；后者失败（无候选）时 reviewer 已解绑，由下轮轮询补派，正确性不变；
- **防并发重派**：解绑 CAS 条件带旧 reviewer_id，两个 worker 同时扫描时恰一赢家
  （rowcount=1），败者跳过；
- 重派的目标 reviewer 从 `_online_reviewer_candidates` 随机取，**排除旧 reviewer**
  （避免 A 失联又派回 A）；Task 版额外排除 assignee（复用既有语义）。

## 4. 统计口径（get_review_stats）

```
{
  "project_id": N,
  "days": 7,
  "stories":  { "total", "approved", "rejected", "pending", "blocked" },
  "tasks":    { "total", "approved", "rejected", "pending", "blocked" },
  "rounds":   { "avg_story_round", "avg_task_round" },
  "reject_rate": 0.0,          # rejected / (approved + rejected)，分母为 0 → 0.0
  "timeout_pending": 0,        # 当前超时未决数（stories + tasks，按默认 30min）
  "by_reviewer": [ { "user_id", "name", "story_reviewed", "task_reviewed",
                     "story_approved", "story_rejected", "task_approved", "task_rejected" } ],
  "generated_at": "..."
}
```

判定规则：

| 指标 | 口径 |
|---|---|
| story approved | status=ready 且 reviewer_id 非空 |
| story rejected | review_round > 0（产生过驳回往返） |
| story pending | status=pending_review |
| story blocked | status=blocked |
| task approved | status=done 且 reviewer_id 非空 |
| task rejected | review_round > 0 |
| task pending | status=in_review |
| task blocked | status=blocked |
| avg_story_round | 有评审记录（reviewer_id 非空或 round>0）Story 的 review_round 均值 |
| by_reviewer | 按 reviewer_id 分组（非空），聚合上述 approved/rejected/总量 |

`days` 过滤：`created_at >= now - timedelta(days=days)`（Story / Task 各自过滤）。
`user_id` 过滤：仅统计该 reviewer 参与的条目（reviewer_id == user_id）。

## 5. REST 端点

| 方法 | 路径 | 参数 | 权限 | 说明 |
|---|---|---|---|---|
| GET | `/api/review-stats` | `project_id`(必填) `days`=7 `user_id` | 项目成员可读（project_access_middleware 覆盖，公开项目读开放） | 评审统计运营视图 |
| POST | `/api/review-stats/reassign-timeout` | body `{project_id, timeout_minutes=30, max_per_run=20}` | 项目成员写 | 触发超时重派扫描；返回统计；重派成功发布 `review.requested`（定向 reviewer agent 队列退广播）+ Webhook 通道 |

POST 事件发布：每个重派成功的实体发布 `EVENT_REVIEW_REQUESTED`（entity_type=story/task，
ref_id=新 reviewer_id，agent_id=新 reviewer 绑定 Agent 的 agent_id —— 定向退广播），
与既有 `assign-reviewer` 端点语义一致；Webhook 通道 `_notify_webhooks` 并行。

## 6. workflow_worker 集成

`run_poll_once` 末尾追加（best-effort）：

```python
# 切片 3 M2：超时重派扫描（幂等，服务端 CAS 仲裁）
try:
    r = self._request("POST", "/api/review-stats/reassign-timeout",
                      json={"timeout_minutes": 30, "max_per_run": 20})
    if r.status_code in (200, 201):
        log.info("超时重派扫描：%s", r.json())
except Exception as e:
    log.warning("超时重派扫描失败（网络异常，下轮重试）：%s", e)
```

## 7. MCP 工具

| 工具 | 签名 | 说明 |
|---|---|---|
| get_review_stats | (project_id, days=7, user_id=None) | `_http GET /api/review-stats` |
| scan_review_timeouts | (project_id, timeout_minutes=30, max_per_run=20) | `_http POST /api/review-stats/reassign-timeout` |

沿用 `_http(method, path)` 模式（路径带 /api），身份经服务端 token 解析。

## 8. 兼容与安全

- 零 DB 迁移（最后活动指标用现有列/评论时间推导）；
- 零既有契约破坏：新增端点与函数，不动既有评审端点语义；
- `scan_review_timeouts` 幂等：CAS 解绑仲裁，重复调用无副作用；
- `max_per_run` 有界（默认 20），防止单轮扫描长时间独占；
- 无在线 reviewer 时解绑不指派，由轮询兜底 —— 评审流不会因重派失败而卡死。

## 9. 测试策略

`tests/test_epic122_s3m2.py`：

1. **统计口径**：空项目全零；构造 approve/reject/blocked 样本后计数正确；days 过滤
   （老数据不计入）；user_id 过滤；reject_rate 与 avg round 计算；
2. **超时重派**：超时 Story 换 reviewer（≠旧 reviewer）、review_round 不变；轮次达
   上限 → blocked；未超时不处理；无候选 → 解绑 + no_candidate；Task 用 updated_at
   判定；max_per_run 有界；
3. **API**：GET 权限（成员 200 / 非成员 403 / 匿名 401）；POST 触发后事件与 Webhook
   断言（mock publish + _notify_webhooks）；
4. **MCP**：AST 注册 + 真实栈直调（设 AGENTBOARD_MCP_TOKEN + ms.API_URL）；
5. **Epic 97 AST 护栏**：mcp_server.py 零 `_api(` 残留。
