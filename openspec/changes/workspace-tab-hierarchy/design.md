# Design: 项目工作台 Tab 层级化与工作上下文

## 决策 1：Tab 表达「工作上下文」而非页面

**选择**：保留现有多 Tab 机制，只改语义与渲染，不推翻重来。

**理由**：现有机制已经解决了真实问题——Tab 切换走 service 状态而非 router 跳转，避免重拉数据导致的状态丢失；非激活 pane 用 CSS 隐藏，筛选/滚动/已加载数据都能保留。推翻重来会同时丢掉这两点。改造成本集中在「元信息 + 渲染顺序」，风险可控。

## 决策 2：Tab 顺序 = 固定置顶 + 父子聚拢

`displayTabs` 是一个 computed，算法分三步：

1. 按 `parentId` 建子桶；
2. 以「无父级的 Tab」为根，按 `(pinned, 打开顺序)` 排序后深度优先展开，子 Tab 深度 = 父深度 + 1（封顶 2）；
3. 仍未吐出的孤儿 Tab（父 Tab 未打开或父 id 失效）按打开顺序补在末尾，深度固定为 1，并同样递归带出它自己的子 Tab。

**关键点**：根只取「无父级」的 Tab，而不是「父未打开」的 Tab。否则先打开 Story、再打开它的 Epic 时，Story 会作为根先被吐出，永远排不到 Epic 后面——这是最容易写错的一处。

**为什么孤儿也要缩进**：父 Tab 没打开时，如果按顶层渲染，用户会误以为这是个独立的顶层上下文。缩进一级至少保留了「它有父级」这个信息。

## 决策 3：Task 走 Drawer，Story 走 Tab

**选择**：Task 默认 Drawer，Epic / Story / Proposal 保持 Tab。

**理由**：Epic 和 Story 是「工作上下文」——用户会在里面待很久、切来切去；Task 是执行单元，通常是「瞄一眼确认状态/日志/输出」就走，数量也最多。让每个 Task 都占一级 Tab 是 Tab 条溢出的直接原因。

Drawer 不是新开一条数据加载路径：`host.openWorkspaceEntity()` 仍是唯一入口，Drawer 只改「渲染在哪」和「要不要建 Tab」。这样 Drawer 与 Tab 里的内容天然一致，不会出现两处表现分叉。

**互斥约束**：Drawer 与 Task Tab 共用同一份 `host.task()` 信号，因此二者必须互斥——点开 Drawer 不建 Tab，点任一常驻 Tab 即关闭 Drawer。这避免了「Drawer 显示 A、Tab 显示 B，切换后互相污染」。

## 决策 4：持久化按 projectId 隔离

Pin 与最近访问都用 `agentboard_ws_pins_<projectId>` / `agentboard_ws_recent_<projectId>` 两个 key 存储，`setProject()` 时载入对应项目的数据。

**理由**：Tab 本身在切项目时会清空，如果 Pin/最近访问是全局的，会出现「A 项目看到的固定项其实是 B 项目的」。按项目隔离后语义一致。

localStorage 读写全部包在 try/catch 里并做 `typeof localStorage === 'undefined'` 守卫——服务在单测里被直接 `new` 出来，不能依赖 DI 注入的 storage 抽象，也不能因隐私模式/配额满而抛异常。

## 决策 5：不改路由模型

`/project/:id/:section/:entityId` 的直链能力是 Agent 协作的关键入口（MCP / 通知 / 文档里都会引用具体 Task），改成 `/project/:id/workspace?state=json` 会一次性废掉所有已有链接。

本阶段保留全部现有路由，只让 Task 的直链在落地时按当前偏好进 Drawer 而不是 Tab——URL 不变，渲染位置变。路由模型改造留到第二阶段，届时可以做新旧路由双轨 + 重定向。

## 风险

- `host.task()` / `host.story()` / `host.epic()` 是单例信号，父级绑定的 effect 依赖「当前加载的实体」这一前提。同一时刻多实体并存时，只有当前加载的那一个能拿到正确父级。当前交互模型下不会同时加载两个实体，可接受。
- Drawer 与 Tab 内容共用单例信号，见决策 3 的互斥约束。
