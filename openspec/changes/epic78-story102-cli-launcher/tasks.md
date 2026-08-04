# Tasks — 模式 A：Launcher（CLI Agent 主动拉起）

**status**: in_review

## Task 1.1 — `CliLauncher` 基类（`agentboard/executor.py` 增量扩展）

- [x] `CliLauncher(LauncherAdapter)`：`command` 默认命令列表 + `env_var` 环境变量覆盖
- [x] `build_command(ctx)`：env 覆盖完整命令串（`shlex.split`），未覆盖用默认命令
- [x] `build_prompt(run, task, ctx)` 覆写：title + spec + 项目记忆 + 验收标准
- [x] `launch()`：`Popen(stdin=DEVNULL, stdout=PIPE, stderr=STDOUT, text=True,
      encoding="utf-8", errors="replace")` + `communicate(input=prompt)` 喂入
- [x] `read_output()` 读回 stdout 全文；`poll_status` 继承 LauncherAdapter 退出码判定
- [x] 命令不存在（FileNotFoundError）→ `AdapterError`（含可读错误）

## Task 1.2 — 具体适配器 + 注册

- [x] `CodexLauncher`：默认 `["codex", "exec", "--json"]`，env `AGENTBOARD_CODEX_BIN`
- [x] `ClaudeLauncher`：默认 `["claude", "-p"]`，env `AGENTBOARD_CLAUDE_BIN`
- [x] 两者注册进 `ADAPTERS`（键 `codex` / `claude`）；`KNOWN_AGENTS` 不变

## Task 1.3 — 最小单次驱动 `launch_run` + CLI

- [x] `launch_run(session_factory, run_id, *, poll_interval, max_poll_seconds)`：
      pending → running → success/failed 回写 DB（status/started_at/finished_at/
      output/error_message）
- [x] 组装 `AgentRunContext`：schedule title / task title+spec / project key+name /
      memory（Document.type=memory 抽取）+ acceptance（task.spec 中验收段落）
- [x] agent 名解析：env `AGENTBOARD_DEFAULT_AGENT`（默认 codex）
- [x] 超时兜底：kill 子进程 → FAILED(timeout)
- [x] CLI：`python -m agentboard.executor --run <id>` / `--once`

## Task 1.4 — 单元测试 `tests/test_epic78_story102_launcher.py`

- [x] 注册表含 codex / claude，可取回并实例化
- [x] Fake CLI（stdin 读 prompt 的 python 脚本）退出码 0 → SUCCESS + output 回写
- [x] 退出码非 0 → FAILED + error_message 回写
- [x] env 指向不存在路径 → FAILED（AdapterError）不裸崩
- [x] 超时（max_poll_seconds 小值）→ FAILED(timeout)
- [x] build_prompt 四要素（title/spec/memory/acceptance）断言
- [x] launch_run 全链路：DB pending run → success/failed，时间戳回写正确
- [x] --once / --run CLI 冒烟

## Task 1.5 — OpenSpec 文档

- [x] `proposal.md` / `design.md` / `tasks.md` 三件套

## 验证结果

- `tests/test_epic78_story102_launcher.py`：**13 passed**（13.4s）
  （注册表 / prompt 四要素 / env 覆盖 / 退出码 0→SUCCESS / 非 0→FAILED /
  命令缺失 / 超时 / 非 pending 跳过 / 记忆+验收加载 / claude 适配器 /
  first_pending / CLI --run+--once）
- `tests/test_epic78_story102_launcher_e2e.py`：**2 passed**（Playwright 真浏览器，
  登录 + 项目/看板渲染 0 控制台报错 / 0 资源 404）
- 聚焦回归：Story101 + Story102 + scheduler + domain_boundaries + epic30_cache +
  Story105 = **65 passed, 1 skipped**
- 中等回归：proposals P0 / worker P1-2 / convert P3 / admin_api_key_scope /
  api_keys / mcp_smoke = **72 passed**；顺带修复预存在测试-契约漂移
  `test_due_date.py`（list_tasks 分页 {items,total}，5 passed）
- MCP 状态：Task 967 / Story 102 → **in_review**；Epic 78 → in_progress
- 硬约束：未触碰 18001 / docker；零既有 REST 契约变更（仅新增 executor.py
  增量代码 + Story 101 测试隔离 fixture 兼容顶层注册）
