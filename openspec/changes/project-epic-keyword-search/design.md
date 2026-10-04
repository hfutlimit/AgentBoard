# Design: 项目 Epic 列表关键词搜索与清空刷新

## 1. 目标、当前基线与范围

本设计为项目工作台的 `Epics` 页增加项目内关键词搜索，不实现功能本身。当前
`App.visibleEpics` 已从 `epics()` 派生数据，并按 `epicFilterStatus` 筛选、按
`created_at DESC, id DESC` 排序；但它先调用通用 `match()`，而 `match()` 读取宿主
`search` signal。该耦合不符合 Epic 页临时筛选条件不可跨页面泄漏的要求。

`EpicsTabComponent` 是无状态展示组件，已有状态筛选输入/输出；项目路由工作台和
保持存活的标签页工作台分别在下列位置装配该组件：

- `project-workspace-route/project-workspace-route.html`
- `project-workspace-shell/tab-pane/tab-pane.html`

当前 `loadProjectTab('epics', projectId, force)` 已通过 `listEpics(projectId)` 加载数据，
以 `projectTabGeneration` 拒绝项目切换后的旧响应，并把 loading、error、retry 投影给
`EpicsTabComponent`。本设计复用该路径；不新增网络合同。

### 目标

1. 输入标题或描述中的关键词后，立即在当前已加载项目 Epic 集合中筛选，忽略大小写，
   不请求网络、不防抖。
2. 关键词和状态筛选取交集，且始终保持既有 newest-first 稳定排序。
3. 清空仅移除关键词、复位分页并强制重新加载当前项目 Epic；状态筛选和既有请求/错误
   交互保持有效。
4. 项目切换重置两个 Epic 临时条件；两个工作台入口的行为一致。
5. 旧项目或较早的同项目强制请求永远不能覆盖当前强制刷新结果。

### 非目标

- 不搜索未加载的 Epic，不添加 API `q` 参数、服务端过滤、缓存、localStorage 或 debounce。
- 不修改全局 `search` 的其他消费者；Epic 页只是不再消费它。
- 不改变状态筛选五个受控选项、历史状态在“全部状态”下可见的兼容边界、分页组件、
  进度、详情/新建入口、后端或数据库。
- 不把网络访问、请求防护或应用状态移入 `EpicsTabComponent`。

## 2. 状态和派生数据合同

### 2.1 宿主状态

`App` 新增如下不持久化状态和写入口；原有 `epicFilterStatus` 与
`setEpicFilterStatus` 保留：

```ts
readonly epicSearchQuery = signal('');

setEpicSearchQuery(query: string): void {
  this.epicSearchQuery.set(query);
  this.epicsPage.set(1);
}
```

信号保存输入框原始文本，以使 UI 受控；用于业务判断的唯一值为：

```ts
const query = this.epicSearchQuery().trim().toLocaleLowerCase();
```

因此空串和仅空白字符串都没有关键词语义；它们均显示状态筛选后的完整集合，且清空
按钮不出现。标题或描述为缺失值时实现须按空串处理，避免对历史 API 数据抛错；正常
`Epic` 合同的字符串字段不因此改变。

`visibleEpics` 是唯一传入列表组件的完整派生结果，按以下固定顺序运行：

1. 从 `this.epics()` 取得当前项目已加载集合；不调用 `match()`，也不读取通用 `search`。
2. 若规范化 `query` 非空，保留其规范化 `title` 或 `description` 包含 `query` 的项。
3. 若 `epicFilterStatus()` 非空，保留 `status` 精确相等的项。
4. 在副本上按 `created_at DESC`、相同或不可解析时间以 `id DESC` 排序。

步骤 2 与 3 是 AND；排序只作用于新数组，绝不原地修改 `this.epics()`。延续既有防御
行为：不可解析的 `created_at` 当作最旧时间，然后由 ID 打破并列。分页、total 和空态
继续读取此完整派生数组，因此不需要改变 `ManagedListComponent` 的签名。

### 2.2 状态转换

| 用户/系统事件 | `epicSearchQuery` | `epicFilterStatus` | `epicsPage` | 请求 |
| --- | --- | --- | --- | --- |
| 输入任意文本 | 写入原文 | 保留 | 设为 1 | 无 |
| 输入仅空白 | 写入原文，派生视为无关键词 | 保留 | 设为 1 | 无 |
| 改变状态筛选 | 保留 | 写入受控状态 | 设为 1 | 无 |
| 点击清空（仅规范化关键词非空） | 设为 `''` | 保留 | 设为 1 | 强制加载当前项目 Epic |
| 切换项目 | 设为 `''` | 设为 `''` | 设为 1 | 由目标 Tab 原有加载触发 |

