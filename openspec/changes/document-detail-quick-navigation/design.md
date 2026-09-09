# 设计：文档详情快速定位导航

> 对应 Story 434 / Task 1723。本文件是实现前设计，并已针对第一轮评审修订；其中测试均为计划，不能据此声称功能已实现或已验证。

## 1. 范围、现状与状态边界

`src/frontend/src/app/app.html` 的 `document` 视图先输出面包屑和 `.doc-title`，再按 `docViewMode()` 输出预览、分屏编辑或分屏只读。预览状态再按 `docDetailTab()` 在 Markdown `.doc-content` 与历史内容间切换。评论卡片位于这些内容分支之后；其标题、空态和评论表单由模板始终提供。`loadDocumentDetail` 读取文档后尝试读取评论，评论读取失败时将列表置为空数组。

fullscreen 是模板末尾的独立 `@if (docFullscreenOpen() && docItem())` 覆盖层；它不会移除普通详情树。因此本功能不能把“普通预览”仅解释为 preview/content。唯一允许导航及其定位 id/testid 存在的状态为：

```ts
view() === 'document'
  && docViewMode() === 'preview'
  && docDetailTab() === 'content'
  && !docFullscreenOpen()
```

下文称其为“快速定位状态”。它排除 history、`split-edit`、`split-read` 与 fullscreen。导航、正文目标和评论目标必须共用这个唯一守卫；fullscreen 打开后它们必须一起从普通详情树移除，避免导航处于 Tab 顺序或与 fullscreen 的 Markdown 副本形成重复 id。

本设计不恢复任何模式入口。现有模板已没有历史、分屏或 fullscreen 的进入控件，尽管 `app.ts` 仍保存相应 state/method。不能用修改页面入口来让本功能测试可达这些模式。

## 2. 决策

| 议题 | 决策 | 理由 |
| --- | --- | --- |
| 控件位置 | 在 `.doc-title` 后、普通预览 Markdown 前输出独立的 `nav.doc-quick-nav` | 与标题相邻；作为文档主内容流的一部分可用 sticky 保持入口可用，不用 fixed 覆盖表单。 |
| 范围守卫 | 用一个 `isDocumentQuickNavigationMode()` 方法表达四项条件，包含 `!docFullscreenOpen()` | 同一函数供模板、目标属性和点击处理使用，防止 fullscreen 漏排除或条件漂移。 |
| 正文目标 | 快速定位状态下的 `.doc-content` 外壳具有 `document-content-start` id/testid | 目标不依赖 Markdown 生成的标题；`content: ''` 时 `article` 仍存在。 |
| 评论目标 | 快速定位状态下，评论卡片的静态 `.section-header` 具有 `document-comments-start` id/testid | 落点准确为评论标题，不依赖评论 `@for` 项；空态、输入表单和评论读取失败仍可达。 |
| 控件语义 | `nav` 带 `aria-label="文档快速定位"`，含两个原生 `button type="button"` | 原生按钮提供 Tab、Enter 和 Space；文本本身就是清晰可访问名称。 |
| 滚动 | 从注入的 `DOCUMENT` 按固定 id 同步查找并调用 `scrollIntoView` | 控件与目标已经共同渲染；无计时器、无全局 Markdown 查询、无额外渲染回调。 |
| 减少动态效果 | 每次点击时仅在 `typeof document.defaultView?.matchMedia === 'function'` 时查询偏好 | 保持与 `ThemeService` 相同的特性检测习惯；缺失 API 时不抛错并回退到默认 smooth。 |
| 焦点 | 滚动后保持焦点在激活按钮，不向内容/标题调用 `focus()` | 避免二次滚动和非预期读屏上下文跳转；键盘用户仍可继续操作。 |
| 窄屏和主题 | 复用 `ghost-sm`、现有变量和 focus ring；小于等于 640px 时两个按钮等宽且至少 44px 高 | 不遮挡标题、正文、空态或评论表单，并满足触控面积。 |

## 3. 模板和 DOM 契约

### 3.1 控件

在 `.doc-title` 后、既有 preview/content 分支内新增导航。导航本身受快速定位状态保护；不能放在文档标题外层、评论卡片内或 fullscreen 覆盖层内。

```html
@if (isDocumentQuickNavigationMode()) {
  <nav class="doc-quick-nav"
       aria-label="文档快速定位"
       data-testid="document-quick-navigation">
    <button id="document-jump-to-content"
            data-testid="document-jump-to-content"
            type="button"
            class="ghost-sm"
            (click)="scrollDocumentDetailTo('content')">正文开头</button>
    <button id="document-jump-to-comments"
            data-testid="document-jump-to-comments"
            type="button"
            class="ghost-sm"
            (click)="scrollDocumentDetailTo('comments')">评论开头</button>
  </nav>
}
```

不使用只含图标的按钮，不使用 `aria-pressed`（它们不是可切换状态），也不修改路由/hash。按钮 id 仅用于 DOM/自动化识别；按钮文字保持为可访问名称。

