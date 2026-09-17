# 统一手动刷新控件设计

## 状态、背景与范围

本设计描述尚未实现的 AgentBoard 前端变更，覆盖三个工作区手动刷新入口：

- 提案列表：`#proposal-list-refresh-btn`
- 提案详情：`#proposal-detail-refresh-btn`
- Story 的 Task 列表：`#story-task-refresh-btn`

既有 Task 工具栏的 `#refreshBtn` 也接入同一组件和视觉语言，但其业务刷新范围不因本设计改变。当前四段模板各自复制刷新 SVG、spinner 和可视“刷新”文字；`src/frontend/src/app/app.css` 中 `.btn.refresh-btn:not(:disabled):hover .refresh-icon` 还会为装饰目的旋转图标。当前提案详情还把派发/重新派发主操作声明在刷新控件之前，违反刷新应是次级操作的排序要求。

本设计只改变呈现、无障碍语义、操作排序和窄屏布局，不改变刷新请求及任何后端或数据合同。

## 目标与非目标

### 目标

1. 三个指定入口在静止和忙碌状态均为 36px × 36px 的方形图标按钮；刷新图标或 spinner 为 16px，页面没有可视“刷新”或“刷新中”文字。
2. 鼠标、键盘和读屏用户都能辨识刷新目标、可用性和忙碌状态；浅色、深色与 `prefers-reduced-motion` 下均可用。
3. 刷新始终是标题操作区的次级操作：提案列表在“新建提案”前，提案详情在派发/重新派发前，Story Task 列表在“新建 Task”前。
4. 窄屏允许操作区换行，且标题、刷新与主操作不重叠、不产生页面级横向溢出，主操作仍可见且可触发。
5. 保留现有 `refreshing` 并发守卫、同尺寸 spinner 禁用态、旧内容保留和成功/失败 toast 行为。

### 非目标

- 不修改 API、数据模型、路由、筛选、分页、自动刷新定时器或现有 toast 文案与触发条件。
- 不改变 `refreshStoryTasks()` 只刷新当前 Story 当前页 Task 的范围，不改变 `refreshProposals()` 的当前筛选语义，也不改变 `refreshProposalDetail()` 加载提案主体和轮次信息的语义。
- 不重构自动刷新控件、其他页面中的普通文字刷新按钮或通用 `.icon-btn`。

## 方案选择

### 备选方案

1. **各模板分别修改。** 代码改动看似较小，但 SVG、spinner、无障碍名称和状态规则仍会继续分叉。
2. **独立展示组件（采用）。** 组件统一按钮结构、状态语义和视觉令牌；父视图继续拥有请求和状态。四处接入能保持 DOM id 和原有业务回调。
3. **仅共享 CSS 类或 directive。** CSS 无法消除重复的状态/无障碍模板；directive 会把结构和状态规则散落到宿主节点，测试边界不清晰。

采用方案 2：新增轻量 standalone `ManualRefreshButtonComponent`。组件不注入服务、不持有持久化状态、不发请求且不处理异常；刷新业务仍由父组件的现有方法唯一拥有。组件采用默认 Angular 样式封装，专用选择器以 `.manual-refresh-button` 为根，防止规则扩散到其他 `.icon-btn`。

四处模板全部迁移后，`app.css` 中旧的 `.btn.refresh-btn`、其 `.refresh-icon`/`.refresh-spinner` 子规则、hover 旋转、`@keyframes refresh-spin` 及其 reduced-motion 覆盖必须作为一个整体删除。它们目前把图标定为 15px、spinner 定为 14px，并会与新控件的 16px 合同竞争。`refresh-btn` 与 `refresh-spinner` 可继续作为 DOM 兼容钩子保留在组件内部；它们不再拥有全局视觉规则。`refresh-label` 不得由新组件渲染，既有依赖该可视标签的测试必须迁移到无障碍名称和 busy 状态断言。

## 组件与模板合同

新增目录 `src/frontend/src/app/manual-refresh-button/`：

- `manual-refresh-button.ts`
- `manual-refresh-button.html`
- `manual-refresh-button.css`
- `manual-refresh-button.spec.ts`

公开合同如下：

| 合同 | 类型 | 规则 |
| --- | --- | --- |
| `buttonId` | `string` | 渲染到内部原生 `<button>` 的稳定 id；必须保留现有测试和自动化选择器。 |
| `label` | `string` | 非空、面向用户的目标名称；用作静止态 `title` 与 `aria-label`。 |
| `refreshing` | `boolean` | `true` 时用 spinner 替换图标、禁用原生按钮、暴露忙碌状态。 |
| `refresh` | `EventEmitter<void>` | 仅在按钮未禁用时转发一次 click；父模板绑定既有刷新方法。 |

