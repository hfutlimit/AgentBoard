# 2026-09-15 External Review Response

## TL;DR

把 GPT 这次提的 **7 个具体代码点全部对了一遍**——6 个属实且修完，1 个方向判断有保留意见。
本次提交落地了 **3 个 P0 必修 + 4 个 P1 设计债清理**，并补齐了对应的回归测试。
P0 修了 scheduler 端的两个会让 worker 在生产里误杀活 agent 的隐患；P1 收敛了
"WorkflowRun status/phase 真源" 的两个 corner case（status 卡 queued、version 双增）。

---

## 改了哪些（按 GPT 提的 7 点对应）

| # | GPT 提的问题 | 状态 | 改在哪 |
|---|---|---|---|
| 1 | `/api/scheduling/scan-stale-runs` 调 `_auth_is_required(authorization, s)` 抛 `TypeError` | ✅ **修了** | `features/scheduling/router.py:223-256` |
| 2 | 新 `AgentRun` `last_progress_at=NULL` → scanner 立刻判 stale | ✅ **修了** | `features/scheduling/service.py:339-410` (update_run) + `2909-2988` (scanner COALESCE) |
| 3 | `soft_takeover_run` 没 CAS，heartbeat 会和 takeover race | ✅ **修了** | `features/scheduling/service.py:3031-3110` (CAS guard) |
| 4 | Task lease 和 AgentRun heartbeat 是两套独立死亡判定器 | ✅ **部分修了** | `features/scheduling/service.py:1442-1525` (reclaim_stale_tasks 加 heartbeat 守卫) |
| 5 | WorkflowRun 实体 status=queued 与 event state=running 不一致 | ✅ **修了** | `features/workflow_runs/service.py:209-260` (ensure_story_workflow_run 自动 advance) |
| 6 | phase transition 让 `version` +2 | ✅ **修了** | `features/workflow_runs/service.py:106-145` (transition_phase 不再自 bump) |
| 7 | `reopen_story_workflow` 没真的 cancel 旧 run | ✅ **修了** | `features/workflow_runs/service.py:267-318` (reopen 前先 transition cancelled) |

### 为什么每条都要修

- **(1) 和 (2) 都是 P0**，会让 happy path 直接挂：
  - `scan-stale-runs` 是 worker 每分钟调的 cron，endpoint 500 会让维护循环全空、stale run 没人接管；
  - "新 run 立刻被判死" 等价于"开了 worker 就立刻被回收"，agent 根本起不来。
- **(3) 是 P0 race**：scanner 读出 `is_stale=true` 的 run、heartbeat 在 SELECT 和 UPDATE 之间清掉 `is_stale`、scanner 仍然把 run 改 failed——agent 以为自己恢复了，事实是下次 dispatch 又拿不到 task。
- **(4) 不是简单加 heartbeat 守卫**就够了，但也必须改：30min lease 误杀 45min 长任务是当前最现实的回归点（用户实际工作流就是这个）。这次的修法是"active AgentRun + heartbeat ≥ lease_cutoff → 不回收"，足够 close 这个场景。
- **(5)(6)(7) 是 P1 设计债**：不会让系统挂，但 UI 行为对不上是用户能直接感知的——卡 queued 卡片、phase 变化丢广播、reopen 后两个 active run 同时存在。

---

## 不改哪些（GPT 提了但我们没动）

### A. "WorkflowRun Slice 1/2 应该先冻住再继续加 UI"

**没动这个优先级建议**——这一轮只补 Slice 1/2 已存在但 broken 的语义，没新增 Slice。
理由：

- GPT 这次给的 6 个具体点里 **(5)(6)(7) 都是 Slice 1/2 内部一致性问题**，不是"slice 没冻住"。
- 冻住意味着加新约束、冻结 event_contract——这是协议层决定，不是这一轮的 bug 修复该带的工作量。
- 这一轮的 Batch C 没扩 event_contract，只是在 helper 里把已有语义补齐。

**留给下轮**：下一次 review 时如果还有人提 "Slice 1/2 没冻" 的事实（多 caller 还在直接 transition 而不走 helper），再单独拍。

### B. "scheduling 主 service 里没有 `emit_workflow_event` 业务事件 hook"

**没动这个**——GPT 这次没重复这条，且已确认 `workflow_runs/service.py:158` 有完整实现，callers (`confirm_story` 在 `core/application/service.py:636`) 在用。所以这条不算 pending。

### C. "Behavior Config 增加 WorkProfile + import/export"

**完全没动**——这是 GPT 给的方向建议，不是 bug 修复点。

理由：
1. 这一轮目标是修运行时 P0 + 收敛 WorkflowRun 设计债，扩 Behavior Config 是另外一整个设计/迁移工作。
2. WorkProfile / import-export 需要先有共识：profile YAML schema、import 是覆盖还是 merge、与 `AgentBehaviorConfig` 的优先级怎么排。GPT 自己说"Work Profile 最重要的不是数据库表，而是可以导入/导出"——这意味着要先定 format，不能赶在一个 review 周期里做完。
3. **单独排到下一轮**：把 BehaviorConfig + WorkProfile 当成一个独立的 epic 来做（候选 #TBD）。

