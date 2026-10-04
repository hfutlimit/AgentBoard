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
- [ ] 扩展 Epic Tab 的本地请求代次，使同项目强制刷新前的请求不能在 success/error/finally
  覆盖较新请求；保留既有跨项目 `projectTabGeneration` 防护和 retry/loading/error 行为。
- [ ] 为 `EpicsTabComponent` 增加 `searchQuery` 输入、`searchQueryChange` 和 `clearSearch`
  输出；在工具栏加入可访问关键词输入和仅关键词非空时出现的清空按钮，不注入 API 服务。
- [ ] 在路由工作台与标签页工作台两个 `app-epics-tab` 装配点接入相同的关键词输入/输出
  绑定，保留现有状态筛选绑定。

## 验证

- [ ] 在 App 测试覆盖标题/描述匹配、忽略大小写、空白恢复、关键词与状态交集、稳定排序、
  原数组不变、分页复位，以及通用 `search` 不影响 Epic 列表。
- [ ] 覆盖项目切换复位关键词和状态、同项目 Tab 不复位、清空仅保留状态且强制重新加载
  当前项目、无当前项目时不请求、加载失败后的既有 error/retry 投影。
- [ ] 用可控异步请求覆盖同项目强制刷新与旧请求乱序返回、项目切换后旧响应返回，并断言
  不会覆盖较新/当前数据、错误或 loading 状态。
- [ ] 在 `EpicsTabComponent` 测试受控输入、立即事件、空白无清空按钮、非空清空按钮和
  clear 输出，以及既有状态下拉回归。
- [ ] 为两个工作台入口增加绑定级或模板回归测试，确认都将同一关键词值和三个事件合同
  连接到宿主方法，且状态筛选绑定未丢失。
- [ ] 运行 `npm --prefix src/frontend test -- --watch=false`、`npm --prefix src/frontend run build`、
  `openspec validate project-epic-keyword-search --strict` 和 `git diff --check`；记录实际结果，
  不将设计阶段未运行的功能测试当作完成。
