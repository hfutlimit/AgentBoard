# 任务清单：Proposal 后端基座（Epic 96 P0 · Task 922）

## Task 922 — P0-1 · Proposal 三表模型 + 状态机 + CRUD/问答 REST API
- [x] `agentboard/domains/proposals/models.py`：`Proposal` / `ProposalRound` / `ProposalQuestion`
      实体 + `ProposalStatus` 枚举 + `PROPOSAL_TRANSITIONS` 状态机表 + `ASKABLE_STATUSES`。
- [x] `agentboard/models.py` facade 导出上述符号并加入 `__all__`。
- [x] `migrations/versions/h4i5j6k7l8m9_add_proposals.py`：新增三表（CHECK 约束 / FK / 索引 /
      唯一约束），双后端兼容，`down_revision="g3h4i5j6k7l8"`。
- [x] `agentboard/service.py`：create / get / list / update / delete_proposal、set_proposal_status
      （校验合法迁移）、create_proposal_round（幂等）、add_proposal_questions（推 awaiting）、
      answer_proposal_question（unsure）、list_proposal_rounds、自动 awaiting→answered。
- [x] `agentboard/api.py`：REST 端点 `/api/proposals`（CRUD + status + rounds + questions + answer）
      + Pydantic 模型；访问控制中间件接入 project_id 校验；审计 entity 映射补充。
- [x] `tests/test_epic96_p0_proposals.py`：17 个 pytest 覆盖模型 / 状态机 / REST 全链路
      （真实 uvicorn 子进程 + httpx，规避 BaseHTTPMiddleware + TestClient 死锁）。

## 验证结论
- `pytest tests/test_epic96_p0_proposals.py`：17 passed（346s，真实子进程 + httpx）。
- `pytest tests/test_domain_boundaries.py`：3 passed（修复了既有硬编码表数断言）。
- `tests/test_epic96_p0_proposals_e2e.py`：Playwright 真实浏览器冒烟——注册/登录、项目列表与
  看板区域渲染无 console/pageerror/非预期 401/404；经真实运行后端 `POST /api/proposals` →
  `GET /api/proposals` → 状态机合法迁移 draft→queued→analyzing → 非法迁移拒 400 全链路通过；
  截图 `screenshots/epic96_p0_proposals_e2e.png`。
- MCP 状态流转：Task #922、Story #154、Epic #96 均推进至 `in_review`。
- 兼容性：纯增量，未改动任何既有端点契约 / 模型 / 前端文件；`init_db()` 启动自动 `alembic
  upgrade head` 落地三表，本地与生产均无需手动迁移。

## 后续（P1，本变更不涵盖）
- MCP 4 工具（create_proposal / ask / answer / get_pending）+ Worker 消费者 + 无头 WorkBuddy 调用。
- 前端问答工作台 UI（Story #154 前端部分）。
