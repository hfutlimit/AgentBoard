# Design: 项目 Epic 列表默认排序与状态筛选

## 1. 决策摘要

- 筛选状态由 `App` 持有；`EpicsTabComponent` 是无状态展示组件，仅接收值并发出变更事件。
- `visibleEpics` 是唯一的“筛选后、排序后”数据源；分页继续由现有
  `ManagedListComponent` / `paginatedItems` 在该结果集上执行。
- 排序在客户端、派生副本上完成：`created_at DESC`，同时间戳 `id DESC`。绝不对
  `this.epics()` 原数组调用原地 `sort()`。
- 选择筛选项立即把 `epicsPage` 设为 `1`；不持久化筛选值。

## 2. 状态和数据契约

实现新增局部类型和常量，不复用包含历史值的共享 `Status` 全集作为下拉选项：

```ts
type EpicListFilterStatus = '' | 'todo' | 'in_progress' | 'in_review' | 'done' | 'blocked';

const EPIC_LIST_FILTER_STATUSES: Exclude<EpicListFilterStatus, ''>[] = [
  'todo', 'in_progress', 'in_review', 'done', 'blocked',
];
```

`App` 的状态与写入入口：

```ts
readonly epicFilterStatus = signal<EpicListFilterStatus>('');

setEpicFilterStatus(status: EpicListFilterStatus): void {
  this.epicFilterStatus.set(status);
  this.epicsPage.set(1);
}
```

只允许模板从受控 `EPIC_LIST_FILTER_STATUSES` 发出上述值；若实现选择在 handler 中作
运行时白名单保护，非法值必须回退 `''`，而不是写入任意字符串。切换项目时，现有
`resetProjectListPages()` 仍只负责分页；因为筛选不持久化且这是项目工作台局部选择，
实现应在项目 ID 切换时同时复位 `epicFilterStatus` 为 `''`，避免把前一项目的临时条件
带到另一项目。

## 3. 派生顺序、异常值和分页

`visibleEpics` 取代现有仅文本匹配的 computed。其顺序必须固定为：

1. 从 `this.epics()` 取得数据；先执行现有文本匹配，使已有全局/列表搜索语义不丢失。
2. 若 `epicFilterStatus()` 非空，仅保留 `epic.status === selectedStatus` 的项。
3. 对新数组排序，不变更步骤 1 的数组：`created_at` 解析为 Unix 毫秒并降序比较；时间
   相同以数值 `id` 降序比较。

建议的比较器（`created_at` 正常契约为可解析 ISO 字符串）：

```ts
const createdMillis = (value: string): number => {
  const milliseconds = Date.parse(value);
  return Number.isFinite(milliseconds) ? milliseconds : Number.NEGATIVE_INFINITY;
};

return [...matched]
  .filter((epic) => !selectedStatus || epic.status === selectedStatus)
  .sort((left, right) =>
    createdMillis(right.created_at) - createdMillis(left.created_at) || right.id - left.id,
  );
```

无效时间戳不应抛出或令排序不确定：把它当作最旧时间，并以 `id DESC` 打破并列。该分支是
防御性行为，不改变 API 的非空 ISO 时间契约。

`EpicsTabComponent.paginatedItems(epics, page)` 不改签名：`epics` 已经是筛选、排序后的
完整集合，故 `total`、空态判断和切片天然一致。`setEpicFilterStatus` 先重置页码可避免
在第 N 页筛选后落在不存在的页；现有 clamp 仍作为防御。

## 4. 组件和装配契约

`EpicsTabComponent` 新增：

```ts
@Input() filterStatus: EpicListFilterStatus = '';
@Input() statuses: readonly EpicListFilterStatus[] = [];
@Output() filterStatusChange = new EventEmitter<EpicListFilterStatus>();
```

在 `ml-toolbar` 插槽加入可访问的 `<label>` + `<select id="project-epic-status-filter">`：

- 首项 `value=""`、文案“全部状态”；
- 其余五项以 `statusLabel` 呈现中文文案；
- `[value]` 绑定 `filterStatus`；`change` 只 emit 受控值；
- 沿用现有 `doc-toolbar` / `doc-filter-select` 样式，除非视觉验收显示必须补最小组件 CSS。

以下两个仍在源码中渲染 `app-epics-tab` 的位置必须同改，并向组件传入同一个父状态：

1. `project-workspace-route/project-workspace-route.html`
2. `project-workspace-shell/tab-pane/tab-pane.html`

绑定形式为 `[filterStatus]="host.epicFilterStatus()"`、`[statuses]="host.epicListFilterStatuses"`
和 `(filterStatusChange)="host.setEpicFilterStatus($event)"`。这样路由工作台和保持存活的
shell Tab 不会出现一处能筛选、一处不能筛选的行为分叉。

## 5. 保持不变的行为

- `loadProjectTab('epics', projectId, ...)` 仍调用原有 `listEpics(projectId)`；没有状态 query
  参数、重新请求或服务端排序依赖。
- Epic 进度继续通过同一 `epicProgressFor` 函数按 `item.id` 查询。
- `<a>` 的 href、`openEpic`、新建 Epic、加载骨架、失败重试和空项目引导保持原行为。
- 不把筛选写入 localStorage，不改变状态 badge/编辑表单，也不改变 `Epic.status`。

## 6. 测试设计与验收数据

在 `app.spec.ts` 新增聚焦数据层测试，种子至少包含：五个可筛选状态、两个完全相同
`created_at` 但不同 `id` 的 Epic，以及一个较旧 Epic。断言：

| 场景 | 断言 |
| --- | --- |
| 默认 | `epicFilterStatus()` 为 `''`；结果按 `created_at DESC, id DESC`，同时间戳 ID 大者在前。|
| 各状态 | 每个五值筛选只保留完全匹配状态，且结果仍遵循相同排序。|
| 恢复全部 | 从任一状态切回 `''` 后返回全部种子结果，顺序不变。|
| 分页复位 | 先设 `epicsPage` 为大于 1 的值，调用筛选入口后为 `1`。|
| 不可变性 | 调用 `visibleEpics()` 前后 `epics()` 的原始 ID 顺序相同。|
| 历史兼容 | `backlog`/`verifying`（若用类型窄化可通过测试夹具转换）在“全部状态”仍显示且参与排序；五值筛选不误匹配它们。|

增加组件/工作台渲染断言：下拉默认显示“全部状态”、有且仅有五个指定状态选项、change
事件更新父筛选并回到第一页、筛选为空时显示既有空态且不显示错误态。实现完成后执行
`npm test -- --runInBand`（若项目 runner 不接受该参数，则使用其等价单次 Vitest 命令）和
`npm run build`；本设计阶段不执行功能测试，因为尚未实现。
