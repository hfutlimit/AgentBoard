# Proposal: Epic 96 P3 — 定稿转化 Story/Task（人工终审确认端点）

## 背景

Epic 96（Proposal 澄清回路）P0-P2 已交付：

- P0：proposal / proposal_round / proposal_question 三表 + 状态机 + REST + 前端问答工作台；
- P1：6 个 proposal_* MCP 工具 + Worker 消费者 + 无头 Agent 调用 + 崩溃恢复租约；
- P2：RabbitMQ 派发 + CAS 原子认领 + 显式租约 + DLQ + 超时回退。

澄清收敛后，提案停在 `converged` 等待人工终审。但**没有任何代码把 `converged_spec` 转化为 Story/Task** —— 闭环最后一环缺失，提案永远无法走到终态 `story_created`，澄清产出无法进入项目管理主链路。

## 目标

人工终审确认后，基于 `converged_spec` 生成 Story 与子 Task，回填 `proposal.story_id` 并推进 `converged → story_created`。

## 约束

- 保留人类最后一道闸：不直接由 WorkBuddy/Worker 调 `create_story`，必须经服务端转化端点由人工/管理员确认。
- 纯增量，零既有 REST 契约破坏；不触碰端口 18001。
- 存储双后端兼容（本变更不涉及迁移，无新增列/表）。

## 变更

1. `agentboard/service.py`：新增 `convert_proposal_to_story()`（校验 converged + epic 归属 → 创建 Story → 解析 `- [ ]` 清单生成子 Task → 回填 story_id + 推进 story_created；幂等防重放）。
2. `agentboard/api.py`：新增 `ProposalConvertIn` + `POST /api/proposals/{pid}/convert`。
3. `agentboard/mcp_server.py`：新增 `proposal_convert` 工具（走 `_http`，供人工/管理员终审调用）。
4. 测试：`tests/test_epic96_p3_proposal_convert.py`（真实 uvicorn + REST 全链路）。