### 3.2 唯一目标

预览内容的 `article` 仍必须在 fullscreen 打开时保留为背景内容，但其定位属性只能在快速定位状态出现。因此不要用静态 id；用同一守卫的属性绑定：

```html
<article class="card md doc-content"
  [attr.id]="isDocumentQuickNavigationMode() ? 'document-content-start' : null"
  [attr.data-testid]="isDocumentQuickNavigationMode() ? 'document-content-start' : null"
  [innerHTML]="renderMarkdown(d.content)"></article>

<div class="section-header"
  [attr.id]="isDocumentQuickNavigationMode() ? 'document-comments-start' : null"
  [attr.data-testid]="isDocumentQuickNavigationMode() ? 'document-comments-start' : null">
  <h3>评论 / 协作者讨论 <span class="count">...</span></h3>
  ...
</div>
```

这两个固定字符串在快速定位状态各只出现一次。fullscreen 的 `.doc-content` 副本不得复用它们；history 和 split 分支也不得输出它们。评论外层、空态与表单保持原有条件，不因空数组、评论读取失败或 `content: ''` 而移除锚点。若以后重构为懒加载评论，需维持标题容器的即时存在性，或在控件可用前渲染该容器。

## 4. 客户端行为契约

在 `app.ts` 使用受限目标类型和私有 id 映射：

```ts
type DocumentQuickNavigationTarget = 'content' | 'comments';

private readonly documentQuickNavigationIds: Record<DocumentQuickNavigationTarget, string> = {
  content: 'document-content-start',
  comments: 'document-comments-start',
};
```

`isDocumentQuickNavigationMode()` 返回第 1 节的完整条件。它可由模板访问，且是点击处理的第一道防御。`scrollDocumentDetailTo(target)` 按下列顺序执行：

1. 若守卫为假，直接返回；不滚动、不改变任何状态。
2. 从映射取得 id，使用 `this.document.getElementById(id)` 同步取目标。找不到目标则安静返回。
3. 同时保护 `scrollIntoView` 的可用性；测试环境或非浏览器 DOM 缺少该函数时安静返回。
4. 取得 `const view = this.document.defaultView`。仅当 `typeof view?.matchMedia === 'function'` 时读取 `'(prefers-reduced-motion: reduce)'`；其它情况按 `false` 处理。`matchMedia` 调用本身如被宿主异常实现抛出，捕获后也按 `false` 处理。
5. 调用 `targetElement.scrollIntoView({ behavior, block: 'start', inline: 'nearest' })`。`behavior` 在 reduce 为真时为 `'auto'`，否则为 `'smooth'`。
6. 不调用 `focus()`，不写路由/hash/localStorage，不显示 toast，不请求/修改文档或评论 API。

点击发生在控件和目标已经由同一条件模板提交后的状态，故不需要 `setTimeout`、`requestAnimationFrame` 或显式 after-render 回调。防御性空值和能力检查仍覆盖路由/状态在事件处理前后变化、空正文、空评论及非浏览器测试宿主。

## 5. 样式、布局和可访问性

样式追加到现有文档详情样式所在的 `src/frontend/src/app/app-features.css`：

- `.doc-quick-nav` 使用 `position: sticky; top: 12px`，文档流内 block 布局、非透明 `var(--surface, ...)` 背景、已有边框/圆角和足以浮在正文上的局部 `z-index`。实现前应在实际详情滚动根确认 sticky 祖先没有破坏性 `overflow`；若将来滚动根改变，随该根 sticky，不改用 fixed 补丁。
- 两个目标都设置相同的 `scroll-margin-block-start`，值至少为 sticky 导航高度、12px top offset 与安全间距之和。正文首行和评论标题到达 `block: 'start'` 时不得被导航遮住。
- 复用 `.ghost-sm` 及主题变量。若局部规则覆盖其焦点样式，`.doc-quick-nav button:focus-visible` 使用既有 `--focus-ring`（或等效品牌 ring）并有可见 outline/offset；按钮不可只靠 hover 表示可用。
- 默认两个按钮紧凑内联。`@media (max-width: 640px)` 下导航满宽、`display: flex`，每个按钮 `flex: 1`、`min-height: 44px`，文本不裁切、不重叠 `.doc-title` 且不遮住评论表单。
- 不用导航动画表达状态；若新增 transition，则在 `@media (prefers-reduced-motion: reduce)` 下关闭。实际滚动偏好由第 4 节运行时检测决定。

## 6. 数据、路由和模式契约

本变更不修改服务端、模型、数据库、REST、MCP、权限、路由或持久化状态。加载详情仍是既有的文档读取及评论列表读取；点击任何快速定位按钮只能操作已渲染 DOM，不能发出新的 `/api/documents/*` 或评论请求。

