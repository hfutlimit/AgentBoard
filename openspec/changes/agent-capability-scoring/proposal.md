# 变更提案：Agent 能力评分（Epic 140 切片 1）— task_outcome + 过程指标 + leaderboard

## 背景

AgentBoard 的 worker 派单链路已完善（CAS claim + MQ 广播 + 评审护栏），但缺少对 agent 能力的可观测性：不知道哪个 agent 在哪个项目、哪种任务类型上更强；历史 task 的产出质量没有结构化指标。Epic 140 规划「能力评估 + 持续学习」两层基础设施。

## 目标（本切片）

1. 每个完成（done/blocked/withdrawn）任务自动沉淀结构化 outcome（`task_outcome` 表）。
2. L1 任务结果 + L2 过程质量全部由**纯统计**计算（无 LLM 依赖，可靠可测）。
3. `GET /api/learning/agent-leaderboard` 多维聚合（agent × project × task_type）。
4. L3 LLM-as-judge（切片 2）字段预留（judge_json.judge_pending=true），不阻塞本切片。

## 范围

- 新增 `agentboard/features/learning/`：models（TaskOutcome）、service（指标计算/落库/聚合）、router（2 个端点）。
- `work_items/service.set_status` 终态分支自动调用 `record_outcome`（幂等 upsert，失败不阻断主流程）。
- Alembic 迁移 `a2b3c4d5e6f7`（task_outcome 表）。
- 测试 `tests/test_learning_outcome.py`（10 用例）。

不在范围：LLM judge 调度（切片 2）、RAG recall + playbook（切片 3）、前端 dashboard（切片 4，独立交付）。

## 约束

- 零新增依赖；双后端兼容（SQLite/MariaDB）。
- 不破坏现有 REST/DB 契约；outcome 落库失败不阻断任务状态流转。
- 不触碰端口 18001。
