# Tasks — Agent 认领并发护栏（Epic 118）

## 1. 实现 `_agent_claim_task` 并发护栏（Task 998，highest） ✅

- [x] 死代码清理：`if False else` 双分支统一为 `/api/schedules/1/runs`
- [x] 占用保护：status ∉ {backlog, todo} → `{error: already claimed, run: null}`，不创建 Run/不 PUT
- [x] Run 幂等复用：GET runs 命中同 task active Run（pending/running）→ `reused: true`
- [x] 创建失败兜底：POST error 透传，不推进状态
- [x] `python -m py_compile` 通过

## 2. 单测（tests/test_epic118_claim_guard.py） ✅

- [x] 9 用例全绿（mock `_http` 三分支 + AST 死代码护栏 + FastMCP 注册验证）
- [x] Epic 97 MCP 可用性回归护栏 5 passed（无新 `_api(` 回归）

## 3. 回归与部署约束 ✅

- [x] 零 REST/DB 契约变更；零新增依赖
- [x] 18001 未触碰（容器内存旧代码，随独立运维窗口重部署）
- [x] 前端零改动，Epic 117 三 E2E（S1/S2/S3）验收全绿不回归
