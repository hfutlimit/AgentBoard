# Design：Webhook 事件接入（S3 M1）

> ID: agent-collab-s3-m1-20260807 · Epic 122 / Story 232 / Task 1013 · 参照文档 #50 §5-§6、§8 切片 3

## 1. 数据模型

**无迁移、无表结构变更。** 复用既有 `WebhookConfig`（`domains/work_items/models.py`）：

| 列 | 类型 | 说明 |
|---|---|---|
| `project_id` | FK projects | 项目级订阅范围 |
| `enabled` | bool | 开关（toggle_webhook） |
| `events` | Text(JSON) | 订阅事件列表；**空列表 = 订阅全部事件**，非空 = 精确匹配 |

## 2. service.py：`fire_webhooks_for_event`

```python
def fire_webhooks_for_event(s, *, project_id, event, payload=None) -> dict:
    """按事件向项目 Webhook 派发（best-effort，不抛异常）。"""
```

流程：
1. 查询 `WebhookConfig WHERE project_id = ? AND enabled = true`；
2. `json.loads(wh.events or "[]")` 解析订阅列表；`subscribed and event not in subscribed`
   → 跳过（空列表 = 订阅全部）；
3. 命中 → `fire_webhook(wh, event, payload)`（复用既有 HMAC-SHA256 签名，10s 超时）；
   单 webhook try/except 隔离（返回 False 与抛异常均计失败，不阻断其它）；
4. 返回 `{"matched": 命中数, "succeeded": 2xx 成功数}`；DB 查询异常也吞掉返回全零。

## 3. api.py：`_notify_webhooks` + 事件点接入

```python
def _notify_webhooks(s, project_id, event, payload) -> dict:
    """best-effort：任何异常返回 {matched:0, succeeded:0}，绝不阻断主业务。"""
```

接入点（与 `publish_workflow_event` 平行调用，事件名复用 `mq.EVENT_*` 常量）：

| 端点 | Webhook 事件 | payload（定位信息） |
|---|---|---|
| `POST /api/epics/{eid}/stories` | `story.created` | id / epic_id / title / status |
| `POST /api/stories/{sid}/assign-reviewer` | `review.requested` | id / reviewer_id / status |
| `POST /api/stories/{sid}/review` approve | `story.ready` | id / status / reviewer_id / review_round |
| `POST /api/stories/{sid}/review` reject | `review.rejected` | 同上（review_round 递增） |
| `POST /api/stories/{sid}/comments` | `comment.replied` | id / comment_id / by |
| `POST /api/tasks/{tid}/submit-review` | `task.ready_for_review` | id / assignee_id / status |
| `POST /api/tasks/{tid}/assign-reviewer` | `review.requested`(task) | id / reviewer_id / status |
| `POST /api/tasks/{tid}/review` approve | `task.reviewed` | id / status / reviewer_id / review_round |
| `POST /api/tasks/{tid}/review` reject | `task.rejected` | 同上（review_round 递增） |

注意：**Story 无 `project_id` 列** → 事件点经 `s.get(Epic, st.epic_id).project_id` 解析
（避免造表迁移）；Task 直接有 `project_id`。

## 4. 事件语义与铁律

- Webhook 事件名 = `mq.EVENT_*` 常量（与 RabbitMQ workflow 事件同构）；
- payload 只带定位信息，状态一律以 DB 为准（与事件总线铁律一致）；
- 双通道平行：MQ 面向 Agent、Webhook 面向外部系统，互不替代、互不阻塞；
- Webhook 派发同步执行（单发 10s 超时），MVP 量级（项目级 webhook 个位数）可接受；
  重试/死信/异步队列留 M2。

## 5. 兼容与回退

- 无 Webhook 配置 → `{matched:0, succeeded:0}`，业务零影响；
- Webhook 全挂（网络/DB 异常）→ best-effort 双保险，主业务照常成功；
- 既有 `create_webhook / list_webhooks / delete_webhook / toggle_webhook` REST/MCP
  契约零改动；`fire_webhook` 签名零改动。

## 6. 测试策略

- service 层：mock `service.fire_webhook` 断言过滤语义与统计；
- API 层：TestClient + mock `api._notify_webhooks` 断言事件名 / project_id / payload；
  mock `service.fire_webhooks_for_event` 抛异常断言主业务不受影响；
- MCP AST 护栏：Epic 97 零 `_api(` 残留；webhook 4 工具注册断言（复用，不新增）。