内部使用一个 `type="button"` 的原生按钮，带 `btn refresh-btn manual-refresh-button` 类。保留 `refresh-btn` 是为了现有选择器兼容；专用样式只能从 `.manual-refresh-button` 开始，不能修改通用 `.icon-btn` 的默认规则。组件渲染的 SVG 和 spinner 均为 `aria-hidden="true"`，且不渲染可视标签元素。

静止态 `title` 与 `aria-label` 均等于 `label`。忙碌态两者均为“正在”加 `label`（例如“正在刷新提案列表”）；按钮为原生 `disabled`，设置 `aria-busy="true"`，并含一个仅供读屏读取的 `role="status"` 状态文本。恢复静止态时移除忙碌状态并恢复图标。这样鼠标 tooltip、键盘焦点和读屏名称始终指向相同的刷新目标。

组件不会自行复制并发守卫。父级在 click 的同一调用栈内更新现有 `refreshing` signal，随后模板将按钮置为 disabled；现有方法仍保留其防重入检查，形成 UI 与业务两层保护。

### 接入映射与排序

| 位置 | 稳定 id | `label` | 父级事件绑定 | 操作区最终 DOM 顺序 |
| --- | --- | --- | --- | --- |
| `ProposalsTabComponent` | `proposal-list-refresh-btn` | `刷新提案列表` | `refresh.emit()` | 刷新，随后“＋ 新建提案” |
| `ProposalDetailViewComponent` | `proposal-detail-refresh-btn` | `刷新提案详情` | `refreshProposalDetail()` | 刷新，随后派发或重新派发（按现有状态条件仅出现其一） |
| `StoryDetailViewComponent` | `story-task-refresh-btn` | `刷新当前 Story 的 Task 列表` | `host.refreshStoryTasks()` | 刷新，随后“＋ 新建 Task” |
| 根 `App` Task 工具栏 | `refreshBtn` | `刷新当前视图` | `manualRefresh()` | 保持现有工具栏中相对位置 |

实施时，`ProposalsTabComponent`、`ProposalDetailViewComponent`、`StoryDetailViewComponent` 和根 `App` 分别在 standalone imports 中导入该组件。提案详情必须实际重排投影子节点：刷新组件写在按状态显示的派发/重新派发按钮之前，而不是只依靠 CSS `order`，以便键盘与读屏顺序也正确。其余两个指定入口当前已符合此顺序，实施时保留。

组件的默认及根 App Task 工具栏实例均为 36px。实现必须显式覆盖当前 `.taskbar--slim .btn.icon-btn` 的 32px 尺寸和 padding（仅在该节点同时具有 `.manual-refresh-button` 时），但不得改变同一工具栏的自动刷新或其他图标按钮。这样四处手动刷新均共享同一尺寸、图标、忙碌、焦点与动效语言；三个指定入口仍是验收中必须测量 36px 的目标。

## 视觉、状态与动效合同

所有颜色与尺寸使用现有主题变量，不硬编码仅适用于浅色的颜色；候选变量为 `--surface`、`--surface-2`、`--border`、`--text`、`--text-2`、`--text-3`、`--brand-500` 和 `--brand-ring`。

组件 CSS 必须在 `button.btn.refresh-btn.manual-refresh-button` 上显式设置 `box-sizing`、`padding: 0`、`margin: 0`、`inline-size`、`block-size`、`min-inline-size`、`min-block-size`、`flex: 0 0 36px` 与居中布局；图标和 spinner 都在该根下显式定为 16px。根 App 的 slim 工具栏需要一个仅匹配 `.taskbar--slim .btn.icon-btn.manual-refresh-button` 的等价覆盖，以抵消现有 32px 规则。不得以弱选择器、样式加载顺序或全局 `.icon-btn` 修改来赌优先级。

