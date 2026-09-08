## ADDED Requirements

### Requirement: 工作台 Tab 携带层级元信息

每个工作台 Tab SHALL 携带 `level`（`section` / `epic` / `proposal` / `story` / `task`）与 `accent` 色标，并 MAY 携带 `parentId` / `parentTitle` 以表达所属父级。系统 SHALL 在详情数据加载完成后回填 `parentId`：Story 的父级为其所属 Epic，Task 的父级优先为其所属 Story，无 Story 数据时退到其所属 Epic。

#### Scenario: 实体 Tab 携带正确的层级与色标

- **GIVEN** 项目工作台中打开了一个 Epic Tab、一个 Story Tab 和一个 Task Tab
- **WHEN** 渲染 Tab 条
- **THEN** Epic Tab 的 `level` 为 `epic` 且 `accent` 为 `epic`
- **AND** Story Tab 的 `level` 为 `story` 且 `accent` 为 `story`
- **AND** Task Tab 的 `level` 为 `task` 且 `accent` 为 `task`
- **AND** Section Tab 的 `level` 为 `section` 且 `accent` 为 `neutral`

#### Scenario: 详情加载后回填父子关系

- **GIVEN** 一个 Task Tab 已打开，但创建时尚不知道其所属 Story
- **WHEN** 该 Task 的详情数据加载完成且包含有效 `story_id`
- **THEN** 该 Task Tab 的 `parentId` 指向对应 Story Tab
- **AND** `parentTitle` 为该 Story 的标题

### Requirement: Tab 条按父子关系分组并缩进

系统 SHALL 按以下规则渲染 Tab 条：固定 Tab 置顶；其余以「无父级的 Tab」为根深度优先展开，子 Tab 紧随其父 Tab 之后，缩进深度为父深度 + 1 且封顶 2；父 Tab 未打开或 `parentId` 失效的 Tab 按打开顺序补在末尾并缩进一级。子 Tab 比父 Tab 先打开时，系统 SHALL 仍将子 Tab 归位到父 Tab 之后。

#### Scenario: 子 Tab 聚拢到父 Tab 之后

- **GIVEN** 依次打开 `backlog`、`Epic 31`、`Story 434`（父为 Epic 31）、`Task 1001`（父为 Story 434）
- **WHEN** 渲染 Tab 条
- **THEN** 顺序为 `backlog`、`Epic 31`、`Story 434`、`Task 1001`
- **AND** 四者的缩进深度分别为 0、0、1、2

#### Scenario: 子 Tab 先于父 Tab 打开时仍然归位

- **GIVEN** 先打开 `Task 1001`（父为 `Epic 31`），再打开 `Epic 31`
- **WHEN** 渲染 Tab 条
- **THEN** 顺序为 `Epic 31`、`Task 1001`
- **AND** `Task 1001` 的缩进深度为 1

#### Scenario: 父 Tab 未打开时子 Tab 仍保留缩进

- **GIVEN** 只打开了一个 `parentId` 指向未打开 Story 的 Task Tab
- **WHEN** 渲染 Tab 条
- **THEN** 该 Tab 仍然渲染
- **AND** 其缩进深度为 1
- **AND** 其不标记为已分组

#### Scenario: Tab 的可访问名称包含完整父级路径

- **GIVEN** 一个 Task Tab 的 `parentTitle` 为 `Story A`，标题为 `Task · QA`
- **WHEN** 渲染该 Tab
- **THEN** 其 `aria-label` 与 `title` 为 `Story A › Task · QA`

### Requirement: 固定 Tab 置顶且不被批量关闭清除

系统 SHALL 允许固定任一 Tab。固定 Tab SHALL 排在 Tab 条最前。执行「关闭其他」时系统 SHALL 保留目标 Tab 与所有固定 Tab；执行「全部关闭」时系统 SHALL 保留所有固定 Tab。固定状态 SHALL 按项目持久化，切换项目后重新进入该项目时应恢复。

#### Scenario: 固定项置顶并在批量关闭中保留

