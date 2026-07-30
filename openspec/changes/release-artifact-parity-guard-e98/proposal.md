# Proposal — 发布产物一致性护栏（Epic 98 P0）

status: in_review
epic: 99 (Epic 98 P0 发布产物一致性护栏)
story: 163
task: 926

## 问题

`dist/` 下的三个 Windows/IIS 部署单元是**提交进 Git 的构建产物**，只有人工重跑
`scripts/package_windows.py` 时才刷新。没有任何测试比对过「源码」与「即将部署出去的东西」，
于是形成一个完全静默的故障通道：源码测试全绿、CI 全绿，交付物却是坏的。

巡检实测（修复前）：

| 证据 | 源码 | dist 产物 |
| --- | --- | --- |
| `mcp_server.py` 中 `_api(` 计数 | 0（Epic 97 已修） | **15**（webapi 与 mcp 包各 15） |
| `agentboard/domains/proposals/` | 存在（Epic 96 P0） | **整个包缺失** |
| `migrations/.../h4i5j6k7l8m9_add_proposals.py` | 存在 | **缺失** |
| `api.py` / `service.py` / `models.py` / `cache.py` | 最新 | 全部分叉 |
| zip 与同名目录 | — | **zip 比目录还旧**（有人改过目录没重新压包） |

后果是双重的，且都指向生产：

1. **把已修好的 bug 重新装回生产** —— 按 dist 部署 Windows/IIS，Epic 97 修复的 15 个 MCP 工具
   仍然会在运行期抛 `NameError: name '_api' is not defined`。
2. **新功能根本没发出去** —— 提案（Proposal）后端的模块与建表迁移都不在包里，
   生产启动即 `ImportError`，或表压根不存在。

而真正被拿去部署的是 zip，它比目录更旧一层——这意味着连「看一眼 dist 目录」的人工核对都会被骗过。

## 方案

把「发布产物陈旧」从偶发的人为疏漏，变成 CI 可拦截的确定性失败。

1. **`scripts/package_windows.py` 重构为清单驱动**：build 与 check 共用同一份 `package_specs()`
   事实来源，杜绝「构建逻辑和校验逻辑各写一遍、慢慢漂移」。
2. **新增 `--check` 模式**：不写盘，只比对，按「缺失 / 多余 / 内容不符」分类输出可读差异，
   不一致时非零退出，可直接挂进 CI 与 pre-push。
3. **新增两层 pytest 护栏**：
   - 文本层 `test_epic98_release_artifact_parity.py`：目录奇偶校验、zip↔目录奇偶校验、
     两起历史事故的定点回归、以及对**产物副本**复用 Epic 97 的 AST 未定义调用检查。
     另含自证用例——故意篡改产物副本后 `--check` 必须失败，防止护栏本身写错而永远为真。
   - 运行层 `test_epic98_release_artifact_e2e.py`：解压 `agentboard-webapi.zip` 到临时目录，
     **完全脱离仓库源码**拉起 API + Web，验证提案接口可用、三张提案表建得出来、
     产物内的 MCP 工具真调不抛 `NameError`、前端可登录且控制台零报错。
4. **重新生成 dist 三个包**，让 Epic 96/97 的修复真正进入交付物。

## 非目标

- 不重启、不重建 18001 上的 MCP 容器（自动化硬约束：会切断 WorkBuddy 的 MCP 连接）。
  本次交付的是**产物正确性**；让运行中的容器加载新代码属于独立运维窗口。
- 不修改任何 REST 契约、不改前端。
- 不改动 web 包的来源解析语义（仍是「优先前端构建产物，回退 static 目录」）。
  web 包内容取决于前端是否重新构建，故 pytest 强校验只覆盖两个纯 Python 服务包，
  web 包由 `--check`（全量模式）负责报告。

## 影响

- `scripts/package_windows.py`（重构 + `--check`）
- `tests/test_epic98_release_artifact_parity.py`（新增）
- `tests/test_epic98_release_artifact_e2e.py`（新增）
- `dist/agentboard-{webapi,mcp,web}` 及三个 zip（重新生成）
