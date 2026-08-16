# 设计：Agent 能力评分（Epic 140 切片 1）

## 数据模型

```
task_outcome
├── id            PK
├── task_id       FK→tasks.id，UNIQUE（幂等 upsert 键）
├── project_id    FK→projects.id（聚合维度）
├── agent_id      FK→users.id 可空（未指派归 None 桶，终态全覆盖）
├── task_type     dev/bug/qa/design（聚合维度）
├── score         0~1 复合分（CHECK 约束）
├── judge_json    Text JSON 明细（L1/L2 过程指标 + judge_pending 标记）
├── duration_s    时长（created_at→updated_at）
├── attempts      状态迁移次数
└── created_at / updated_at
```

## 复合评分公式（Story 267 定义，L3 未接入时中性占位）

```
score = 0.4*pass_first_try + 0.3*judge_quality(0.75 占位) + 0.2*cycle_efficiency + 0.1*reason_quality
```

- `pass_first_try`：评审历史无 reject 往返 = 1.0，否则 0.0
- `cycle_efficiency`：1.0 − 0.25*(rejects−1)，下限 0.2
- `reason_quality`：终态 status_reason 非空 = 1.0
- L3 `judge_quality` 在切片 2 由 LLM judge 回填，替换中性占位

## 过程指标来源

- `task_status_history`：rejects（in_review→in_progress）、review_rounds（→in_review 次数）、blocked_count、attempts
- `task.status_reason`：withdrawn / reason_quality
- `task.created_at / updated_at`：duration_s

## 落库 Hook（work_items.service.set_status）

终态（done/blocked）迁移提交后，延迟 import `learning.service.record_outcome` 幂等 upsert：
- 失败 rollback 并吞掉异常（增强数据不阻断主流程）
- 延迟 import 规避 features 间循环依赖

## API

| 端点 | 参数 | 返回 |
|------|------|------|
| GET /api/learning/agent-leaderboard | project_id?, task_type?, limit 1-200 | [{agent_id, project_id, task_type, tasks, avg_score}] |
| GET /api/learning/outcomes | project_id?, task_id?, limit 1-200 | 明细列表（含 judge_json 解析对象） |

鉴权：`_optional_user_id`（存在 token 则校验 api:read；REQUIRE_AUTH=1 由全局 middleware 强制）。

## 切片边界

- 切片 2：LLM judge 调度（judge_prompt.py + provider 抽象 + daily quota）
- 切片 3：Worker RAG recall + playbook
- 切片 4：前端 dashboard（agent 评分排行）
