# Tasks

- [x] 先写失败测试：无 task 的 stale run 在心清掉 `is_stale` 后不得被置 failed / 不得计入 `taken_over`（RED：`_cas_fail_stale_run` 不存在，AttributeError ×2）
- [x] 抽出 `_cas_fail_stale_run`，`soft_takeover_run` 与 `scan_stale_agent_runs` 的 `task_id is None` 分支共用；`error_message` 由调用方传入以保留既有文案（`6521182`）
- [x] 补正向对照：真正停滞的无 task run 仍置 failed、`error_message='stale_run_no_task'`、`finished_at` 非空并计入 `taken_over`
- [x] 修正新测试 docstring 里与提交日期不符的「2026-09-19 fix」→ 2026-11-12（`6521182`）
- [x] 先写失败测试：声明端点凭据开销不得随项目数增长（HEAD 实测 RED：1 项目 3 次 / 3 项目 5 次）
- [x] 抽出 `write_actor` / `authorize_member`，`authorize()` 语义不变，声明端点每请求只解析一次凭据；docstring 补 401/409 前置契约（`f200d2b`）
- [x] 补 409 回归（`AGENTBOARD_WORKER_OWNED_ENABLED=0` + 有效凭据）（`f200d2b`）
- [x] 复核 .NET presence 耦合风险 → 不可达，撤回不改代码（证据见 design.md §4）
- [x] 复核 `_caller_uid_admin` 漏校验 `revoked_at` → 不可达；改为修正 `revoke_api_key` 的假文档陈述，补 admin API key 回归（已撤销→401、有效→200）与文件末尾换行（`67bc427`）
- [x] 规格同步：MODIFIED「Worker 项目范围声明按成员资格收敛」补前置门与单次解析；ADDED「Stale run 回收上报与实际回收一致」覆盖两条路径三场景
- [x] 归档本变更至 `openspec/changes/archive/2026-11-16-scheduling-review-hardening/` 并同步 `openspec/specs/agentboard/spec.md`
- [x] 验证：takeover 文件 24 passed、声明文件 32 passed、auth 文件 6 passed、相邻 reclaim/coordinator 27 passed
- [x] 全量对照：.NET 376 passed / 0 failed；Python 全量基线 `3136720` 219 failed / 1520 passed / 275 errors vs 本轮 218 / 1528 / 286，基线失败集抽样 79/79 仍失败，本轮新增测试零 setup error。声明测试在全量上下文的失败已用双树最小组合复现为既有污染（基线 6 failed、本轮 8 failed，同为 `happy_path/conftest.py:213`）。详见 `qoder-worker-provider/tasks.md` 附录
- [ ] `openspec validate --strict`：本机未安装 openspec CLI（`which openspec` 与 `npx --no-install openspec` 均失败），改为手工结构校验——MODIFIED 标题与 `specs/agentboard/spec.md` 既有 requirement 逐字一致、每个 requirement 至少一个 `#### Scenario:`、GIVEN/WHEN/THEN 齐全。安装 CLI 后需补跑