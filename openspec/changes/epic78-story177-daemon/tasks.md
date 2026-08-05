# Tasks — Executor 常驻 daemon 模式

**status**: in_review

## [x] 1. `run_daemon()` 常驻主循环（agentboard/executor.py）

- [x] 循环扫描 id 升序第一个 `pending` AgentRun，交 `execute_run` 驱动；
- [x] 无 pending → `stop_event.wait(idle_sleep)`（可唤醒）或 `time.sleep(idle_sleep)`；
- [x] `max_runs` 限制（None=无限）；`stop_event`/KeyboardInterrupt 优雅退出（`stopped=True`）；
- [x] `execute_run` 抛异常 → 兜底 `service.update_run(failed)` 后 continue（不拖垮常驻循环）；
- [x] 返回 `{"processed", "last_status", "stopped"}`。

## [x] 2. CLI `--daemon*` 参数

- [x] `--daemon` / `--daemon-poll-interval` / `--daemon-idle-sleep` / `--daemon-max-runs`；
- [x] `--daemon-max-runs 0` 立即退出（冒烟/测试）；打印 `daemon exit: processed=...`。

## [x] 3. 单元测试 tests/test_epic78_story177_daemon.py（5 用例）

- [x] 连续处理 pending 至 max_runs（按 id 升序、逐 run 落 success）；
- [x] 无 pending idle + stop_event 优雅退出（`stopped=True`）；
- [x] execute_run 异常 → 该 run failed（error_message 含标识）+ 继续处理下一个；
- [x] execute_run 返回 None（run 被他人认领）→ 计数继续，不死循环；
- [x] CLI 子进程 `--daemon --daemon-max-runs 0` 立即退出（独立临时 DB，规避跨进程 SQLite 锁）。

## [x] 4. E2E tests/test_epic78_story177_daemon_e2e.py（1 用例）

- [x] 自起 API+Web（临时 SQLite 完整迁移链）；
- [x] REST 创建 project + schedule（agent=codex）+ 手动触发 pending run；
- [x] CLI `--daemon --daemon-max-runs 1` 真实处理（run 离开 pending → success/failed）；
- [x] Playwright 登录 Web 项目视图 0 控制台报错 / 0 页面异常 / 0 资源 404。

## [x] 5. 回归验证

- [x] 聚焦回归：Epic78 Story101-107 + scheduler + smoke = 100 passed（smoke 单独 8 passed，
      全量并列失败为既有「模块级 env 污染」预存在模式，非本次回归）；
- [x] 未触碰 18001 / docker 端口；零 REST/DB 契约变更。