| 状态 | 令牌化表现 |
| --- | --- |
| 默认 | `inline-size`、`block-size` 和 `flex-basis` 均为 `36px`；`16px` 图标；紧凑圆角；`--surface` 表面、`--border` 细边框、`--text-2` 图标色。 |
| Hover | 仅未禁用时使用 `--surface-2`、`--brand-500` 边框和 `--text` 图标色；不旋转、缩放或平移图标。 |
| Focus visible | `--brand-500` 边框及清晰的 `--brand-ring` 外环；不得只依赖 hover 表示键盘焦点。 |
| Pressed | 仅使用令牌化的表面/边框反色反馈；不引入位移动画。 |
| Disabled / busy | 固定保留 36px 占位，使用仍可辨识的 `--surface-2`、`--border` 和 `--text-3`，`cursor: not-allowed`，不可重复点击。 |
| Loading | 16px spinner 替代刷新图标；正常偏好下可以线性旋转。 |

删除旧 `.refresh-icon` 的 hover 旋转和仅为该旋转服务的 transform transition。`prefers-reduced-motion: reduce` 下 spinner 停止旋转，以静态的部分品牌色描边表示加载；不得播放其他非必要动画。深色主题由既有主题变量覆盖；浏览器验收必须实际检查默认、hover、focus-visible 和 disabled/busy 的边框与图标可辨性，不能只依据 CSS 源码断言。

## 布局合同

`WorkspaceHeadingComponent` 的 `.workspace-heading-v7` 保持左侧 `min-width: 0`。其 `.workspace-heading-actions` 在受控断点允许 `flex-wrap: wrap`，保留间距并可在窄宽度占用独立行。不得使用固定页面宽度、绝对定位或负 margin 来强塞操作。

Story Task 区只在 `.workspace-story-task-list` 作用域内调整 `.section-header` 与 `.section-header-actions`：标题可收缩，动作组允许换行；窄屏时动作组可移动到标题下方。不得把这项规则提升为无范围的全局 `.section-header` 变更，以免影响其他列表。宽屏与窄屏都必须让刷新和主操作保持可见、可点击，且 DOM 顺序不变。

## 既有行为与错误处理合同

组件只负责展示和事件转发。下列父级行为为不变量：

- `refreshStoryTasks()` 继续用当前 `storyTaskPage()` 加载当前 Story 的 Task，保留 Story 详情和当前页内容。
- `refreshProposals()` 继续在当前项目及当前状态筛选语义下加载，且旧列表内容不因开始刷新而清空。
- `refreshProposalDetail()` 继续加载提案主体和轮次信息，且开始刷新不清空当前详情。
- 所有父级继续复用 `refreshing`，并在 `finally` 中恢复；现有成功/失败 toast 仍是唯一用户结果反馈。

网络失败时，按钮组件不得捕获、吞掉或另行提示错误。父级原有的 `try/catch/finally` 决定 toast 与恢复时机；恢复后组件回到同尺寸、可点击的静止态。

## 实施文件边界

新增：

- `src/frontend/src/app/manual-refresh-button/manual-refresh-button.ts`
- `src/frontend/src/app/manual-refresh-button/manual-refresh-button.html`
- `src/frontend/src/app/manual-refresh-button/manual-refresh-button.css`
- `src/frontend/src/app/manual-refresh-button/manual-refresh-button.spec.ts`
- 三个指定入口对应的组件 spec；如目录不存在则创建。
- 面向该变更的 Playwright UI 回归测试，使用独立临时测试数据与系统临时目录保存截图/报告。

修改：

- 根 `App` 的 imports 与 `app.html`，仅接入既有 `#refreshBtn` 并删除其重复结构。
- `proposals-tab`、`proposal-detail-view`、`story-detail-view` 的 imports/template，仅接入组件、保留 id 和既有刷新回调，并完成提案详情 DOM 排序。
- `app.css` 中完整的旧手动刷新规则组（按钮、图标、spinner、动画和 reduced-motion 覆盖），由组件封装样式替代；不得改变 `.auto-refresh-btn` 或无关 `.icon-btn`。
- `app-features.css` 中仅作用于 `.taskbar--slim .btn.icon-btn.manual-refresh-button` 的 36px 覆盖，并移除只为 `.refresh-label` 服务的刷新标签隐藏分支；不得改变自动刷新、视图或看板控件。
- `workspace-heading.css` 与限定为 Story Task 区的布局样式，以实现换行合同。

不修改后端、API client、路由、自动刷新或数据模型。

## 测试与验收

### Angular 单元测试

