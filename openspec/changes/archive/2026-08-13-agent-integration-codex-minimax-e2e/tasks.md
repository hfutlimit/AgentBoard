# Tasks — Codex + MiniMax 端到端验证

**status**: completed

## Task 1 — Codex E2E 测试

- [x] `tests/_fake_codex.py`：fake codex CLI 脚本（按 `codex exec --json` 真实协议：stderr 进度行 + stdout 决策 JSON + 退出码可控）
- [x] `tests/test_codex_e2e.py`：
  - [x] 拉起 fake codex → success + output 含决策 JSON
  - [x] 退出码非 0 → failed + error_message
  - [x] 超时（max_poll_seconds 小值）→ failed(timeout)
  - [x] 命令不存在（AGENTBOARD_CODEX_BIN 指向 no/such）→ failed + "command not found"

## Task 2 — MiniMax 接入 executor 框架

- [x] `agentboard/executor.py` 新增 `MiniMaxLauncher(CliLauncher)`：
  - [x] `name = "minimax"`，`env_var = "AGENTBOARD_MINIMAX_BIN"`，`timeout_seconds = 600.0`
  - [x] `command = [sys.executable, str(Path(__file__).parent.parent / "scripts" / "minimax_invoker.py")]`
  - [x] `@adapter("minimax")` 注册到 `ADAPTERS`
- [x] 不修改既有 `CodexLauncher` / `ClaudeLauncher` / `CliLauncher` / `KNOWN_AGENTS`
- [x] **副产品**：CliLauncher.launch() 注入 PYTHONIOENCODING=utf-8 + PYTHONUTF8=1 到子进程 env（修 Windows zh-CN cp936 编码 bug）

## Task 3 — MiniMax 单元测试

- [x] `tests/test_minimax_invoker_unit.py`：
  - [x] `_extract_decision` 正常 ask 决策（无 think 块）
  - [x] `_extract_decision` 剥离 `<think>...</think>` 块
  - [x] `_extract_decision` 容忍 Markdown ```json 包裹
  - [x] `_extract_decision` 无 JSON → fail action + 错误说明
  - [x] `_extract_decision` 括号不平衡 → fail action
  - [x] `_extract_decision` 中文 in think 块
  - [x] `_http_post_chat` HTTP 4xx/5xx → 抛 RuntimeError（含 err_body 摘要）
  - [x] `_http_post_chat` 网络错误 → 抛 RuntimeError
  - [x] `_http_post_chat` 缺 API Key → 抛 RuntimeError
  - [x] `_http_post_chat` 正常路径 → 返回 assistant content
  - [x] `main()` 缺 `MINIMAX_API_KEY` → 进程退出码 1
  - [x] `main()` empty prompt → 进程退出码 1
  - [x] `main()` 正常路径 → stdout 一行 JSON decision，进程退出码 0
  - [x] `main()` API 4xx → 进程退出码 0 + stdout fail action

## Task 4 — MiniMax E2E 测试

- [x] `tests/test_minimax_e2e.py`：
  - [x] `MiniMaxLauncher` 在 `ADAPTERS` 注册 + 默认命令路径校验
  - [x] `launch_run` 走 `MiniMaxLauncher` + fake server 回 finalize 决策 → success + output 含 converged_spec
  - [x] API server 故意 500 → success（子进程协议层面）+ output 含 fail action
  - [x] `AGENTBOARD_MINIMAX_BIN` env 覆盖 fake minimax 脚本

## Task 5 — 文档更新

- [x] `docs/agent-integration-analysis.md`：
  - [x] Codex 行状态：「框架已就位、未见 E2E 验证」 → 「端到端验证（tests/test_codex_e2e.py）」
  - [x] MiniMax 行状态：「已端到端跑通（脚本级）」 → 「端到端验证（tests/test_minimax_e2e.py + tests/test_minimax_invoker_unit.py）」
  - [x] 建议优先级部分：把 Codex 标 ✅ 完成 / MiniMax 标 ✅ 完成
- [x] `README.md`：在「### Codex」节后追加「### MiniMax」命令模板段

## Task 6 — 归档 & 提交

- [x] `openspec/changes/agent-integration-codex-minimax-e2e/` 整体移入 `openspec/changes/archive/2026-08-13-agent-integration-codex-minimax-e2e/`
- [x] git commit + push

## 验证结果（实跑数据）

- [x] `pytest tests/test_codex_e2e.py -q` 全绿 → 8 passed
- [x] `pytest tests/test_minimax_invoker_unit.py -q` 全绿 → 15 passed
- [x] `pytest tests/test_minimax_e2e.py -q` 全绿 → 5 passed
- [x] 既有回归 `pytest tests/test_epic78_story102_launcher.py -q` 全绿 → 13 passed
- [x] 合并跑 → **41 passed in 24.78s**
