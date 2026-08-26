# Proposal: Agent Runtime 引入 Worker ↔ AgentInstance 二层模型

## 背景

`AgentBoard/src/backend-fastapi/agentboard/agent_runtime/heartbeat.py::agent_heartbeat_once` 当前通过
`GET /api/agents` 拉全表，再用本机 `subprocess.run(<cli> --version)` 逐个探测；失败就调
`/api/agents/{id}/deregister`。两层问题：

1. **没有 worker 隔离**。`Agent` 表的 `cli_command` / `online` / `probe_message` 是全局字段；
   Worker A 在本机跑 `claude --version` 失败，会把全局 `claude` agent 置 offline，
   Worker B 的健康 `claude` agent 也会被一并打掉。两台机器场景必然互殴。
2. **API contract mismatch**。`Agent.to_public_dict()` 主动脱敏 `cli_command`（档 A 阻断级
   修复 2026-08-20），但 `heartbeat_once` 又在 `GET /api/agents` 上读 `cli_command` 字段；
   一旦 list 走 `to_public_dict`（生产路径），所有 agent 都会被 `skipped`。

## 目标

把"逻辑 Agent 身份"和"Worker 上的可执行 CLI 实例"解耦到两张表：

- `Worker` —— 物理/虚拟机器（机器身份、本机状态、最后心跳）。
- `AgentInstance` —— 逻辑 Agent 在某个 Worker 上的实例（`cli_command` / `model` /
  `online` / `probe_message` 等本机状态）。

`Agent` 表保留为逻辑身份（`agent_id` / `name` / `roles` / `capabilities` / `user_id` / `enabled`），
不再存 `cli_command` / `online` / `probe_message` 等本机状态（迁移到 `AgentInstance`，
老的字段保留一段时间以兼容单 Worker 部署）。

`heartbeat_once` 改走新路径：Worker 只探测本机的 `AgentInstance`，**绝不**触达其他 Worker。

## 非目标

- 不改 Agent 评审/调度（`rank_agents_for_task` 等仍按 `Agent.online`）。
  短期保留 `Agent.online` 作为「至少一个 instance 在线」的聚合字段，
  由 instance heartbeat/deregister 触发同步。
- 不引入跨 Worker 的任务路由/调度（独立 Epic）。
- 不动前端 UI（只暴露新 API，旧 API 仍可用）。

## 为什么

`Agent = logical agent + local executable instance` 两个概念混在一张表里，是当前所有多 Worker
问题的根因。`worker_id` 补丁只是延后症状：同一 CLI 多机器部署、同一机器多 agent 共用 CLI、
多 agent 跨机器 HA、容量控制这些场景都需要 Worker / Instance 二层抽象。趁 AgentBoard
尚未大规模部署，直接做对。

## 风险

- **数据迁移**：现有 `Agent.cli_command` 数据需要落到 `AgentInstance(worker_id="default")`。
  通过 alembic data migration 做，不动 `Agent.cli_command` 字段（兼容旧路径）。
- **向后兼容**：`WorkerConfig.worker_id` 留空时走旧 `agent_heartbeat_once` 路径（虽然
  `/api/agents` 不返回 `cli_command` 是已知问题，本 change 不引入新症状），Worker 起新
  部署默认开。
- **`Agent.online` 聚合**：所有 instance 都 offline 时置 false；任一 instance online
  时置 true。事件驱动的同步通过 instance heartbeat 路径触发。
