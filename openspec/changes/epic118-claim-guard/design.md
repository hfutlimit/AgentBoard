# Design — Agent 认领并发护栏（Epic 118）

## 决策点

| 决策 | 选择 | 备选 | 理由 |
|------|------|------|------|
| 护栏实现层 | mcp_server.py（MCP 工具内） | service.py / api.py（REST 层） | 只改工具行为即可达成；服务端零改动则零契约变更、零回归面；符合 Epic 118「工具硬化」定位 |
| 占用判定状态集 | 非 backlog/todo 即拒绝 | 仅拒绝 in_progress | backlog 默认未开工；todo 已排期未开工仍可认领；其余状态（in_progress/in_review/verifying/done）均视为不可认领，语义最紧 |
| Run 复用范围 | 同 task + active（pending/running） | 同 task 任意 Run | 终态 Run 是历史完成记录，复用无意义；active Run 代表「该任务正在被执行」，复用避免重复执行 |
| schedule 硬编码 | 保留 1 | 改为参数 | 历史约定：schedule 1 = 手动触发占位；扩大改动面需动 REST 契约与文档，超出本 Epic 范围 |

## 关键流程（claim_task 新语义）

```
claim_task(task_id, agent_name)
  ├─ GET /api/tasks/{id} ── error? ────────────────→ 透传 error
  ├─ status ∉ {backlog, todo} ────────────────────→ {error: already claimed, task, run: null}
  ├─ GET /api/schedules/1/runs
  │    └─ 存在同 task 的 pending/running Run ─────→ 复用 {run, reused: true} + PUT in_progress
  ├─ POST /api/schedules/1/runs ── error? ────────→ {error, task, run: null}
  └─ PUT /api/tasks/{id}/status = in_progress
       └─ GET /api/tasks/{id} 刷新 → {run, task}
```

## 测试设计

- 单测（mock `ms._http` 序列驱动，9 用例）：
  1. 空闲 backlog → 创建 Run + 推进 in_progress（校验调用序列与 idempotency_key 前缀）
  2. in_progress → error，零 POST/PUT
  3. done / in_review / verifying → error
  4. 已有 running Run → 复用（reused=True，无 POST）
  5. 已有终态 Run → 不复用，新建
  6. GET task error → 透传
  7. create run error → error，不推进状态
  8. AST 静态护栏：`_agent_claim_task` 无 `if False`/恒假分支
  9. FastMCP 注册验证：claim_task / heartbeat 在 list_tools 中
