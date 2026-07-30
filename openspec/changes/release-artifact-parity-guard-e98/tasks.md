# Tasks — 发布产物一致性护栏（Epic 98 P0）

Epic 99 / Story 163 / Task 926（AgentBoard 项目 id=3）

## 1. 现状取证

- [x] 比对源码与 dist 中 `mcp_server.py` 的 `_api(` 计数：源码 0 / 两个包各 15
- [x] 比对 zip 内文件哈希：源码 `5f70fd82…` vs 产物 `450916c8…` → 不一致
- [x] `diff -rq agentboard dist/agentboard-webapi/agentboard`：6 处差异，
      含「Only in agentboard/domains: proposals」（整包缺失）
- [x] 发现 zip 比目录更旧（目录已被手改但未重新压包）
- [x] 发现 Alembic 迁移 `h4i5j6k7l8m9_add_proposals.py` 也未进包（生产建不出提案表）

## 2. 打包脚本重构

- [x] 抽出 `package_specs()` 单一事实来源，build 与 check 共用
- [x] `iter_tree_files()` 统一忽略 `__pycache__` / `.pyc` / `.pyo`
- [x] `expected_manifest()` 产出「包内相对路径 → 源文件」映射
- [x] `build_pkg()` 改为照清单复制，三个包合并为一条构建路径
- [x] 新增 `--check`：分类输出缺失 / 多余 / 内容不符，非零退出
- [x] 新增 `--python-only`：只校验两个纯 Python 服务包（供 pytest 与 CI 用）
- [x] zip 校验 `check_zip()`：条目集合 + 逐字节内容与目录一致
- [x] 保持 web 包来源解析语义不变（前端构建产物优先，回退 static）

## 3. 修复产物

- [x] 重新执行 `python scripts/package_windows.py` 生成三个包
- [x] 复核：`--check` 退出 0，三个包全部「与源码一致」
- [x] 复核：两个 zip 内 `mcp_server.py` 的 `_api(` 计数为 0 且 sha 与源码相同
- [x] 复核：`domains/proposals` 与 `add_proposals` 迁移均已入包

## 4. 文本层护栏（`tests/test_epic98_release_artifact_parity.py`）

- [x] 直接以模块方式加载打包脚本，复用其清单定义（测试与构建同源）
- [x] `test_python_packages_match_source`：缺失 / 多余 / 内容不符三类断言
- [x] `test_zip_matches_package_dir`：zip ↔ 目录奇偶校验
- [x] `test_known_p0_regressions_absent_from_artifacts`：两起历史事故定点回归
- [x] `test_no_undefined_global_calls_in_packaged_mcp_server`：对**产物副本**跑 AST 检查
      （改为纯静态取模块命名空间，避免 import 产物造成 `sys.modules` 冲突）
- [x] `test_check_mode_detects_tampering`：自证护栏有效——沙箱内篡改后 `--check` 必须失败
- [x] `test_check_mode_passes_on_current_artifacts`：当前仓库状态必须通过
- [x] 全部失败信息附带修复命令 `python scripts/package_windows.py`

## 5. 运行层护栏（`tests/test_epic98_release_artifact_e2e.py`）

- [x] 解压 `agentboard-webapi.zip` 到临时目录，`PYTHONPATH` 只暴露产物目录
- [x] 自起 API + Web 两个 uvicorn（随机空闲端口 + 独立临时 SQLite）
- [x] `test_packaged_artifact_serves_proposals_api`：提案创建 / 列表 / 状态迁移 / pending 队列
- [x] `test_packaged_artifact_has_proposal_tables`：三张提案表确实建出
- [x] `test_packaged_mcp_tools_run_without_nameerror`：子进程加载**产物内** mcp_server，
      真调 5 个曾损坏的工具，断言无 `NameError`
- [x] `test_packaged_frontend_renders_without_console_errors`：Playwright 登录 →
      等骨架屏消失 → 断言控制台零报错 / 静态资源零失败 → 截图

## 6. 验证与回归

- [x] Epic 98 文本层护栏 10 passed
- [x] 回归：epic97 / epic96 / domain_boundaries / epic30_cache / admin_api_key_scope 全绿
- [x] 运行层 E2E 全绿（含 Playwright 前端零报错）
- [x] 未触碰端口 18001；未修改任何 REST 契约

## 7. 交付

- [x] OpenSpec proposal / design / tasks
- [x] git commit + push origin main
- [x] MCP 状态流转：Task 926 → in_review，Story 163 / Epic 99 同步

## 后续（不在本次范围）

- [ ] 把 `--check` 挂进 CI 与 pre-push 钩子
- [ ] 评估方案 A：把 dist/ 移出 Git，改由流水线产出
- [ ] 运维窗口：重建 18001 MCP 容器，让运行中的服务加载已修复的代码
