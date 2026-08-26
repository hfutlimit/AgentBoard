# Tasks: Agent Runtime 引入 Worker ↔ AgentInstance 二层模型

## 1. 数据模型

- [ ] alembic 迁移 `add_workers_and_agent_instances`：
  - 建 `workers` 表（id, worker_id UNIQUE, hostname, status, last_heartbeat, created_at, updated_at）
  - 建 `agent_instances` 表（id, worker_id, agent_id, cli_command, model, auth_key,
    enabled, online, last_heartbeat, last_probe_at, probe_message, created_at, updated_at）
  - 唯一约束 `(worker_id, agent_id)`
  - 数据迁移：插入 `Worker(worker_id="default", hostname="legacy", status="active")`，
    对每个 `Agent.cli_command != ""` 的旧记录插一条 `AgentInstance(worker_id="default",
    agent_id=existing.agent_id, cli_command=existing.cli_command, model=existing.model,
    online=existing.online, last_heartbeat=existing.last_heartbeat, probe_message=
    existing.probe_message, last_probe_at=existing.last_probe_at)`
- [ ] `models.py` 新增 `Worker` 和 `AgentInstance` 类
- [ ] `AgentInstance.to_public_dict()` 脱敏 `auth_key`（保留 `cli_command` —— Worker 是 owner）

## 2. Service 层

- [ ] `register_worker(s, worker_id, hostname, user_id)` —— 幂等 upsert Worker
- [ ] `list_workers(s)` —— admin 视角
- [ ] `upsert_agent_instance(s, worker_id, agent_id, cli_command, model, auth_key,
  enabled=True, user_id=None)` —— Worker 在本机挂载/更新 instance
- [ ] `list_agent_instances(s, worker_id=None, agent_id=None)` —— 过滤
- [ ] `get_agent_instance(s, instance_id)` —— 拿一条
- [ ] `delete_agent_instance(s, instance_id, user_id, is_admin)` —— admin/owner
- [ ] `instance_heartbeat(s, instance_id, probe_ok, probe_message, caller_worker_id)` ——
  校验 `instance.worker_id == caller_worker_id`（防 A 改 B），落 `online` / `probe_message`，
  同步聚合 `Agent.online = any(instance.online)`
- [ ] `instance_deregister(s, instance_id, probe_message, caller_worker_id)` —— 同上
  校验，置 `online=False`

## 3. 路由层（`features/scheduling/router.py`）

- [ ] `POST /api/workers/register` —— Worker 启动时自报
- [ ] `GET /api/workers` —— admin
- [ ] `GET /api/workers/{worker_id}/instances` —— Worker 拿本机 instances
  （含 `cli_command`，因为是 owner）
- [ ] `POST /api/agents/{agent_id}/instances` —— Worker 给某个 agent 挂本机 instance
- [ ] `GET /api/agent-instances?worker_id=&agent_id=` —— 通用列表
- [ ] `GET /api/agent-instances/{id}` —— 单条
- [ ] `DELETE /api/agent-instances/{id}` —— admin 删
- [ ] `POST /api/agent-instances/{id}/heartbeat` —— Worker 上报探测结果
  （**强制**校验 worker ownership）
- [ ] `POST /api/agent-instances/{id}/deregister` —— 同上

## 4. Worker 侧（`agent_runtime/heartbeat.py` + `config.py`）

- [ ] `WorkerConfig.worker_id: str = ""`（env `AGENTBOARD_WORKER_ID`）
- [ ] 重写 `agent_heartbeat_once`：先看 `config.worker_id`：
  - 非空 → 走 `GET /api/workers/{worker_id}/instances`，逐 instance 探测，
    上报 `/api/agent-instances/{id}/heartbeat` 或 `/deregister`
  - 空 → 走旧 `GET /api/agents` 路径（标记为 deprecated，不修内部数据脱敏问题）
- [ ] `WorkerConfig.from_env()` 读 `AGENTBOARD_WORKER_ID` 环境变量

## 5. 测试

- [ ] `test_worker_instance_isolation.py`（两 Worker 互不影响回归）：
  - 两个 worker 各自 register + 给同一 logical agent 挂 instance
  - Worker A 的 `cli_command` 探测失败 → A 的 instance offline，**B 的 instance online 不变**
  - `Agent.online` 聚合正确（A offline 但 B online → `Agent.online == true`）
  - A 想 deregister B 的 instance → 403，状态不变
- [ ] 更新 `test_worker_heartbeat.py`：
  - 加 `worker_id` config 路径测试
  - 加 `agent_heartbeat_once` 走新路径的 mock client 测试
- [ ] `test_agent_instance_migration.py`：
  - alembic 迁移能跑（forward + backfill + backward）
  - 旧 `Agent.cli_command` 数据落到 `AgentInstance(worker_id="default")`

## 6. 验证

- [ ] `pytest tests/test_worker_instance_isolation.py -v` 全绿
- [ ] `pytest tests/test_worker_heartbeat.py -v` 全绿
- [ ] `pytest tests/test_agent_instance_migration.py -v` 全绿
- [ ] 现有 `pytest tests/test_agent_public_dict.py` 仍绿（向后兼容）

## 状态流

- [ ] Task 1（迁移 + 模型）→ in_review
- [ ] Task 2（service）→ in_review
- [ ] Task 3（路由）→ in_review
- [ ] Task 4（Worker 侧）→ in_review
- [ ] Task 5（测试）→ in_review
- [ ] Task 6（验证）→ in_review