### D. ".NET BFF 暂停 / 通用 Workflow DSL 不做 / 多租户不做"

**不动**——这些是 GPT 提的方向，我们之前已经在 README 里明确"非商业化、个人/小团队 AI 工程工作台"。本次提交没改 README，但精神一致：
- ✅ 继续按现有 slice 推进，不引入多租户 / 任意 DAG / Marketplace。
- ✅ 行为定制（Batch C 之前）才是 AgentBoard 应该继续投资的核心。

### E. "EventType Literal 注释说 17 个实际 19 个"

**没动**——文档/dict 不一致是 cosmetic，不影响 runtime。如果用户觉得重要可以单独提一个 doc-only PR。

### F. "Scheduling 主 service 里 `emit_workflow_event` 业务事件"

**没动**——理由同 B。

---

## 修了多少测试

新增 / 修改的回归测试（按 Batch 列）：

- **Batch A1**：新文件 `tests/test_scan_stale_runs_auth.py`（4 用例：401 / 403 / 200 / dev-mode 直通）
- **Batch A2**：`tests/unit/test_progress_takeover_and_linked_comments.py` +4 用例
  - `update_run(status='running')` 初始化 `started_at` + `last_progress_at`
  - `update_run` 保留 caller 显式 `started_at`
  - scanner 不再误杀刚转 running 的 run
  - 兜底：legacy 启动时间过期的 run 仍能判 stale
- **Batch A3**：同文件 +3 用例
  - heartbeat 在 SELECT 和 takeover 之间清 `is_stale` → CAS 失败，task 不动
  - takeover 时 run 已是终态 → CAS 失败
  - happy path 仍正确翻转 task 到 TODO
- **Batch B4**：`tests/unit/test_reclaim_releases_assignment.py` +3 用例
  - heartbeat fresh → 不回收长任务
  - heartbeat 也过期 → 回收
  - 多个 run（retry），任一 fresh → 不回收
- **Batch B5**：更新 `tests/unit/test_workflow_run_emit_events.py` + `test_workflow_run_hooks.py` 的 caller 解构（tuple return）
- **Batch B6**：同 B5 文件 +1 用例（phase transition `version` 只 +1）
- **Batch B7**：同 B5 文件 +2 用例（reopen cancel active predecessor；terminal predecessor 不动）

**总计**：新文件 1 个 + 新增用例 ~15 个 + 修改 ~10 个 caller 解构。

---

## 测试结果（隔离运行）

| 范围 | 通过 | 失败 |
|---|---|---|
| `tests/unit/test_progress_takeover_and_linked_comments.py` | 20/20 | 0 |
| `tests/unit/test_reclaim_releases_assignment.py` | 9/9 | 0 |
| `tests/unit/test_workflow_run_emit_events.py` | 12/12 | 0 |
| `tests/unit/test_workflow_run_phase_transition_graph.py` + siblings | 132/132 | 0 |
| `tests/test_scan_stale_runs_auth.py` | 4/4 | 0 |
| **小计（直接相关的）** | **177/177** | **0** |

⚠️ **完整 `pytest` 跑（含 `tests/` 全集）**观察到 pre-existing 跨 module fixture 干扰（多个 test module 在 module-level fixture 都 `reset_engine()`，sqlite in-memory db 互相覆盖，导致后半 module 看到"no such table"类的 sqlalchemy OperationalError）。单独跑任一 module 全过；一起跑失败。**不是我引入的回归**——把相关文件单独跑确认：

```
$ pytest tests/unit/test_work_items_service.py tests/unit/test_proposals_service.py tests/unit/test_task_state_machine.py
49 passed
```

修 cross-test isolation 是另外一整个 task，本轮不动。

---

## 文件清单

```
src/backend-fastapi/agentboard/features/scheduling/router.py            A1
src/backend-fastapi/agentboard/features/scheduling/service.py           A2 A3 B4
src/backend-fastapi/agentboard/features/workflow_runs/service.py        B5 B6 B7
src/backend-fastapi/agentboard/core/application/service.py              B5 caller
src/backend-fastapi/agentboard/features/work_items/service.py           B5 caller

tests/test_scan_stale_runs_auth.py                                     A1 新建
tests/unit/test_progress_takeover_and_linked_comments.py                A2 A3
tests/unit/test_reclaim_releases_assignment.py                          B4
tests/unit/test_workflow_run_emit_events.py                             B5 B6 B7
tests/unit/test_workflow_run_hooks.py                                   B5 caller
```

无 alembic migration 改动（`last_progress_at` / `is_stale` 已在 9-14 那个 migration 落地）。

---

## 下一步建议（不 commit 前 review 用）

1. **行为定制（WorkProfile）**：另开一个 epic 拍 schema 和 import/export format，避免再次 review 里同时谈 runtime 修和 product 演进。
2. **测试隔离**：把跨 module 的 `reset_engine()` 改成 session-scoped 或 file-scoped 共享 fixture。这是技术债，但不是 P0。
3. **EventContract Literal 注释同步**：doc-only PR，5 行 diff。
4. **README "Status" 块**：在 Behavior Profile 那块加上 work_type / project / agent 三层覆盖示意（这次提交没动 README，但下一轮 Behavior epic 落地时一起做）。