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

## 切片 2 设计（2026-08-16 追加）：L3 LLM-as-judge 调度

### 降级优先级（绝不阻塞主流程）
```
LLM 可用（AGENTBOARD_JUDGE_API_URL 非空）且今日未超 quota → llm
否则 → deterministic（启发式，从 L1/L2 + 输入信号推导 L3，零幻觉）
```
judge_json.judge_provider 记录实际 provider，UI 据此标注置信度。

### 模块划分
| 模块 | 职责 |
|------|------|
| `learning/judge_prompt.py` | L3 rubric 常量（5 维度）+ SYSTEM_PROMPT（反偏见：短答案奖励、冗长空洞扣分）+ USER_PROMPT 模板 |
| `learning/judge.py` | build_judge_input（task+评论+状态历史+L1/L2）/ deterministic_judge / call_llm_judge（OpenAI 兼容 chat/completions，标准库 urllib，20s 超时）/ judge_task 主入口 / daily quota / schedule_judge（daemon 线程） |
| `learning/service.py` | apply_judge：judge_json 合并 + score 按复合公式重算（幂等） |
| `learning/router.py` | POST /api/learning/judge/{task_id}（手动触发同步回填）+ GET /api/learning/judge/status |

### 环境变量
| 变量 | 默认 | 说明 |
|------|------|------|
| AGENTBOARD_JUDGE_API_URL | 空 | 空 = 禁用 LLM，全走 deterministic |
| AGENTBOARD_JUDGE_API_KEY | 空 | Bearer token |
| AGENTBOARD_JUDGE_MODEL | gpt-4o-mini | chat/completions model |
| AGENTBOARD_JUDGE_DAILY_QUOTA | 200 | 今日 LLM judge 次数上限（成本护栏） |
| AGENTBOARD_JUDGE_AUTO | 1 | set_status 终态后 daemon 线程异步 judge（测试置 0 关闭） |

### 触发链路
`set_status(done/blocked)` → `_record_learning_outcome`（切片 1）→ `schedule_judge`（daemon 线程，独立 SessionLocal，失败吞异常）。手动端点可随时重算（幂等）。

### 校验与容错
- LLM 返回缺维度 → 其余维度均值补全；非法 JSON / 网络失败 / 超时 → 全部降级 deterministic
- judge 属增强数据：任何异常不外泄到 set_status / 主流程
- score 重算公式与切片 1 一致：0.4·pass_first + 0.3·judge_quality + 0.2·cycle + 0.1·reason
