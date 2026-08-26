# Design: Agent Runtime 引入 Worker ↔ AgentInstance 二层模型

## 架构

```
┌─────────────────────┐                ┌─────────────────────┐
│  Worker A           │                │  Worker B           │
│  - worker_id=A      │                │  - worker_id=B      │
│  - 安装: codex       │                │  - 安装: claude     │
│                     │                │                     │
│  AgentInstance:     │                │  AgentInstance:     │
│  (A, codex-agent)   │                │  (B, codex-agent)   │
│  cli=codex          │                │  cli=codex (缺)     │
│  online=true        │                │  online=false       │
└─────────────────────┘                └─────────────────────┘
         │                                      │
         │ heartbeat 探本机                       │ heartbeat 探本机
         │ 只能改 (A, codex-agent)              │ 只能改 (B, codex-agent)
         ▼                                      ▼
┌──────────────────────────────────────────────────────────┐
│                       Server                              │
│  Worker 表: identity of machines                          │
│  AgentInstance 表: (worker_id, agent_id) 局部执行环境      │
│  Agent 表: logical identity (保留 cli_command 兼容)        │
│  Agent.online = ANY(instance.online for that agent)      │
└──────────────────────────────────────────────────────────┘
```

## 表结构

### `workers`
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 |
| worker_id | VARCHAR(64) UNIQUE | 外部自报 ID，如 `wb-prod-1` |
| hostname | VARCHAR(200) | 主机名（best-effort） |
| status | VARCHAR(20) | `active` / `inactive` |
| last_heartbeat | DATETIME | 任意 instance 上次心跳（聚合） |
| created_at | DATETIME | |
| updated_at | DATETIME | |

### `agent_instances`
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| worker_id | VARCHAR(64) FK | |
| agent_id | VARCHAR(64) FK | |
| cli_command | VARCHAR(500) | 本机 CLI 模板（占位符替换） |
| model | VARCHAR(100) | 同 CLI 多 agent 时不同模型 |
| auth_key | VARCHAR(100) | 本机凭据（脱敏返回） |
| enabled | BOOLEAN | |
| online | BOOLEAN | |
| last_heartbeat | DATETIME | |
| last_probe_at | DATETIME | |
| probe_message | VARCHAR(300) | |
| created_at | DATETIME | |
| updated_at | DATETIME | |
| UNIQUE | (worker_id, agent_id) | |

## 关键设计决策

1. **`Worker.worker_id` 用 VARCHAR 而不是外键到 `users.id`**
   - Worker 是机器身份，不是 user；老用户体系是按人来的，Worker 跨用户部署
   - `agent_id` 已经走 `agents.user_id` 关联到人；Worker 自身不需要 user_id

2. **`Agent.cli_command` 保留不删**
   - 向后兼容旧 single-Worker 部署的 `/api/agents/{id}/probe` 路径
   - 数据迁移期由 `AgentInstance(worker_id="default")` 承担新职责
   - 后续可独立 Epic 删 `Agent.cli_command`

3. **`Agent.online` 改为聚合字段**
   - 任一 instance.online == true → Agent.online = true
   - 全部 instance offline → Agent.online = false
   - 在 `instance_heartbeat` / `instance_deregister` 内同步刷新
   - 评审/调度继续用 `Agent.online`（暂不破坏现有逻辑）

4. **Worker ownership 校验放在 service 层**
   - `instance_heartbeat(s, instance_id, caller_worker_id=...)` 内
     `if instance.worker_id != caller_worker_id: raise Forbidden`
   - 不在路由层做 — 路由层只透传 caller_worker_id（从 WorkerConfig 拿）
   - 这样测试不需要 mock HTTP 层

5. **API contract**
   - `GET /api/workers/{worker_id}/instances` 返回**含** `cli_command`（Worker 是 owner）
   - `GET /api/agent-instances?worker_id=X` 也含 `cli_command`（按 worker 过滤 → 当作 owner 视图）
   - `GET /api/agent-instances?agent_id=Y` 脱敏 `cli_command`（跨 worker 视角）
   - `Agent.to_public_dict()` 保持脱敏不变

