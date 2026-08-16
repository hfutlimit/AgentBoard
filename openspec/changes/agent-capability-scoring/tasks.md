# 任务清单：Agent 能力评分（Epic 140 切片 1）

## [x] 1. 模型 + 迁移
- `agentboard/features/learning/models.py`：TaskOutcome（task_id UNIQUE / score CHECK 0-1 / judge_json Text）
- `migrations/versions/a2b3c4d5e6f7_task_outcome.py`（down_revision=a1b2c3d4e5f6，单 head）

## [x] 2. 过程指标计算器 + 落库
- `learning/service.py`：compute_process_metrics（L1/L2 纯统计）、record_outcome（幂等 upsert）
- `work_items/service.py`：set_status 终态分支 `_record_learning_outcome`（延迟 import + 失败吞异常）

## [x] 3. leaderboard + outcomes API
- `learning/router.py`：GET /api/learning/agent-leaderboard、GET /api/learning/outcomes
- `api.py`：注册 learning_router

## [x] 4. 测试
- `tests/test_learning_outcome.py`：10 用例（落库/幂等/非终态/blocked/过程指标/聚合/过滤/limit 校验/明细）

## [x] 5. 回归
- test_story_265 + test_epic30_cache + test_smoke + test_learning_outcome 全绿（42 passed, 1 skipped）

## [ ] 6.（切片 2）LLM judge 调度：judge_prompt.py + provider 抽象 + daily quota + 校准
## [ ] 7.（切片 3）Worker RAG recall + playbook
## [ ] 8.（切片 4）前端 agent 评分 dashboard