- **GIVEN** 打开了 `overview` 与 `Epic 31`，且 `Epic 31` 已固定
- **WHEN** 执行「全部关闭」
- **THEN** 仅保留 `Epic 31`
- **AND** 取消固定后再次「全部关闭」将清空所有 Tab

#### Scenario: 固定状态按项目隔离

- **GIVEN** 在项目 7 中固定了 `overview`
- **WHEN** 切到项目 8 并打开 `overview`
- **THEN** 该 Tab 不是固定状态
- **AND** 切回项目 7 后重新打开的 `overview` 恢复为固定状态

### Requirement: 记录并恢复最近访问的工作上下文

系统 SHALL 记录最近打开的实体（Epic / Proposal / Story / Task），上限 10 条，包含类型、标题、父级路径与直链路径。重新激活已记录的实体 SHALL 将其移到最前而不是新增重复条目。Section Tab SHALL NOT 进入最近访问。最近访问 SHALL 按项目持久化，切换项目后重新进入时恢复。

#### Scenario: 记录实体访问并可重新打开

- **GIVEN** 依次打开 `Story 434` 与 `Task 1001`
- **WHEN** 打开「最近」下拉
- **THEN** 列表为 `Task 1001`、`Story 434`（最新在前）
- **AND** `Story 434` 的直链路径为 `/project/<projectId>/stories/434`
- **AND** 点击任一条目可重新打开该实体

#### Scenario: 重新激活不产生重复条目

- **GIVEN** 最近访问中已有 `Story 434` 与 `Task 1`
- **WHEN** 再次激活 `Story 434`
- **THEN** 最近访问顺序变为 `Story 434`、`Task 1`
- **AND** 条目总数不变

#### Scenario: Section Tab 不进入最近访问

- **GIVEN** 打开了 `overview` 与 `backlog`
- **WHEN** 检查最近访问
- **THEN** 最近访问为空

### Requirement: Task 默认在右侧抽屉打开

系统 SHALL 在用户点击 Task 实体时默认于右侧抽屉打开，而不新增一级 Tab。抽屉 SHALL 复用与实体 Tab 相同的数据加载链路。抽屉 SHALL 提供「在 Tab 中打开」以将当前实体提升为常驻 Tab。系统 SHALL 支持通过 Esc、点击遮罩关闭抽屉，并允许拖拽调整宽度且记忆该宽度。用户 SHALL 能够将 Task 的默认点击行为切换为「新 Tab」，该偏好 SHALL 持久化。Epic / Story / Proposal 的点击行为 SHALL 保持为打开实体 Tab。

#### Scenario: 点击 Task 打开抽屉而不新增 Tab

- **GIVEN** 项目工作台中 Tab 条已有若干条目
- **WHEN** 用户在工作项列表中点击一个 Task
- **THEN** 右侧抽屉打开并展示该 Task 详情
- **AND** Tab 条条目数量不变
- **AND** 地址栏更新为该 Task 的直链路径

#### Scenario: 从抽屉提升为常驻 Tab

- **GIVEN** 抽屉中展示某个 Task
- **WHEN** 用户点击「在 Tab 中打开」
- **THEN** Tab 条新增该 Task 的常驻条目
- **AND** 抽屉关闭
- **AND** 该 Tab 的标题不包含重复的 `Task · Task ·` 前缀

#### Scenario: 激活常驻 Tab 时关闭抽屉

- **GIVEN** 抽屉处于打开状态
- **WHEN** 用户点击任意一个常驻 Tab
- **THEN** 抽屉关闭
- **AND** 该 Tab 成为激活上下文

#### Scenario: Task 直链与刷新遵循同一偏好

- **GIVEN** Task 默认行为设为抽屉
- **WHEN** 用户直接访问或刷新 `/project/<projectId>/tasks/<taskId>`
- **THEN** 该 Task 在抽屉中打开
- **AND** 不新增常驻 Tab

#### Scenario: 可切换 Task 的默认点击行为

- **GIVEN** 用户在 Tab 条「更多」菜单中切换 Task 点击行为
- **WHEN** 切换为「新 Tab」后点击一个 Task
- **THEN** 系统直接打开常驻 Tab，不打开抽屉
- **AND** 该偏好在刷新后保留
