# Epic 78 · Story 103 — 模式 B：Trigger（Webhook 唤醒 Runner）

**status**: in_review
**date**: 2026-08-04

## 问题

Story 101/102 交付了适配器框架与模式 A（Launcher：直接 spawn CLI Agent 子进程）。
但常驻 Runner 场景（WorkBuddy / QoderWork 自动化）仍是「每 N 分钟轮询 story 领任务」：
Runner 主动全量 `list_tasks` 扫描，找不到活就空转，找到活才执行——这是 pull 而非 push。

Epic 78 的目标是「执行器把任务主动推给 Agent」。模式 B（Trigger）面向常驻 Runner：
**执行器把 pending run 打包成事件 POST webhook 唤醒 Runner，Runner 被叫醒后直奔
指定 task_id 执行，不再全量轮询**。

## 目标

1. `WebhookTrigger(TriggerAdapter)` 具体适配器：把 pending run 组装成事件负载
   （`event + run_id + task_id + project_id + agent + task_title + prompt + token`）
   POST 到目标 URL；配置了 secret 时附加 HMAC-SHA256 签名头（与既有
   `fire_webhook` 签名模式一致）。
2. 目标 URL 来源优先级：env `AGENTBOARD_TRIGGER_URL` > 项目级 `WebhookConfig`
   （复用既有 `create_webhook` 基础设施，取 enabled 第一个）。
3. 注册进 `ADAPTERS`：`workbuddy` / `qoder` 两个常驻 Runner 名共享
   `WebhookTrigger`（`preserve_name=True` 防止别名覆盖类逻辑名）。
4. 最小单次驱动 `trigger_run(session_factory, run_id)`：pending → running →
   POST webhook → 轮询 DB `run.status`（外部经 `report_run_result` 回写，
   Story 104 落地 MCP 工具）→ success/failed 或超时兜底。
5. CLI 入口 `python -m agentboard.executor --trigger <id>` / `--trigger-once`。

## 非目标（后续 Change 承接）

- `report_run_result` MCP 工具本身（Story 104）；
- 执行器 daemon 主循环（并发认领 / 租约续期 / 后台轮询）（Story 104）；
- `AgentSchedule` 绑定松绑（项目/Agent 级 + 筛选）（Story 106）。

## 验收

- `workbuddy` / `qoder` 经 `get_adapter` 取回 `WebhookTrigger`；
- 触发一次 run：webhook 送达（payload 含 task_id 等直取字段），外部回写
  success/failed 后 `trigger_run` 正确 finalize；
- 无目标 URL / 非 2xx / 超时均有可读失败记录，不裸崩；
- 既有 WorkBuddy/QoderWork 自动化逻辑不动，仅触发源改为 AgentBoard 事件。
