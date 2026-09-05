## ADDED Requirements

### Requirement: 提案详情展示已生成 Story 的可访问链接

系统 SHALL 在提案详情的项目工作台入口和根路由入口，以相同的 Story 引用解析规则显示已生成 Story 的业务链接。解析 SHALL 优先使用正整数 `proposal.story_id`；仅当该字段为 `null` 或 `undefined` 时，才可在 `proposal.ticket_type` 严格等于 `story` 且 `proposal.ticket_id` 为正整数时使用 `ticket_id`。系统 SHALL 不从 TicketRequest、状态文本、标题、Epic、Task、Bug 或其他字段推断 Story。无有效 Story 引用时，系统 SHALL 保留当前非 Story 工单或无引用的展示，不得创建 Story 链接。

#### Scenario: 兼容字段优先于通用 Ticket 字段

- **GIVEN** 一个终态提案的 `story_id` 为 `123`，且其 Ticket 字段为空或指向不同的工单
- **WHEN** 用户打开任一提案详情入口
- **THEN** 页面显示“查看 Story #123”的链接
- **AND** 该链接的真实 `href` 为 `/story/123`
- **AND** 系统不使用 Ticket 字段替换该 Story 引用

#### Scenario: 缺失兼容字段时仅回退到 Story Ticket

- **GIVEN** 一个终态提案的 `story_id` 为 `null` 或 `undefined`，`ticket_type` 为 `story`，且 `ticket_id` 为正整数 `456`
- **WHEN** 用户打开任一提案详情入口
- **THEN** 页面显示“查看 Story #456”的链接
- **AND** 该链接的真实 `href` 为 `/story/456`

#### Scenario: 非 Story Ticket 或无效引用不被误链接

- **GIVEN** 一个终态提案没有有效 `story_id`
- **AND** 其 `ticket_type` 为 `epic`、`task`、`bug`、未知类型或空值，或者 `ticket_id` 不是正整数
- **WHEN** 用户打开任一提案详情入口
- **THEN** 系统不显示 Story 链接
- **AND** 现有通用工单展示保持不变

#### Scenario: 非空但无效的兼容字段不触发回退

- **GIVEN** 一个终态提案的 `story_id` 非空但不是正整数
- **AND** 它的 `ticket_type` 为 `story` 且 `ticket_id` 是正整数
- **WHEN** 用户打开任一提案详情入口
- **THEN** 系统不显示 Story 链接
- **AND** 系统不以 `ticket_id` 覆盖该损坏的兼容字段

### Requirement: Story 链接遵循入口对应的导航语义

系统 SHALL 为两个入口都输出真实的 `/story/<storyId>` href，以支持键盘访问、复制链接、上下文菜单和浏览器原生新标签操作。项目工作台入口 SHALL 仅拦截无 Ctrl、Cmd、Shift 修饰键的主键点击，并使用既有实体 Tab 机制打开或激活 Story；根路由入口 SHALL 使用既有 Angular 路由行为。带修饰键点击和中键点击 SHALL 不被工作台代码阻止。

#### Scenario: 工作台普通左键打开或复用 Story 实体 Tab

- **GIVEN** 项目工作台中的提案详情显示一个有效 Story 链接
- **WHEN** 用户以无修饰键的主键点击该链接
- **THEN** 系统阻止该链接的浏览器默认导航
- **AND** 系统通过既有实体 Tab 机制打开或激活该 Story
- **AND** 工作台进入既有 `/project/<projectId>/stories/<storyId>` 路由语义

#### Scenario: 原生新标签操作保持可用

- **GIVEN** 项目工作台中的提案详情显示一个有效 Story 链接
- **WHEN** 用户使用 Ctrl、Cmd、Shift 或中键激活该链接
- **THEN** 工作台不阻止默认行为
- **AND** 工作台不调用实体 Tab 打开逻辑
- **AND** 浏览器可依据真实 `/story/<storyId>` href 执行原生操作

#### Scenario: 两个详情入口的链接表现一致

- **GIVEN** 根路由提案详情和项目工作台提案详情载入同一提案数据
- **WHEN** 该提案具有有效 Story 引用
- **THEN** 两个入口显示相同的 Story 编号、业务文案和 `/story/<storyId>` href
- **AND** 根路由入口的普通点击按现有 Angular 路由打开 Story 详情
