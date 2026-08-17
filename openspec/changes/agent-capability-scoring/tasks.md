# 任务清单：Agent 能力评分（Epic 140 切片 1 + 切片 2）

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

## [x] 6.（切片 2）LLM judge 调度（Task 1088 设计 / Task 1089 实现）
- `learning/judge_prompt.py`：L3 rubric（spec_coverage/code_quality/test_coverage/spec_drift/reason_quality）+ system prompt（反偏见）+ user prompt 模板
- `learning/judge.py`：build_judge_input（task+评论+状态历史+L1/L2）/ deterministic_judge（无 LLM 降级启发式）/ call_llm_judge（OpenAI 兼容 urllib 调用，超时 20s）/ judge_task 主入口（schema 校验+回填+score 重算）/ daily quota（AGENTBOARD_JUDGE_DAILY_QUOTA 默认 200）/ schedule_judge（daemon 线程异步）
- `learning/service.py`：apply_judge（judge_json 合并 + score 按复合公式重算，幂等）
- `learning/router.py`：POST /api/learning/judge/{task_id}（手动触发）+ GET /api/learning/judge/status（provider/quota 状态）
- `work_items/service.py`：set_status 终态后 `AGENTBOARD_JUDGE_AUTO=1`（默认）daemon 线程异步 judge，失败吞异常
- 环境变量：AGENTBOARD_JUDGE_API_URL / _API_KEY / _MODEL / _DAILY_QUOTA / _AUTO
- `tests/test_learning_judge.py`：14 用例（deterministic schema/回填重算/幂等/非终态/LLM mock 成功/非法 JSON 降级/网络失败降级/缺维度补全/quota 降级/status 端点/build_judge_input/API 手动触发/leaderboard 更新）
- 顺手修：MCP create_task 默认 type="task"→"dev"（Story 265 类型精简后失效，不传 type 的 MCP 创建任务 422）；test_mcp_smoke 断言过期（create_epic 自动建 Story / create_story 自动带 design+dev Task → 成员判断替代 [0]）
- 回归：learning+story265+smoke+epic30_cache+crud_smoke 56 passed / 10 skipped；test_mcp_smoke 3 passed

## [x] 7.（切片 3）Worker RAG recall + playbook
- `learning/memory.py`：embed_text（signed-hash 256 维，L2 归一化）+ HashVectorStore（SQL 余弦扫描）+ build_episode_text（spec/状态历史/评论聚合）+ store_episode（episode_id=task_id 幂等 upsert）+ recall_episodes（成功 top-5 / 失败 top-3）+ build_recall_section（prompt 注入段）
- `learning/memory.py`：update_playbook（episode 模式：强幂等；非 episode 模式：字符串兜底）
- `learning/memory.py`：get_playbook（空模板/有内容）
- `learning/models.py`：EpisodeEmbedding / ProjectPlaybook（last_appended_episode_id 锚点）
- `learning/router.py`：GET /api/learning/project-playbook / POST /api/learning/playbook/{project_id}/append / GET /api/learning/recall
- `features/workers/handlers/story.py`：build_story_prompt 注入 recall 段
- `tests/test_learning_memory.py`：18 用例（向量化/store/recall/playbook/全链路/prompt 注入/API）

### 7.1 8/17 review fixes（DB 级幂等 + RAG project filter 下推）
- **P1 #1 RAG recall project filter 下推**：`HashVectorStore.search()` 接收 `project_id` 推到 SQL `WHERE`，
  避免「全库 Top-K → Python 过滤」被跨项目高相似度 episode 挤出本项目结果；`VectorStore` Protocol
  同步更新契约；`recall_episodes` 移除冗余 post-filter。3 个新测试覆盖（5+8 抢占场景、协议 SQL 下推、其他项目排除）。
- **P1 #2 playbook DB 级幂等**：新增 `project_playbook_episode (project_id, episode_id)` 复合主键关联表；
  `update_playbook` 改用「SELECT 预检 + SAVEPOINT(begin_nested) + IntegrityError 兜底」三层防御，跨
  并发也由 DB 仲裁；`ProjectPlaybook.last_appended_episode_id` 退化为「最近一次」展示字段。3 个新测试
  覆盖（旧 last 路径仍正常 / 非相邻 101→102→101 拒绝 / 多线程并发只一方胜出）。
- **伴随 fix：`delete_task` FK 清理**：FK 链 `project_playbook_episode.episode_id → tasks.id` 加入
  `delete_task` 的清理路径，避免「task 走到 done → 落 playbook → 用户删 task」撞 422；playbook
  `content_md` 不动（保留历史 pattern）。`test_update_task_atomicity` 新增覆盖。
- 迁移：`migrations/versions/e5f6a7b8c9d0_playbook_episode_unique.py`（down=d7e8f9a0b1c2）

## [ ] 8.（切片 4）前端 agent 评分 dashboard
## [ ] 9.（可选）judge 校准脚本：50+ 人工 ground truth 相关性（pearson r ≥ 0.7 门槛）
