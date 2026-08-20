# Epic 152 路由完全收口设计

## 目标

项目工作台以 Angular Router 作为唯一导航状态源，移除 `activeTab` 双轨；项目页内容由真实路由组件渲染，并把 8 个工作台视图从初始 bundle 中移出。

## 当前约束

- `App` 仍负责项目数据加载、弹窗和跨视图动作，立即把全部业务逻辑拆出会扩大回归面。
- 8 个 tab 已经是 standalone 组件，但仍由 `app.html` 的 `@if (activeTab())` 实例化。
- `app.routes.ts` 虽声明 `loadComponent`，根模板没有 `router-outlet`，因此这些路由组件当前不会渲染。
- Settings 仍以内联模板存在，需与其它 7 个 tab 一起迁出初始模板。

## 设计

### 1. Router 成为唯一 tab 状态源

- 侧边栏使用 `RouterLinkActive` 判断当前项，不再接收 `activeTab`。
- `selectProjectTab()` 改为导航到 `/project/:id/:tab`，不再写 signal。
- `loadRoute()` 从 URL 计算一次局部 `projectTab`，仅用于选择加载器，不保存第二份 UI 状态。

### 2. 路由内容边界

- `/project/:id` 重定向到 `overview`。
- 8 个子路径加载项目工作台路由内容组件。
- `app.html` 的 project case 只保留 `<router-outlet>`；原 8 个 `@if` 和 settings 内联模板迁入路由内容边界。
- 现有 `ProjectWorkspaceShellComponent` 继续承担 navy 项目导航，激活态完全由 Router 计算。

### 3. 数据桥接与后续拆分

- 本次先由 `ProjectDataService` 暴露一个显式绑定的 workspace host port，让 lazy route 内容复用现有数据与动作，避免同时重写 API、弹窗和状态机。
- host port 只在项目工作台路由边界使用；它是拆 God Component 的兼容层，不新增业务状态。
- 后续逐 tab 把 selector/action 下沉到 `ProjectDataService` 后，可以移除 host port，而无需再次改变 URL 或模板结构。

### 4. 包体与样式

- `App` 不再直接 import 8 个 tab 组件，路由内容及其组件进入 lazy chunk。
- Settings 模板和项目工作台专用样式迁出 `app.html` / `app.css`。
- 恢复严格 budget：initial warning `1MB`，component-style warning `150kB`；构建必须在严格阈值下无 warning。

## 验收

- `activeTab` 在生产 TypeScript/HTML 中零运行时引用。
- 8 个 `/project/:id/:tab` URL 可直接访问，侧边栏激活态、前进/后退正确。
- project case 由真实 `router-outlet` 渲染，不再有 8 个 `@if`。
- production build initial `< 1MB`，`app.css < 150kB`。
- Vitest、相关 Python 测试和真实浏览器导航回归通过。
