# Design: 提案已生成 Story 链接交互

## 1. 目标与范围

本设计让已成功生成 Story 的提案详情提供可访问、可复制且可按浏览器原生方式打开的 Story 链接。它覆盖两个现有渲染入口：

1. 项目工作台的 `ProposalDetailViewComponent`（`proposal-detail-view`）。
2. 根模板 `App` 的 `@case ('proposal')` 详情视图。

两处只改变“已生成 Story”的呈现和跳转；不改变任何提案转换、工单请求或后端数据写入行为。

## 2. 已核实的基线

- `ProposalItem` 已有兼容字段 `story_id` 及通用字段 `ticket_type`、`ticket_id`。
- 两个入口当前都在终态工单区域把 `ticket_type` 和 `ticket_id` 呈现为不可点击的 `id=<数字>` 文本。
- 工作台现有实体链接约定是保留真实 href；普通左键调用 `openWorkspaceEntity`，而 Ctrl/Cmd/Shift/中键不调用 `preventDefault`。`TabPaneComponent` 和详情组件已经使用这项约定，最终会进入 `/project/<projectId>/stories/<storyId>` 并复用实体 Tab。
- 非工作台根模板已使用 Angular `routerLink` 打开 `/story/<id>`，可作为该入口的正常左键路径。

## 3. Story 引用解析契约

实现一个唯一、可单测的前端解析函数（名称可为 `proposalStoryId`），两个模板均只消费它的返回值：

```ts
proposalStoryId(proposal: ProposalItem): number | null
```

解析按以下顺序执行：

1. 如果 `proposal.story_id` 是正整数，返回它；不检查 `ticket_type` 或 `ticket_id`。
2. 仅当 `proposal.story_id` 缺失（`null` 或 `undefined`）时，若 `proposal.ticket_type === 'story'` 且 `proposal.ticket_id` 是正整数，返回 `ticket_id`。
3. 其他所有情况返回 `null`，不显示 Story 链接。

此处“有效”固定为正整数，避免生成 `/story/0`、`/story/NaN` 或以非数值跳转。若 `story_id` 非空但格式损坏，不能把它悄悄替换为另一个 ticket 引用：这不是“缺失”，应保持无链接以暴露数据问题并防止误跳转。`ticket_type` 使用大小写敏感的精确比较；`epic`、`task`、`bug`、空字符串和未知类型一律不能得到 Story 链接。

该函数不读取 `TicketRequestItem`，也不以 `status`、标题或父级 ID 推断实体。其输入仅是当前提案 DTO，保持兼容字段的权威顺序明确且稳定。

## 4. 统一渲染契约

在现有终态工单区域内，渲染逻辑按下列优先级组织：

1. `proposalStoryId(p)` 有值时，显示业务文案“查看 Story #<id>”，并产生 `href="/story/<id>"` 的 `<a>`。
2. 否则，保留现有通用工单文本（仅当 `p.ticket_type && p.ticket_id` 时）。
3. `ticket_preparing`、`converged` 的既有表单与提示保持原样；失败、取消及无有效引用不新增链接。

因此，旧的 `story_created + story_id` 记录无需补写 `ticket_type`/`ticket_id` 也能获得链接；新 `ticket_created + ticket_type='story' + ticket_id` 记录同样可用。非 Story ticket 继续显示原有类型和编号，绝不伪装为 Story。

两个模板都使用同一解析方法，链接使用相同的可见文本和精确 href。工单请求历史中的 `TicketRequestItem.ticket_id` 不是本设计指定的解析来源，保持现有展示，以免请求记录与已回填 Proposal 的最终实体不一致时误链接。

## 5. 点击与路由行为

### 工作台详情

`ProposalDetailViewComponent` 增加与其他工作台详情一致的 Story 链接点击处理器。它接收 `MouseEvent` 和已解析的 `storyId`：

- 普通主键点击（`button === 0`，且无 Ctrl、Cmd、Shift）调用 `preventDefault()`、`stopPropagation()`，再调用宿主的 `openWorkspaceEntity('story', storyId)`。
- Ctrl、Cmd、Shift 或中键直接返回，不拦截浏览器默认动作。

模板链接依旧以 `href="/story/<id>"` 输出；处理器只是普通点击的增强层。这样可支持键盘焦点、复制链接地址、上下文菜单与原生新标签，并沿用已有实体 Tab 去重逻辑。

### 根模板详情

根模板只渲染同样的 `/story/<id>` `routerLink`/href，不附加工作台拦截器。普通点击因此走现有 Angular 路由至 Story 详情；带修饰键或中键由真实 href 交给浏览器。不得为这一入口另写 `window.open`、手工 URL 拼接或与工作台不同的 ID 优先级。

## 6. 影响边界与风险

| 风险 | 控制措施 |
| --- | --- |
| 两个入口后续重新分叉 | 将解析收敛为 `App` 宿主的单一方法，工作台适配器只转发它；测试两个入口的链接绑定。 |
| 兼容字段与通用字段冲突 | 先用有效 `story_id`；仅缺失才回退，且不做数据写回。 |
| 非 Story ticket 被错误链接 | `ticket_type === 'story'` 的精确门槛；明确的 epic/task/bug/未知类型负例。 |
| 破坏新标签或辅助功能 | 始终渲染真实 href；仅普通主键调用 `preventDefault`。 |
| 无效历史数据产生坏路由 | 正整数验证；无效值回到现有无链接呈现。 |

无需后端迁移、权限改变、缓存失效策略或回滚脚本。若需要回滚，只撤回前端模板和解析/点击处理变更，即恢复原来的文本呈现。

## 7. 测试与验收

自动化测试应至少覆盖：

| 场景 | 断言 |
| --- | --- |
| `story_id=123` 且 ticket 字段冲突 | 链接文案为“查看 Story #123”，href 为 `/story/123`。 |
| `story_id=null`、`ticket_type='story'`、`ticket_id=456` | 使用兼容回退，href 为 `/story/456`。 |
| `story_id=null` 且 ticket 类型为 epic/task/bug/未知 | 不生成 Story 链接，保留既有通用工单展示。 |
| 非空无效 `story_id` | 不回退至 ticket，不生成 Story 链接。 |
| 工作台普通左键 | 调用一次 `openWorkspaceEntity('story', id)`，阻止默认行为；工作台路由/Tab 结果为既有 `/project/<projectId>/stories/<id>` 语义。 |
| Ctrl/Cmd/Shift/中键 | 不调用工作台打开方法，也不阻止默认行为；DOM 仍有 `/story/<id>` href。 |
| 两个详情入口 | 对同一数据使用同一解析结果、文案和 href。 |

实现完成后在 `src/frontend` 运行聚焦 Vitest 测试和 `npm run build`。QA 需在浏览器或等效端到端环境确认：工作台普通点击激活/复用 Story Tab；Ctrl/Cmd/Shift/中键按真实 href 原生打开；根路由详情同样显示可访问链接。设计阶段不运行这些实现后验证命令，因为功能尚未实现。