| 契约 | 保持不变 |
| --- | --- |
| 路由与状态 | `/documents/:id`、既有深链、URL hash/query、localStorage 均不变 |
| 文档 | get/read、编辑、revision、Markdown/Mermaid 渲染均不变 |
| 评论 | list、添加、编辑、删除、排序、权限、空态与表单均不变 |
| 普通预览外状态 | history、split-edit、split-read、fullscreen 的内容与现有行为不被本改动修改；快速导航和目标均不存在 |

当前没有普通 UI 可进入后三种模式或 history/fullscreen。该可达性缺口记录为独立工作，不以本功能“回归进入/退出”来掩盖；本实现只能确保自身不向这些分支添加控件、id、测试绕道或 API 请求。

## 7. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| fullscreen 覆盖层下背景详情树仍存在 | 完整守卫含 `!docFullscreenOpen()`，并同时控制导航和两类 target 属性；组件测试直接翻转该 signal。 |
| Markdown 无标题、空正文或渲染结果为空 | 目标绑定静态 `article` 外壳，不查询 Markdown 输出；空正文是显式测试数据。 |
| 无评论或评论请求失败 | 目标绑定不依赖 `@for` 的标题，空态和表单仍在；点击目标缺失时静默返回。 |
| `matchMedia` 在 jsdom/旧宿主不可用或行为异常 | `typeof` 特性检测与 try/catch；回退 smooth，不让定位失效。 |
| `scrollIntoView` 在测试宿主不可用 | 特性检测并静默返回；浏览器 E2E 单独验证真实调用/落点。 |
| sticky 遮挡落点 | 两个稳定目标使用同一 scroll margin；E2E 测量目标与导航矩形。 |
| 窄屏或长标题挤压控件 | 标题后独立块、640px 等宽 44px 按钮；390px 浏览器用例。 |
| 旧 fullscreen E2E 依赖已移除入口 | 不将其列为本改动的可执行验收；组件测试覆盖守卫状态，入口恢复后再恢复端到端路径。 |

## 8. 验收与测试计划

实现完成前必须新增/扩展下列自动化，所有浏览器验证记录实际环境；不能以组件或构建通过冒充浏览器验收。

1. 在 `src/frontend/src/app/app.spec.ts` 增加快速定位的组件/单元测试：设定 document view、preview、content 与 `docFullscreenOpen=false` 后，断言 nav、两按钮、两个唯一目标和中文可访问名称出现；逐一设为 history、split-edit、split-read、fullscreen 时，断言 nav 与两个 id/testid 都不渲染。该测试直接设置既有 signals，不新增产品入口。
2. 单元测试在拥有目标的 fixture 中 spy `scrollIntoView`：默认/无 reduce 调用参数为 `{ behavior: 'smooth', block: 'start', inline: 'nearest' }`，reduce 为 `auto`，缺少或抛出 `matchMedia` 不抛错且用 smooth；目标缺失和缺少 `scrollIntoView` 不抛错、不请求 ApiService。
3. 新增 `tests/test_document_detail_quick_navigation_e2e.py`，用临时长 Markdown 与至少一条评论打开普通预览内容页；断言通过可访问名称可找到「正文开头」和「评论开头」，且稳定 testid/id 各唯一。
4. 从页面深处分别激活两个按钮，等待滚动稳定后用 `getBoundingClientRect()` 断言 `#document-content-start` 的正文起点和 `#document-comments-start` 的评论标题到达视口顶部区域，并位于 sticky 导航可见下边缘之后。
5. 创建 `content: ''` 的文档并单独验证：正文容器目标和两个入口存在，正文定位不会产生 page/console error 或额外请求；同时对无评论文档验证评论目标、空态与评论输入表单存在且评论定位可用。评论列表读取失败应作为组件/API-mock 情况验证，不依赖破坏真实服务。
6. Tab 分别聚焦两个按钮，用 Enter 和 Space 激活；断言激活后焦点仍在原按钮且可见 focus ring。浏览器中用 `page.emulate_media(reduced_motion='reduce')` 验证实际可用，并在组件测试精确验证 `auto` 参数；默认偏好验证 smooth 参数。
7. 在 390px viewport 及浅色/深色主题验证导航和按钮可见、可点击、文字未裁切，且评论表单未被 sticky 导航覆盖。每个浏览器场景采集 pageerror、console error 和激活后的网络请求；两个按钮不能增加文档或评论 API 调用。
8. 执行 `cd src/frontend && npm run test -- --run`（或仓库当前等效 Vitest 单次运行命令）和 `npm run build`。运行既有可达的文档模块回归。不要将当前无法通过 UI 到达的 Epic 139/fullscreen E2E 标记为本功能通过；待独立入口恢复后，将其修复并重新纳入端到端回归。

## 9. 完成定义

只有模板、客户端逻辑、样式、组件测试和浏览器覆盖均实现，构建与可执行回归通过，且没有新的 console/page error 或定位触发 API 后，功能才可完成。任何实现不得扩大到恢复模式入口、改动 comment/revision 数据行为或把计划中的验证写成已验证事实。