项目切换处目前同时调用 `resetProjectListPages()`、清空 `epicFilterStatus` 与
`resetProjectTabs()`。实现应将两个 Epic 临时筛选的复位收敛到明确的私有 helper（或在
该同一受控分支中同时设置），保证从任意项目路由进入时都不会遗漏 `epicSearchQuery`。
该复位只发生在项目 ID 实际不同的分支，不能在同项目 Tab 导航时清除用户条件。

## 3. 清空与强制刷新的请求合同

### 3.1 宿主操作

`clearEpicSearch()` 由宿主拥有，组件只发出 `clearSearch`。实现顺序固定为：

1. 将关键词设为空并把 `epicsPage` 设为 `1`；不调用 `setEpicFilterStatus`，因此状态
   条件必须保持。
2. 读取当前 `project()?.id`。若当前没有项目，只完成本地清空且不发请求。
3. 若存在项目，调用既有 `loadProjectTab('epics', projectId, true)`。`true` 是必要的：
   已加载的 Tab 不能因为 `projectTabLoaded.epics` 为真而跳过刷新。

不得复制 `listEpics` 调用、直接设置 loading/error，或添加搜索 endpoint。`loadProjectTab`
仍在请求开始时清空该 Tab 的旧错误、设置 loading，成功时更新 `epics` 并触发既有进度加载，
失败时保留其错误投影和 retry；清空操作不吞掉或另行提示错误。

### 3.2 新旧响应防护

现有 `projectTabGeneration` 必须继续作为跨项目防护：项目切换后返回的请求不能写入新
项目。由于 `force=true` 可以在同一项目已有 Epic 请求尚未结束时再发一个请求，单靠项目
generation 无法决定两个同项目响应的新旧。本变更在 App 内为 Epic Tab 增加递增的本地
请求代次（例如 `epicLoadGeneration`），不是 API 版本、缓存或持久化状态。

每次实际开始 `loadProjectTab('epics', ...)` 时捕获该代次；只有下列条件同时成立，才可
设置 `epics`、启动该批进度加载、写错误、修改 loaded/loading 状态：

```ts
this.isCurrentProjectTabRequest(projectId, projectGeneration) &&
requestGeneration === this.epicLoadGeneration
```

其他 Tab 沿用现有项目 generation 逻辑。新项目切换通过既有 `projectTabGeneration` 使旧
项目失效；同项目较早请求则因 Epic request generation 失效。实现必须保证失效请求的
`finally` 不会把较新请求的 loading 状态提前设为 false，失效请求的失败也不会覆盖较新
成功结果。这样清空后真正完成的强制刷新数据才是可见结果，且现有 retry 仍可重新走该
受保护路径。

## 4. `EpicsTabComponent` 展示合同

组件新增以下公开输入/输出，继续不注入任何服务：

| 名称 | 方向/类型 | 规则 |
| --- | --- | --- |
| `searchQuery` | `@Input() string` | 受控的 Epic 专属原始输入值；默认 `''`。|
| `searchQueryChange` | `@Output() EventEmitter<string>` | 每次输入立即发出完整当前字符串，不防抖、不请求。|
| `clearSearch` | `@Output() EventEmitter<void>` | 用户点击清空按钮时只发事件；组件不修改输入、不加载数据。|

工具栏状态下拉保留，并新增关联的可访问关键词 `<label>` 和 `type="search"`（或文本
等价控件）输入。输入必须具备稳定 id，例如 `project-epic-search`，其可访问名称明确
说明搜索范围为 Epic。状态下拉和输入都保持键盘可操作。

当 `searchQuery.trim()` 非空时，组件呈现 `type="button"` 的清空按钮（稳定 id，例如
`project-epic-search-clear`）；其可访问名称为“清空 Epic 搜索”。关键词为空或仅空白时，
该按钮不渲染，因而不可操作。按钮只 emit `clearSearch`。布局复用当前 `doc-toolbar` /
`doc-filter-select` 视觉体系；如需额外 CSS，只对 `.epics-tab-toolbar` 作用域添加最小
规则，确保窄屏输入、状态选择和清空按钮可见且可访问。

组件仍将父级提供的 `epics` 作为唯一列表/分页/空态来源。搜索得到零项时，现有空态会
显示，这是筛选结果为空而非网络错误；loading 或 error 优先级不变。

## 5. 两个装配入口合同

下列两个 `app-epics-tab` 必须同步加入相同四个绑定，任何一处遗漏都视为回归：

```html
[searchQuery]="host.epicSearchQuery()"
(searchQueryChange)="host.setEpicSearchQuery($event)"
(clearSearch)="host.clearEpicSearch()"
```

原有 `[filterStatus]`、`[statuses]` 和 `(filterStatusChange)` 绑定保持不变。两个入口都
从同一 `App` 宿主读取状态、调用同一 handler；不得在其中一个入口直接 `.set()` signal，
否则分页复位和强制刷新合同会分叉。

