# Worker-owned v2 验证记录（2026-09-05）

后续生产验证已完成，见 [生产 Codex 受控故障闭环记录](worker-owned-production-validation-20260905.md)。下文保留此前隔离环境验证的时间边界。

范围：真实 CLI + 两个独立 Node Worker + 真实 RabbitMQ + 隔离 FastAPI/SQLite + 专用 KnowledgeVault Git worktree。
不是 fake adapter，也不是生产服务器的验收。测试只提交 Proposal，不手工写 Task/Story 完成状态。

## Codex：通过

- 两个 `gpt-5.6-terra` 实例，只有 Codex provider 在本轮运行。
- Project #1、Proposal #1 → Story #2；Design #3、Dev #4、QA #5 均 done，Story done。
- 七种工作全部完成，额外真实经过 design_review reject → design 返工 → design_review approve。
- QA 独立于上游 Dev，QA Review 独立于 QA。
- 真实部署验证默认 18765 / 覆写 18766、中文 UTF-8、默认名称/空名称、404、回环监听、Ctrl+C 退出与端口释放；6 项 unittest 通过。
- 实施提交 `c0042018ed88df0a4094b36292605228d835fe7f`，最终工作树干净。
- 证据：`tmp/worker-owned-e2e/run-03-codex/report.json`、`business-evidence.json`、`qa-artifacts/`、Node/API 日志。
- 本轮 Node DLL SHA256：`8d322e26e79efc15a775e7708b7f82225aa3940ce01be85ca7cf6c29db90fe25`。
  后续恢复/监控加固在本轮启动后完成，因此需用更新构建继续验证，不能声称所有后续改动都由这次运行覆盖。

前两轮失败也保留：`run-01` 是 Windows 中文 stdin 非 UTF-8，3 次同因失败后切换；
`run-02-workbuddy` 累计 3 次在旧多行 JSON 解析器失败后停止。两处均已修复并有回归测试。

## WorkBuddy：修复拆单问题后通过

`run-04-workbuddy` 发现 Worker 将验收复选框解析成 9 个重复 Dev Task，已停止本轮并保留证据。
修复为 Proposal Agent 明确输出结构化 DAG；未提供 DAG 时只生成一个完整 Dev Task，不再解析任意 Markdown 复选框。
这不是 provider CLI 失败，不通过手工删除/合并 Task 改写验收结果。随后在新 checkout/DB 上重测。

- `run-05-workbuddy` 的运行报告和独立业务证据检查均 `passed: true`。
- 两个独立 Node Worker，只有 WorkBuddy provider（model `auto`）运行。
- Project #1、Proposal #1 → Story #2；Design #3、Dev #4、QA #5 和 Story 均 done。
- 七种工作全部完成；A 执行 proposal/design/dev/qa_review，B 执行 design_review/dev_review/qa，无自审。
- Proposal 首次遗漏 spec，被真实 API 拒绝；失败反馈传回 Agent 后第二次成功，其余工作均一次完成。
- QA 在回环端口 59745 实际启动服务，通过 curl 验证默认名称、Alice、中文张三、404 和 JSON UTF-8 响应头；记录测试步骤和响应，终止服务并确认端口释放。
  此轮停止服务使用 taskkill，不将强制终止描述成验证了应用优雅退出。
- 实施提交 `7a63c2acee969b02b666b16861f12f1026028fed`，QA 后 HEAD 不变且工作树干净。
- 证据：`tmp/worker-owned-e2e/run-05-workbuddy/report.json`、`business-evidence.json`、`qa-artifacts/`、Node/API 日志及 `schema-drift.json`。
- 本轮 Node DLL SHA256：`060c721604d6ae905a5649328edefa465d7c4ff624043ee2d52ba35099b6657a`，覆盖后续恢复/监控加固和结构化拆票修复。
- 两轮成功测试的 Worker 已退出，专用 RabbitMQ 容器已停止并保留；未推送测试项目的功能分支。
- 收尾发现旧 run-04 及 run-05 Dev Review 的 Git Bash 后台 HTTP 进程脱离了已退出的 CLI，共 3 个监听进程（18765/28765/28766）。核对父进程命令中的专用 checkout 后定点停止并确认端口释放；QA 自己的 59745 已正常释放。当前进程树退出不能保证清理已经脱离父进程的后台服务，不能声称无人值守清理完全闭环。

## MiniMax Code：本机安装阻塞

已安装 wrapper 指向不存在的 `resources/resources/daemon/cli.js`，官方 CLI 启动报 MODULE_NOT_FOUND。
包内 `mcode-tools` 仅提供登录/连接器命令，没有代码 Agent 的 headless 执行命令。
未用第三方 invoker 或其他 provider 冒充；需恢复可运行的官方 CLI 后再测。

## 自动测试与部署边界

- Node 全套 287 项通过；新增 Worker 协议 13 项通过；旧 durable intake 6 项通过。
- OpenAPI snapshot/hash 已同步；隔离 API 的 live schema 漂移为 0。
- 旧 `test_epic122_s3m2.py` 有 9 项失败，在未修改 `00646fb` 基线副本复现完全相同的失败；
  包括旧 MCP 文件路径和旧 reviewer fixture 假设。本次不把全仓 Python 测试称为通过。
- `test_execution_contract_review.py` 与若干 E2E 文件混跑会受模块重载污染；独立进程 6 项全部通过，CI 维持独立运行。
- 生产用有效凭据请求新 `/api/worker-work/snapshot` 返回 404，尚未部署 v2；不能用本地通过代替生产通过。
- 新协议由 FastAPI 承载，需更新 FastAPI、迁移业务 DB、发布新 Node，并排空/停用旧中央执行消费者。
  详细切换边界见 [运行说明](worker-owned-execution.md)。
- 后续用户确认 QA 发现问题新建 `bug` Task，由 dev 能力处理。已补齐 Worker 生成明确 Bug/复测计划、Server 原子落库、Bug 实施者不得复测，以及连续两轮缺陷修复后才关闭 Story 的协议回归。
  此补丁验证为后端协议 16 项（含旧 majority 配置不干扰 Worker 独立评审）、Node 291 项；此前两轮真实 CLI 成功记录不代表这个新增失败分支已完成真实 CLI/生产 E2E。
