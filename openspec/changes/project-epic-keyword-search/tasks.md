# Tasks: 项目 Epic 列表关键词搜索与清空刷新

## 实施

- [ ] 在 `App` 新增不持久化的 `epicSearchQuery`、受控关键词写入口和清空入口；关键词、
  状态变化与清空均将 `epicsPage` 复位为 1。
- [ ] 将 `visibleEpics` 改为只使用规范化的 `epicSearchQuery` 搜索标题/描述，再取状态
  筛选交集，并保留不原地修改的 `created_at DESC, id DESC` 排序；不再调用通用 `match()`。
- [ ] 将项目实际切换时的 Epic 临时条件复位统一为关键词和状态筛选均为空；同项目 Tab
  导航不得清除条件。
- [ ] 实现 `clearEpicSearch()`：只清除关键词、保留状态筛选、复位页码，并用当前项目 ID
  调用既有 `loadProjectTab('epics', projectId, true)`；不得新增或复制 API 请求。
- [ ] 为所有写入共享 `epics` 的 `listEpics` 请求建立递增的共享 Epic 快照代次：
  `overview` 聚合分支和 Epics Tab（含强制刷新）均在实际请求开始时捕获它，并仅在项目与
  快照代次都仍当前时写 `epics`；overview 的非 Epic 聚合结果继续仅按原项目代次处理。
- [ ] 保留并扩展 Epics Tab 专用状态代次，使同项目强制刷新前的 Epics Tab 请求不能在
  success/error/finally 覆盖较新 Tab 请求的 loaded/loading/error；将共享快照代次传入
  `loadEpicProgressData`，并在 `stories` 和 `tasks` 每次写入前校验，防止旧进度链污染当前
  Epic 进度。保留既有跨项目 `projectTabGeneration` 防护和 retry 行为。
- [ ] 将 `openProposalModal()` 的目标 Epic 候选迁移到专用 `proposalModalEpics`（或等价）
  signal，并修改弹窗下拉只读取该信号，不再写入或读取共享 `epics`。请求开始时捕获项目
  代次和递增的弹窗加载代次；仅当前请求可写候选、显示失败提示、初始化草稿/项目/目标 Epic
  并打开弹窗。项目切换必须使该请求失效、清空候选并关闭/复位旧项目弹窗；以 `epics.set(`
  复核所有共享写入者，保持首页 `loadDashboardFullTree()` 的首页路由守卫和其他弹窗专用
  候选信号边界。
- [ ] 为 `EpicsTabComponent` 增加 `searchQuery` 输入、`searchQueryChange` 和 `clearSearch`
  输出；在工具栏加入可访问关键词输入和仅关键词非空时出现的清空按钮，不注入 API 服务。
- [ ] 在路由工作台与标签页工作台两个 `app-epics-tab` 装配点接入相同的关键词输入/输出
  绑定，保留现有状态筛选绑定。

## 验证

- [ ] 在 App 测试覆盖标题/描述匹配、忽略大小写、空白恢复、关键词与状态交集、稳定排序、
  原数组不变、分页复位，以及通用 `search` 不影响 Epic 列表。
- [ ] 覆盖项目切换复位关键词和状态、同项目 Tab 不复位、清空仅保留状态且强制重新加载
  当前项目、无当前项目时不请求、加载失败后的既有 error/retry 投影。
- [ ] 用可控异步请求覆盖同项目强制刷新与旧 Epics Tab 请求乱序返回、旧 `overview`
  Epic 响应晚于强制刷新、旧快照的 stories/tasks 链晚于新快照，以及项目切换后旧响应
  返回；断言旧响应不会覆盖较新/当前 `epics`、`stories`、`tasks`、错误或 loading，且
  overview 的非 Epic 聚合结果仍遵循原项目代次。
- [ ] 覆盖 `openProposalModal()` 的两类受控竞态：旧弹窗 Epic 响应晚于清空强制刷新，以及
  项目切换后旧弹窗响应（含失败）返回。断言共享 `epics` 不被写入，旧项目弹窗不打开、不被
  初始化且不显示过期错误；当前请求使用专用候选正常初始化。
- [ ] 在 `EpicsTabComponent` 测试受控输入、立即事件、空白无清空按钮、非空清空按钮和
  clear 输出，以及既有状态下拉回归。
- [ ] 为两个工作台入口增加绑定级或模板回归测试，确认都将同一关键词值和三个事件合同
  连接到宿主方法，且状态筛选绑定未丢失。
- [ ] 运行 `npm --prefix src/frontend test -- --watch=false`、`npm --prefix src/frontend run build`、
  `openspec validate project-epic-keyword-search --strict` 和 `git diff --check`；记录实际结果，
  不将设计阶段未运行的功能测试当作完成。