6. **`agent_heartbeat_once` 双路径**
   - `config.worker_id` 非空 → 走新路径（推荐，多 Worker）
   - 空 → 走旧路径（`GET /api/agents` + 本地探测），保留给 single-Worker 历史部署
   - 旧路径已知问题（list API 不返回 cli_command）不在本 change 修

## 数据迁移（alembic data migration）

```python
# upgrade data migration
op.execute("INSERT INTO workers (worker_id, hostname, status, created_at, updated_at) "
           "VALUES ('default', 'legacy-single-worker', 'active', CURRENT_TIMESTAMP, "
           "CURRENT_TIMESTAMP)")

# 把旧 Agent.cli_command 落成 default instance
op.execute("""
    INSERT INTO agent_instances
        (worker_id, agent_id, cli_command, model, auth_key, enabled, online,
         last_heartbeat, last_probe_at, probe_message, created_at, updated_at)
    SELECT 'default', agent_id, cli_command, model, auth_key, enabled, online,
           last_heartbeat, last_probe_at, probe_message, created_at, updated_at
    FROM agents
    WHERE cli_command != ''
""")
```

downgrade 删 instance + default worker。

## Worker 端 `agent_heartbeat_once` 改写

```python
def agent_heartbeat_once(client, config):
    worker_id = (config.worker_id or "").strip()
    if worker_id:
        return _heartbeat_via_instances(client, config, worker_id)
    # 旧路径：单 Worker 兜底
    return _heartbeat_via_agents_legacy(client, config)

def _heartbeat_via_instances(client, config, worker_id):
    try:
        instances = client.request("GET", f"/api/workers/{worker_id}/instances").json() or []
    except Exception as e:
        log.warning("拉取 Worker %s instances 失败: %s", worker_id, e)
        return {"checked": 0, "online": 0, "offline": 0, "skipped": 0, "worker_id": worker_id}
    stats = {"checked": 0, "online": 0, "offline": 0, "skipped": 0, "worker_id": worker_id}
    for inst in instances or []:
        iid = inst.get("id")
        cmd = inst.get("cli_command") or ""
        if not iid or not cmd or not inst.get("enabled", True):
            stats["skipped"] += 1
            continue
        stats["checked"] += 1
        try:
            ok, msg = probe_cli(config, cmd, model=inst.get("model") or "")
            if ok:
                client.request("POST", f"/api/agent-instances/{iid}/heartbeat",
                               json={"probe_ok": True, "probe_message": msg})
                stats["online"] += 1
            else:
                client.request("POST", f"/api/agent-instances/{iid}/deregister",
                               json={"probe_message": msg})
                stats["offline"] += 1
        except Exception as e:
            log.warning("Instance %s heartbeat 上报失败: %s", iid, e)
    if stats["checked"]:
        log.info("Worker %s 心跳: %s", worker_id, stats)
    return stats
```

## 安全 / 鉴权

- `POST /api/workers/{worker_id}/instances` / `POST /api/agent-instances/{id}/{heartbeat,deregister}`
  都需要鉴权（`_auth_is_required` 软判定），但**不**强制 admin —— Worker 自己的 abk_ key
  即可，key 指纹和 `worker_id` 绑定（轻校验：请求带 `X-Worker-Id` 头与 body 的 worker_id 一致）
- 实际 ownership 校验走 service 层：DB 里 `instance.worker_id != caller_worker_id` → 403

## 复测信号

- 两 worker 同时跑：`docker compose logs worker-A` 和 `worker-B` 看 stats，应**互不触达**
- 故意把 Worker A 上的 `codex` 改名/删除 → A 的 `codex-agent` instance 置 offline，
  Worker B 上的 `codex-agent` instance 仍 online
- `Agent.online` 聚合：A offline + B online = true；A offline + B offline = false