## 6. 实施边界

预期修改文件：

- `src/frontend/src/app/app.ts`
- `src/frontend/src/app/epics-tab/epics-tab.ts`
- `src/frontend/src/app/epics-tab/epics-tab.html`
- 必要时 `src/frontend/src/app/epics-tab/epics-tab.css`
- `src/frontend/src/app/project-workspace-route/project-workspace-route.html`
- `src/frontend/src/app/project-workspace-shell/tab-pane/tab-pane.html`
- `src/frontend/src/app/app.spec.ts`
- `src/frontend/src/app/epics-tab/epics-tab.spec.ts`
- 必要的两个工作台入口模板/宿主测试文件。

不修改 `ApiService.listEpics`、后端路由、数据库、OpenSpec 当前 capability spec 或部署配置。
当前 spec 仅在实现完成并归档变更时才更新；设计阶段不能把未实现行为写成现状。

## 7. 测试设计与验收

### 单元与组件测试

1. App 夹具包含标题唯一命中、描述唯一命中、大小写不同命中、无命中、空白查询和多种
   状态的 Epic。验证标题/描述不区分大小写匹配，空白恢复当前状态下完整结果，且结果
   仍为 `created_at DESC, id DESC`，原始 `epics()` 顺序不变。
2. 验证关键词与每个状态筛选取交集；设置非第一页后，输入关键词、仅空白输入、状态变化
   和清空均将 `epicsPage` 置为 `1`。明确断言更改通用 `search` 不影响 `visibleEpics`。
3. 验证项目 ID 改变时关键词和状态筛选均复位；同一项目内切换 Tab 不复位。
4. 使用可控的 `listEpics` Observable/Promise 验证：输入搜索不调用它；点击清空只调用
   一次带当前项目 ID 的强制 Epic Tab 加载；状态筛选未变；成功数据参与派生；失败后保留
   既有 error 与 retry 可用。若没有当前项目，清空不得请求。
5. 构造重叠请求：同项目旧请求后返回、清空强制请求先返回，断言最终数据/error/loading
   均来自强制请求；再切换项目并完成旧请求，断言它不能写入新项目。覆盖 success、error
   和 finally 分支，防止旧请求解除新请求 loading。
6. `EpicsTabComponent` 测试输入值反映到控件、每个 input 事件原样 emit、仅规范化关键词
   非空时存在清空按钮、点击只 emit、空白时无按钮，并确认现有状态选择输出不回归。
7. 对两个工作台入口分别做绑定级测试或静态模板回归，确认三项关键词绑定存在且调用同一
   宿主方法；两个入口继续保留状态筛选绑定。

### 完成时执行的验证

实现阶段至少运行：

```powershell
npm --prefix src/frontend test -- --watch=false
npm --prefix src/frontend run build
openspec validate project-epic-keyword-search --strict
git diff --check
```

本设计任务未实现前端功能，故本次只验证 OpenSpec 工件的严格格式和 Git 差异；不能把
尚未运行的 Vitest、构建或浏览器验收表述为已通过。

### 验收映射

| 业务验收 | 设计保证 |
| --- | --- |
| 标题/描述、大小写搜索 | 规范化后的本地 title/description 包含匹配与夹具断言。|
| 空白恢复完整且顺序稳定 | `trim()` 空语义，随后状态筛选与不可变稳定排序。|
| 与状态组合、变更回第一页 | 固定派生顺序与统一 handler 状态表。|
| 清空后刷新 | 宿主 `clearEpicSearch` 先复位、后走 `loadProjectTab(..., true)`。|
| 清空保留状态、项目切换全复位 | 单独的状态转换与路由切换断言。|
| 失败/retry/旧响应安全 | 复用 Tab 状态投影并为同项目强制请求补充本地请求代次。|
| 双入口一致 | 两处同样的 input/output 绑定与回归测试。|

## 8. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 误用通用 `search`，导致跨页面筛选泄漏 | `visibleEpics` 只读取 `epicSearchQuery`；单测改动通用 search 仍无影响。|
| 清空只是本地修改，服务端更新未参与 | 清空事件只能进入宿主强制 Tab 加载路径；测试断言 `listEpics(currentProjectId)`。|
| 同项目旧请求晚到覆盖刷新结果 | 给 Epic Tab 请求增加本地递增代次，应用于 success/error/finally。|
| 组件自行请求导致路由与标签页不一致 | 组件只投影输入/输出；两个入口调用同一 App handler。|
| 关键词与状态先后顺序不同造成结果/分页不一致 | 单一 `visibleEpics` 及两个 handler 的固定复位规则。|
| 空白查询误显示清空或无结果 | 所有业务判断使用 `trim()`；保存原始值仅为受控输入。|
