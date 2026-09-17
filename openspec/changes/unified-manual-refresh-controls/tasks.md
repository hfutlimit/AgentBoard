# Tasks: 统一工作区手动刷新控件

## 实施

- [ ] 新增 standalone `ManualRefreshButtonComponent`，提供稳定 id、目标名称、忙碌输入和 refresh 输出；不得加入 API、状态持久化或请求逻辑。
- [ ] 实现专用 36px 图标按钮与 16px 图标/spinner、主题令牌状态、键盘焦点环、disabled/busy 表现和 reduced-motion 静态加载降级。
- [ ] 删除 `app.css` 中完整旧手动刷新规则组（按钮、图标、spinner、动画和 reduced-motion 覆盖），以组件样式替代；保留 `refresh-btn`/`refresh-spinner` DOM 兼容钩子但不渲染 `.refresh-label`，且不影响其他 `.icon-btn` 和 `.auto-refresh-btn`。
- [ ] 在根 `App`、提案列表、提案详情和 Story Task 列表接入组件，保留四个既有 button id 与既有父级刷新方法。
- [ ] 将提案详情刷新组件置于派发/重新派发主操作之前；保留提案列表和 Story Task 已有的刷新优先顺序。
- [ ] 为工作区标题和限定 Story Task 区增加可换行布局，保证窄屏下标题、刷新与主操作均可访问；以 `.taskbar--slim .btn.icon-btn.manual-refresh-button` 的专门覆盖确保根 Task 工具栏的手动刷新也是 36px，而不影响同栏其他图标控件。

## 验证

- [ ] 为刷新按钮组件测试静止结构、无障碍名称、click、busy/disabled spinner 和恢复。
- [ ] 为提案列表、提案详情和 Story Task 入口测试 DOM 排序、原刷新回调、忙碌恢复与主操作未受影响。
- [ ] 更新根 `App` 测试及 `tests/test_epic78_v66_refresh_e2e.py`、`tests/test_epic79_v67_refresh_toast_e2e.py`、`tests/test_epic80_v68_refresh_failure_e2e.py`、`tests/e2e_story_slim_tasks/test_story_slim_tasks_e2e.py`：移除对可视刷新文字/`.refresh-label` 的预期，改验 `title`、`aria-label`、`aria-busy`、disabled、spinner、toast、内容保留和其他 slim 控件行为。
- [ ] 运行 Angular/Vitest 前端测试：`npm --prefix src/frontend test -- --watch=false`。
- [ ] 新增或扩展隔离的 Playwright 回归，验证浅/深色、桌面/窄屏、focus、busy/恢复、无 hover 旋转和 reduced motion；用 `python -B` 运行目标测试。
- [ ] 记录测试命令、结果和任何明确的浏览器环境 skip；确认没有新增浏览器控制台错误。
