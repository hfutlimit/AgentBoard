# Tasks — 模式 B：Trigger（Webhook 唤醒 Runner）

**status**: in_review

## Task 1.1 — `WebhookTrigger` 适配器（`agentboard/executor.py` 增量扩展）

- [x] `WebhookTrigger(TriggerAdapter)`：`timeout_seconds=3600`，`name="webhook"`
- [x] `build_payload(run, task, ctx)`：`{event:"agent_run.triggered", timestamp,
      data:{run_id, task_id, project_id, schedule_id, agent, task_title,
      task_spec, prompt, token}}`（token 读 env `AGENTBOARD_TRIGGER_TOKEN`）
- [x] `resolve_url(ctx)`：env `AGENTBOARD_TRIGGER_URL` > `ctx.extra["webhook_url"]`；
      无目标抛 `AdapterError`
- [x] `launch()`：httpx.post（timeout=15s），secret 时附加
      `X-AgentBoard-Signature`（HMAC-SHA256）/`X-AgentBoard-Timestamp`；
      非 2xx 抛 `AdapterError`；返回挂 metadata 的 RunHandle
- [x] `poll_status` 继承 TriggerAdapter 语义（等待外部显式回写）

## Task 1.2 — 注册 + 上下文装配

- [x] `register_adapter` 新增 `preserve_name` 参数（默认 False 向后兼容）
- [x] 注册 `workbuddy` / `qoder` → `WebhookTrigger`（`preserve_name=True` 防覆盖）
- [x] `build_run_context` 扩展：按 project_id 查 enabled `WebhookConfig` 首个，
      写入 `ctx.extra["webhook_url"]` / `["webhook_secret"]`

## Task 1.3 — `trigger_run` 最小单次驱动 + CLI

- [x] `trigger_run(session_factory, run_id, *, poll_interval, max_poll_seconds)`：
      pending → running → launch（webhook POST）→ 轮询 DB run.status
      （外部 report_run_result 回写）→ success/failed/cancelled finalize；
      超时兜底 failed(timeout)
- [x] agent 不在 `TRIGGER_AGENTS`（workbuddy/qoder）→ 返回 None 不误触发
- [x] `trigger_first_pending`：选第一个 pending 且 agent ∈ TRIGGER_AGENTS 的 run
- [x] CLI：`python -m agentboard.executor --trigger <id>` / `--trigger-once`

## Task 1.4 — 单元测试（`tests/test_epic78_story103_trigger.py`）

- [x] 注册表：workbuddy/qoder → WebhookTrigger，可实例化
- [x] build_payload 字段断言（含 token 注入）
- [x] resolve_url 三态：env 优先 / DB 兜底 / 缺失抛错
- [x] 全链路：POST 送达 → 外部回写 success/failed → trigger_run finalize
- [x] 失败路径：无目标 / 非 2xx / 超时 / 非 pending 跳过 / agent 非 Trigger
- [x] 项目级 WebhookConfig 自动发现 + HMAC 签名头验证
- [x] trigger_first_pending 选中 workbuddy run（跳过 codex）
- [x] CLI 子进程冒烟（--trigger）
