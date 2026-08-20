# Epic 149 Story 320 自动化 E2E 验证报告

**标题**: 阶段4 色板收口：indigo→navy 统一 + 删旧令牌 + 补暗色主题
**验证时间**: 2026-08-20T09:06:55
**环境**: 本地 ng serve http://127.0.0.1:4200（/api 代理生产后端 http://124.220.44.12），项目 3
**结论**: **PASS**

## 运行期错误
- page_errors（致命）: 0
- console errors: 0
- console warnings: 3

## Story 320 检查项（色板收口 scope）

### 外壳 palette
- 侧边栏背景: rgb(16, 36, 62)（期望 navy rgb(16,36,62)）

### 暗色主题切换
- dark_active: True
- 亮度(亮→暗): 765 → 113

### indigo 残留扫描（亮色）
- 扫描节点数: 315
- indigo 命中(应=0): 0
- 图表调色板命中(允许): 0

## 视图渲染

- 概览 (tab=overview): textLen=590 svgUse=0 selector=True
- 看板 (tab=kanban): textLen=248 svgUse=0 selector=True
- Epics (tab=epics): textLen=933 svgUse=0 selector=True
- 工作项 (tab=backlog): textLen=1150 svgUse=0 selector=True
- 提案 (tab=proposals): textLen=356 svgUse=0 selector=True
- 文档 (tab=documents): textLen=2886 svgUse=0 selector=True
- 成员与 Agents (tab=members): textLen=148 svgUse=0 selector=None
- 设置 (tab=settings): textLen=259 svgUse=0 selector=None

## 已知问题（不在 320 scope，已另立跟踪）

- 成员与 Agents 视图主内容区空白（已知 Bug #1290，跨 Story，不在 320 scope）：textLen=148

## 截图

- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\320\01_shell_light.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\320\02_shell_dark.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\320\view_overview.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\320\view_kanban.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\320\view_epics.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\320\view_backlog.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\320\view_proposals.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\320\view_documents.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\320\view_members.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\320\view_settings.png