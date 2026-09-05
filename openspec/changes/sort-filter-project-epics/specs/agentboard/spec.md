## ADDED Requirements

### Requirement: 项目工作台的 Epic 派生列表支持默认排序和状态筛选

系统 SHALL 在项目工作台的 Epics 列表中，从当前项目已加载的 Epic 数据派生展示结果。
默认筛选值 SHALL 为“全部状态”；展示结果 SHALL 先按所选状态精确过滤，再按
`created_at` 降序排序，并以 `id` 降序作为创建时间相同的稳定次级排序。派生不得原地
修改已加载 Epic 原始集合，也不得新增后端查询、服务端排序、数据库或状态流转变更。

#### Scenario: 默认显示最新创建的 Epic

- **GIVEN** 当前项目已加载多个 Epic，其中两个 Epic 的 `created_at` 相同且 ID 不同
- **WHEN** 用户首次打开 Epics 工作台且未选择状态
- **THEN** 列表显示当前项目的全部已加载 Epic
- **AND** 较新的 `created_at` 在前
- **AND** 相同 `created_at` 时 ID 较大的 Epic 在前

#### Scenario: 按业务状态收敛并恢复全部结果

- **GIVEN** 当前项目存在不同业务状态的 Epic
- **WHEN** 用户从“全部状态”选择待办、进行中、评审中、完成或已阻塞中的任一项
- **THEN** 列表只显示 `status` 与所选值完全相等的 Epic
- **AND** 筛选后的结果仍按 `created_at DESC, id DESC` 排序
- **WHEN** 用户再选择“全部状态”
- **THEN** 列表恢复全部已加载 Epic，并保持相同排序契约

#### Scenario: 切换状态时分页与既有操作保持正确

- **GIVEN** 用户位于 Epic 列表的非第一页
- **WHEN** 用户切换状态筛选
- **THEN** Epic 列表回到第 1 页
- **AND** 分页总数、切片和空态基于筛选后的完整结果集
- **AND** Epic 进度、详情打开、新建入口、加载态和失败重试保持原有行为

#### Scenario: 遗留 Epic 状态在全部状态下保持可见

- **GIVEN** API 返回不属于本次五个业务筛选值的遗留 Epic 状态
- **WHEN** 用户选择“全部状态”
- **THEN** 该 Epic 仍在列表中且参与默认排序
- **AND** 系统不在此功能中改写该状态或新增状态流转
