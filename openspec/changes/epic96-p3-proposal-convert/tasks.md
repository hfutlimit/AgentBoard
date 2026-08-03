# Tasks: Epic 96 P3 — 定稿转化 Story/Task

## 任务清单

- [x] service.py 新增 `convert_proposal_to_story()`（converged 校验 / epic 归属校验 / Story 创建 / 清单解析生成子 Task / story_id 回填 / converged→story_created / 幂等防重放）
- [x] api.py 新增 `ProposalConvertIn` + `POST /api/proposals/{pid}/convert` 端点
- [x] mcp_server.py 新增 `proposal_convert` 工具（走 `_http`，供人工/管理员终审）
- [x] 新增 `tests/test_epic96_p3_proposal_convert.py`（9 passed：主链路 / 显式标题 / 幂等重放 / 非 converged 拒绝 / 空 spec 拒绝 / 跨项目 epic 拒绝 / 404 / MCP 注册 + AST 护栏）
- [x] 回归：P0(12) + P11(14) + P3(9) + P12(27) + P2(17) 全部通过，6 skipped（MQ 相关环境性跳过）
- [x] OpenSpec 文档（proposal / design / tasks）
- [ ] Playwright E2E：问答工作台 converged 提案渲染，0 控制台报错
- [ ] 状态流转（MCP）：Task 963 → in_review；Story 157 → in_review；Epic 96 → done（P0/P1/P2/P3 全部 in_review/done 后）
