# 设计：前端持续优化（小步迭代纪律）

## 迭代纪律（强制）
1. **单交付**：每个自动任务周期只认领并交付 backlog 中的**一项**（A-xx 或经评估的 B-xx）。做完即交付、即 commit，不囤积多件。
2. **范围红线**：单文件改动为主；一次交付在所有前端文件中的新增代码合计 < ~80 行；不引入新 npm/打包依赖；不改 `models.py` / `api.py` 契约（除非该项明确标注「需后端」）。统计口径包含 JS、CSS 与 HTML，不以“逻辑行”子集代替总量。
3. **完成标准**：本地启动 `api` + `web` 服务并真实操作该功能。HTTP 200、资源关键字检查与 `httpx` 后端流程测试仅属于部署/契约冒烟；DOM 交互变化必须由 Playwright 或等价真实浏览器验证。浏览器条件暂不具备时，完成记录必须明确列出未验证项。
4. **超限即拆**：若某项实际偏大，应在编码前拆回更细子项，本轮只做其一，剩余回写 backlog（保持 unchecked）。若审查后才发现超限，保留完成事实，标记流程例外并把浏览器回归加入 `playwright-e2e` 变更，不得将例外描述为符合红线。
5. **记录**：每完成一项，在 `docs/tasks.md` Epic 11 勾选并追加「完成记录」（日期 + 一句话）；积累 5~8 项后可写一份简短前端演化小结（非强制）。

## 复用现有能力（避免重复造轮子）
- 状态/类型枚举来自 `GET /api/meta`（`META.statuses` / `META.types`），前端已缓存；新增状态色直接映射，不写死新枚举。
- 任务/子项数据来自既有接口：`/api/stories/{id}/tasks`、`/api/projects`、`/api/epics/{id}/stories` 等，无需新端点。
- 渲染辅助：`md()`（markdown）、`esc()`、`statusSelect()`、`typeSelect()`、`toast()`、`route()` 可直接复用或小幅扩展。

## 视觉对齐 Jira 的低成本手段
- 状态色：用 CSS 类 `.badge.status--<status>` 上色（backlog 灰 / todo 蓝 / in_progress 黄 / in_review 紫 / verifying 橙 / done 绿），bug 用红色调；纯 CSS，无依赖。
- 间距/圆角/阴影：在 `style.css` 中用 CSS 变量统一，便于深色模式（A-10）一套切换。
- 图标：优先用内联 SVG 或 Unicode，避免引入图标库。

## 后端依赖项的评估流程（Backlog B）
当某 Jira 式能力确需后端字段（labels / assignee / due_date / 评论 / 拖拽 order）：
1. 先在 backlog 标注「需后端」，不排入小优化。
2. 单独提一个普通 OpenSpec change（如 `task-labels`）走 proposal/design/tasks，后端 + 前端一起做。
3. 完成后再从前端角度做对应的小优化（如标签选择器 UI）。

## 验证清单（每项交付前自查）
- [ ] 仅改动 `agentboard/web/static/` 下文件（或明确标注需后端的项已单独评估）
- [ ] 本地起服务手测该优化生效
- [ ] `docs/tasks.md` Epic 11 对应项已勾选并加完成记录
- [ ] 现有 `tests/test_web_flow.py` 通过（若改动通用函数）
- [ ] commit message 形如 `feat(ui): 前端小优化 - <一句话描述>`
