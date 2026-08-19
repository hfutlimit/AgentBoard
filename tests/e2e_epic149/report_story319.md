# Epic 149 Story 319 自动化 E2E 验证报告

**标题**: 阶段3 视图逐个迁移（八视图）
**验证时间**: 2026-08-20T07:41:55
**环境**: 本地 ng serve http://127.0.0.1:4200（/api 代理生产后端 http://124.220.44.12），项目 3
**结论**: **FAIL**

## 运行期错误
- page_errors（致命）: 0
- console errors: 0
- console warnings: 3

## Story 319 视图验证

- 概览 (tab=overview): textLen=610 svgUse=0 selector=True
- 看板 (tab=kanban): textLen=257 svgUse=0 selector=True
- Epics (tab=epics): textLen=939 svgUse=0 selector=True
- 工作项 (tab=backlog): textLen=1144 svgUse=0 selector=True
- 提案 (tab=proposals): textLen=356 svgUse=0 selector=True
- 文档 (tab=documents): textLen=2695 svgUse=0 selector=True
- 成员与 Agents (tab=members): textLen=148 svgUse=0 selector=None
- 设置 (tab=settings): textLen=259 svgUse=0 selector=None

## 截图

- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\319\view_overview.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\319\view_kanban.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\319\view_epics.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\319\view_backlog.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\319\view_proposals.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\319\view_documents.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\319\view_members.png
- E:\Projects\WorkBuddy\AgentBoard\tests\e2e_epic149\screenshots\319\view_settings.png

## 问题清单

- app.html 无 @if(activeTab()==='members') 块；frontend/src/app 无 members-tab 组件。Story 319 提交日志宣称「8/8」实际只迁移 7 视图（members 遗漏）。点击侧边栏「成员与 Agents」主内容区空白。已关联 Bug 关联 Story 318。