1. `ManualRefreshButtonComponent` 测试静止结构：稳定 id、专用类、无可视刷新文字、正确 `title`/`aria-label`、图标存在且为 `aria-hidden`；click 只发出一次 `refresh`。
2. 同一组件测试忙碌状态：`disabled`、`aria-busy="true"`、忙碌目标名称、spinner 替代图标；从 `true` 变回 `false` 后图标和可点击性恢复。CSS 尺寸不以 jsdom 的 computed style 作为证据。
3. `ProposalsTabComponent` 测试 `#proposal-list-refresh-btn` 位于 `#new-project-proposal-btn` 之前，点击转发 `refresh`，忙碌时禁用，且新建提案输出仍可触发。
4. `ProposalDetailViewComponent` 使用 `ProjectDataService` host stub，测试刷新位于派发/重新派发前、点击调用 `refreshProposalDetail()`、忙碌/恢复合同成立，且主操作仍调用 `advanceProposalStatus()`。
5. `StoryDetailViewComponent` 使用 host stub，测试 `#story-task-refresh-btn` 位于新建 Task 前、点击调用 `refreshStoryTasks()`、忙碌/恢复合同成立，且新建 Task 仍调用 `openCreate()`。
6. 更新根 `App` 的相关测试，确认 `#refreshBtn` 仍绑定 `manualRefresh()`、保留 `refresh-btn`/`refresh-spinner` 兼容钩子、没有 `.refresh-label`，且静止/忙碌名称分别为完整目标名称和“正在”加目标名称。
7. 更新现有 `tests/test_epic78_v66_refresh_e2e.py`、`tests/test_epic79_v67_refresh_toast_e2e.py`、`tests/test_epic80_v68_refresh_failure_e2e.py`：把对可视“刷新”/“刷新中”文字和 `innerText` 的断言替换为 `title`、`aria-label`、`aria-busy`、disabled 与 `.refresh-spinner` 存在性；保持原有成功/失败 toast、旧内容保留和错误监控断言。更新 `tests/e2e_story_slim_tasks/test_story_slim_tasks_e2e.py`，令其明确要求手动刷新没有 `.refresh-label`，同时继续检查其他图标控件的标签隐藏行为。

使用仓库的 Angular/Vitest runner（不假设 Jest 或 `--runInBand`），至少运行：

```powershell
npm --prefix src/frontend test -- --watch=false
```

### Playwright UI 回归

新增或扩展与本变更关联的 Python Playwright 测试，运行时使用隔离的临时数据库，并把截图、报告和缓存写入系统临时目录。测试在浅色/深色、桌面/窄视口下覆盖三处指定入口的静止、hover、键盘 focus-visible、点击后的 busy/disabled、成功恢复和失败恢复。它必须断言：

- 三个指定按钮计算尺寸为 36px × 36px，图标/spinner 为 16px，且没有可视刷新文本；
- 文档顺序符合刷新在对应主操作之前；窄屏不出现操作区重叠或页面级横向溢出，主操作可点击；
- 点击沿用原刷新路径，加载期间禁用，完成后恢复，并观察既有成功/失败 toast；
- hover 不产生装饰性图标旋转；reduced motion 下 spinner 无动画；
- 浏览器控制台没有新增错误。

根 App Task 工具栏的回归还必须确认其 computed size 已从旧 32px 规则提升为 36px，自动刷新按钮仍保留原有尺寸与行为。这一断言防止通用 slim 工具栏 CSS 悄悄破坏统一控件合同。

按仓库 Python 约定以不产生 bytecode 的方式运行目标测试，例如：

```powershell
python -B -m pytest tests/test_task_1730_manual_refresh_e2e.py -q
```

若浏览器依赖不可用，测试应按仓库既有模式明确 skip；实现验收仍需在具备 Chromium 的环境完成。只有 Angular 相关单测和目标 UI 回归均通过（或有明确环境 skip 证据）且无新增控制台错误时，变更才可验收。

## 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 投影到标题槽后意外改变操作顺序 | 保留原生 button id，按模板声明顺序投影，并在单测和浏览器测试中断言 DOM 顺序。 |
| 通用 `.icon-btn` 或旧 `.refresh-btn` 规则覆盖专用尺寸 | 用组件根类和封装样式限定规则，删除旧 hover 旋转规则，并用浏览器计算尺寸验证。 |
| busy 名称丢失刷新目标 | 忙碌名称固定为“正在”加完整 `label`，每个入口在组件和接入测试中断言。 |
| 窄屏换行导致标题或主操作不可达 | 只在工作区标题和 Story Task 范围内使用 flex-wrap/min-width 合同，并在窄视口 Playwright 回归中验证。 |
| 展示组件意外承担请求或错误处理 | 组件不得注入服务、发请求或捕获错误；父级方法和 `refreshing` signal 保持唯一业务所有权。 |
