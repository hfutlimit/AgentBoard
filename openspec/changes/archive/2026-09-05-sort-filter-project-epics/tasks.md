# Tasks: 项目 Epic 列表默认排序与状态筛选

## 完成记录

- [x] 在 `app.ts` 实现白名单状态筛选、项目切换复位和不原地修改的 `created_at DESC, id DESC` 派生排序。
- [x] 在 `EpicsTabComponent` 及两个工作台装配点接入“全部状态”与五个业务状态的受控下拉和事件。
- [x] 增加 App 派生数据覆盖与 EpicsTab 下拉渲染/事件覆盖；聚焦 Vitest 为 65 passed、1 skipped。
- [x] `npm run build` 通过（仅保留现有 initial bundle budget 警告）。

## 实现

- [x] 在 `app.ts` 定义 Epic 列表专用的五值筛选类型/常量、`epicFilterStatus` signal 和
  白名单化的 `setEpicFilterStatus`；项目切换时清空该临时筛选。
- [x] 将 `visibleEpics` 改为“保留既有文本匹配 → 精确状态过滤 → 新数组
  `created_at DESC, id DESC` 排序”的单一派生数据源；不得原地排序 `epics()`。
- [x] 在 `EpicsTabComponent` 增加 `filterStatus`、`statuses` 输入与 `filterStatusChange` 输出；
  在工具栏加入带标签的“全部状态 + 五个业务状态”下拉。
- [x] 在 `project-workspace-route` 和 `project-workspace-shell/tab-pane` 两个装配点接入相同筛选状态、
  状态选项和事件处理器。
- [x] 保持 `ManagedListComponent` 的 total、分页、空态、加载和错误输入基于派生结果；确认
  进度、详情、新建入口与重试回归不变。

## 自动化验证

- [x] 在 `src/frontend/src/app/app.spec.ts` 覆盖默认排序、同时间戳 ID 次级排序、每个状态过滤、
  恢复全部状态、分页复位、输入数组不变及历史状态兼容。
- [x] 覆盖 Epic 工作台下拉的默认值、五个选项、change 事件和筛选空态；两个装配路径均应有
  绑定级验证或由共享路由渲染测试覆盖。
- [x] 在 `src/frontend` 执行聚焦 Vitest 测试和 `npm run build`，记录实际命令、通过数和构建结果。
- [x] 执行 `openspec validate sort-filter-project-epics --strict` 与 `git diff --check`。

## 验收标准

- [ ] 默认显示当前项目全部 Epic，严格按创建时间从新到旧；相同时间戳按 ID 从大到小。
- [ ] 状态下拉默认“全部状态”；五个业务选项的精确筛选正确，切回全部恢复完整且排序正确。
- [ ] 在非第一页切换筛选后回到第 1 页，分页总数、结果和空态一致。
- [ ] 筛选/排序不影响进度、badge、详情、新建、加载失败重试或其他工作台 Tab。
- [ ] 无后端 API、数据库、状态流转或本地持久化改动；`backlog` / `verifying` 兼容边界符合
  `proposal.md` 的明确约定。
