# Tasks: Proposal 服务端 CAS 原子认领 + 显式租约（Epic 96 P2-0 / Task 933）

## 实现

- [x] `agentboard/domains/proposals/models.py`：`Proposal` 新增 `claimed_by` / `claimed_at`
      列，导出 `CLAIMABLE_STATUSES = {QUEUED, ANSWERED}`
- [x] `migrations/versions/i5j6k7l8m9n0_add_proposal_claim_lease.py`：双后端兼容迁移
      （`claimed_by` / `claimed_at` + `ix_proposals_status_claimed_at` 索引）
- [x] `agentboard/models.py`：facade 导出 `CLAIMABLE_STATUSES`
- [x] `agentboard/database.py`：SQLite connect 事件加 `PRAGMA busy_timeout=10000`
- [x] `agentboard/service.py`：新增 `claim_proposal()`（单条条件 UPDATE 原子认领，
      rowcount 仲裁，404 区分）+ `reclaim_stale_proposals()`（依据 `claimed_at` 批量回退）；
      `set_proposal_status` 维护 `claimed_at`/`claimed_by` 租约；`DEFAULT_CLAIM_LEASE_SECONDS=1800`
- [x] `agentboard/api.py`：新增 `POST /api/proposals/reclaim-stale` 与
      `POST /api/proposals/{pid}/claim`（200/409/404 语义），声明顺序在 `/{pid}` 之前
- [x] `agentboard/worker.py`：`claim()` 改走 CAS 端点（200→True / 409,404→False）；
      `reclaim_stale()` 改走 `POST /reclaim-stale` 解析 `reclaimed`
- [x] `agentboard/mcp_server.py`：`proposal_claim` 改走 `POST /{id}/claim`，支持 `answered`
      态认领，与 Worker 语义对齐

## 测试

- [x] `test_claim_endpoint_returns_200_with_lease_fields`：queued 认领成功 + 带上租约字段
- [x] `test_claim_endpoint_409_when_already_analyzing`：重复认领必须 409（杜绝双 Agent）
- [x] `test_claim_endpoint_404_for_unknown_proposal`：不存在提案 404
- [x] `test_claim_endpoint_409_for_unclaimable_status`：draft 等不可认领 → 409
- [x] `test_claim_endpoint_answered_to_analyzing`：answered 可再认领进入下一轮（MCP 语义）
- [x] `test_concurrent_claim_exactly_one_winner_per_proposal`：8 提案 × 12 线程并发抢，
      每提案恰好 1×200、其余全 409 —— 原子性的最强证明（退回 GET+PUT 写法会偶发多个 200）
- [x] `test_claimed_at_not_refreshed_by_unrelated_patch`：认领后 PATCH 正文刷新 `updated_at`
      但 `claimed_at` 纹丝不动 —— 租约隔离的核心
- [x] `test_reclaim_stale_uses_claimed_at_not_updated_at`：认领→sleep→他人 PATCH 续期
      `updated_at`，短租约回收仍生效（依据 `claimed_at`）—— 决定性证明不误用 `updated_at`
- [x] `test_reclaim_stale_endpoint_contract_and_fresh_untouched`：端点契约 + 未到期不回收
- [x] `test_reclaim_stale_only_touches_analyzing`：answered/converged 不被误回收

## 验证结果

- `tests/test_epic96_p12_proposal_worker.py` — **claim/reclaim 子集 13 passed**
  （10 项 P2-0 新增 + 3 项既有并发/回收用例仍有效）
- Playwright E2E `tests/test_epic96_p02_proposal_workbench_e2e.py` — **2 passed**
  （真实浏览器跑通问答工作台，0 console error）
- 全量回归（排除 2 个与本次无关的损坏收集器 `admin_portal/*`、`test_review_84_85.py`
  及需要常驻服务端的集成测试）：121 passed，无新增失败；46 个失败均为环境性
  （需常驻服务/浏览器）或预存在的导入错误，与本次提案 CAS 改动无关。

## 约束核对

- [x] 端口 18001（WorkBuddy MCP）未触碰、未改任何 docker 端口映射
- [x] 新增端点为**契约增量**，未破坏既有 REST；前端代码零改动
- [x] 测试完全自包含（自起 uvicorn 子进程 + 独立临时 SQLite，真实多线程并发）
- [x] `frontend/` / docker 配置 / dist 构建产物均未改动
