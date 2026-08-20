# Epic 149 踩坑沉淀（2026-08-20）

本次修复 Bug #1290（成员与 Agents 视图空白）过程中的踩坑与解决方案。
供未来 AgentBoard 前后端开发者、E2E 维护者参考。

---

## 1. ng serve E2E proxy 必须指向生产 API

**规则** → 跑 `tests/e2e_epic149/*.py` 自动化 E2E 时，`ng serve` 必须带 `--proxy-config tests/e2e_epic149/proxy.conf.json`（指向 `http://124.220.44.12` 生产），**不要用** `frontend/proxy.conf.json`（指向 `127.0.0.1:58125` 本地后端，但本地后端不一定在跑）。

**证据/原因**：
- 本地后端 58125 不一定在跑（CI / 临时调试时常空）
- production API 124.220.44.12 是 E2E 测试的稳定基线
- ng serve 不带 proxy 时浏览器 fetch `/api/auth/login` 失败，整个 SPA 卡在 marketing/login 页

**适用场景**：
```powershell
# ✅ 正确（E2E）
cd frontend
Start-Process npx.cmd ng serve --host 127.0.0.1 --port 4200 --proxy-config ..\tests\e2e_epic149\proxy.conf.json

# ❌ 错误（E2E 时）
npx ng serve   # 不带 proxy
# 或
npx ng serve --proxy-config proxy.conf.json  # 指向 58125 本地后端
```

**附加**：
- `frontend/proxy.conf.json` → 开发联调（指向 58125）
- `tests/e2e_epic149/proxy.conf.json` → E2E（指向 124.220.44.12 生产）
- 两者 API 路径一致，但 host 不同

---

## 2. Playwright add_init_script 写 localStorage 在 about:blank 失败

**规则** → Playwright `page.add_init_script` 在 page.goto 之前调用，但 `localStorage.setItem` 在 about:blank 上下文中**静默失败**（无 origin，token 不会写入）。

**证据/原因**：
- page 创建时是 about:blank，init script 注入 `localStorage.setItem('agentboard_token', ...)` 不报错但**不生效**
- 首次 navigation 后 `lsToken='missing'`，路由守卫把 /project/3 跳到 /login
- Playwright 1.62.0 + Angular 21 SPA 都观察到此问题

**适用场景**：需要预先注入 localStorage（如 auth token）来通过 SPA 路由守卫的 E2E 场景。

**解法 A（推荐）— 先 origin 后 token**：
```python
# 1. 先到同 origin 但不带 path（避免路由跳）
page.goto("http://127.0.0.1:4200/", wait_until="domcontentloaded")
page.wait_for_timeout(800)

# 2. evaluate 写 localStorage
page.evaluate(f"localStorage.setItem('agentboard_token', '{token}'); "
              f"localStorage.setItem('agentboard_user', 'admin');")

# 3. 再 goto 目标路径
page.goto("http://127.0.0.1:4200/project/3", ...)
```

**解法 B — context-level init script**：
```python
ctx = browser.new_context()
ctx.add_init_script(f"localStorage.setItem('agentboard_token', '{token}'); ...")
page = ctx.new_page()
page.goto("http://127.0.0.1:4200/project/3", ...)  # 首次 navigation 就有 origin
```

**解法 C（最稳）— form login**：
```python
page.goto("http://127.0.0.1:4200/", ...)
page.fill('input[placeholder*="用户"]', "admin")
page.fill('input[type="password"]', "admin123")
page.click('button:has-text("登录")')
```

---

## 3. Angular AppComponent 路由守卫依赖 localStorage

**规则** → `frontend/src/app/app.ts:150`
```ts
readonly authVisible = signal(!localStorage.getItem('agentboard_token'));
```
- 在 component class 构造时读 localStorage
- 无 token → `authVisible() === true` → 显示 login 页 → URL 跳 `/login`
- 有 token → `authVisible() === false` → 渲染主应用

**证据/原因**：AppComponent 在每次 navigation 时 class field 重新初始化。任何 navigation 之前必须有 token，否则被守卫重定向。

**适用场景**：调试 E2E token 注入、自动化代理接入、登录态丢失排查。

---

## 4. ManagedListComponent 内容投影契约（阶段2 抽取）

**规则** → 阶段2（Epic 149 Story 318）`ManagedListComponent` 用**内容投影**（非完全数据驱动）做统一列表外壳。

**投影插槽**：
| 插槽 | 用途 | 渲染时机 |
| --- | --- | --- |
| `[ml-header]` | section-header（标题 + 计数 + 操作按钮） | 始终 |
| `[ml-toolbar]` | 搜索 / 筛选 / 主筛选 | 始终（含 loading） |
| 默认插槽 | 条目主体 + 自有空状态 | 仅非 loading / 非 error |

**状态优先级**：`loading > error > 主体`。

**为什么是内容投影而非数据驱动**：
- 各列表的筛选信号、条目模板差异大（epics 进度条 / proposals 轮次 / documents 拖拽文件夹 / members 表格）
- 强行数据驱动会引入回归风险
- 外壳统一三态 + 分页，条目与工具栏由各列表自行投影
- 后续阶段可在此外壳上叠加「列定义 / 筛选项」数据驱动能力而不破坏现有投影契约

**适用场景**：写新列表组件时直接套壳：
```ts
import { ManagedListComponent } from '../managed-list/managed-list';

@Component({
  imports: [ManagedListComponent, ...],
  // ...
})
export class MyListComponent {
  @Input() loading = false;
  @Input() error: string | null = null;
  // ...
}
```

```html
<app-managed-list
  [loading]="loading"
  [error]="error"
  errorPrefix="列表加载失败"
  loadingLabel="正在加载…"
  [skeletonRows]="5"
  [pageSize]="20"
  [total]="total()"
  [page]="page()"
  (pageChange)="pageChange.emit($event)"
  (retry)="retry.emit()">

  <ng-container ml-header>...</ng-container>
  <div ml-toolbar>...</div>
  <!-- 默认插槽：条目 + 空状态 -->
  <div class="entity-list">...</div>
</app-managed-list>
```

**命名空间约定**：CSS 用 `.<tab-name>-v7` 命名空间隔离本组件样式（如 `.proposals-tab-v7`、`.members-tab-v7`），避免污染其它列表。

---

## 5. Epic 149 重构遗漏 members tab（提交 099eff0 自报 8/8 误判）

**规则** → Epic 149 阶段 2/3 重构时漏迁 members 视图。提交 `099eff0` 自称「Story 319 — stats tab extracted to StatsTabComponent (8/8, final)」中的 **8/8 实际是 stats 而非 members**。

**根因**：
- `app.html` 主内容区有 9 个 `@if (activeTab() === '...')` 渲染块：`overview(778)/epics(793)/backlog(809)/stats(822)/settings(843)/proposals(1082)/documents(1096)/kanban(1140)/tickets(1155)`
- 唯独**缺** `'members'` 块
- `frontend/src/app/` 下 8 个独立组件：overview-tab/kanban-tab/epics-tab/backlog-tab/proposals-tab/documents-tab/stats-tab/tickets-tab
- 唯独**没有** `members-tab/` 目录

**为什么自报数没发现**：
- E2E 脚本 `NAV_ITEMS` 第 3 列为视图 selector，members 项写的是 `None`（弱校验）
- 弱校验逻辑：`sel is None` → 只看 `mainTextLen > 50`（任何渲染都 PASS）
- 即使主内容区空白，marketing 页文本也满足，**误判 PASS**

**适用场景**：下次大重构时，**别用「N/N final」自报数**。

**修复（commit 695d7d9）**：
1. 新增 `frontend/src/app/members-tab/{members-tab.ts,html,css}`（3 文件）
2. `app.html` 加入 `@if (activeTab() === 'members')` 块
3. `app.ts` 导入 + 注册 `MembersTabComponent`
4. E2E 脚本 `NAV_ITEMS.members` sel 补 `'app-members-tab'`（精确校验）
5. 重跑 E2E：Story 318/319 PASS（0 issues / 0 console errors / 0 page errors）

**截图**：
- `tests/e2e_epic149/screenshots/318/list_members.png`（5 成员 + role badge + 加入时间）
- `tests/e2e_epic149/screenshots/319/view_members.png`（同上，独立组件挂载验证）

**阶段2/3 完整闭环**：
- 阶段2：ManagedListComponent 覆盖 5/5 列表（epics/workitems/proposals/documents/members）
- 阶段3：八视图独立组件补全为 9/9（多了 members）

---

## 6. E2E 脚本 NAV_ITEMS.sel 为 None = 弱校验（必须修）

**规则** → `tests/e2e_epic149/test_story318_319_e2e.py` 的 `NAV_ITEMS` 第 3 列是视图 selector。若为 `None`，只判 `mainTextLen > 50` —— 任何渲染（含 marketing 残留）都 PASS。

**证据/原因**：
- 原 `("成员与 Agents", "members", None)` 弱校验漏掉了主内容区空白的 Bug #1290
- 视图挂载失败 vs 视图正确渲染 = 两种状态，弱校验无法区分

**适用场景**：所有 E2E 脚本的视图验证项。

**修复**：
- 每个视图必须给实际 selector（`app-overview-tab` / `app-kanban-tab` / `app-members-tab` / ...）
- 验证逻辑变为：`mainTextLen > 50 AND viewSelectorPresent === true`
- 这样能精确检测「组件挂载 = 视图正确」而不是「有字 = OK」

```python
# ✅ 强校验
NAV_ITEMS = [
    ("概览", "overview", "app-overview-tab"),
    ("看板", "kanban", "app-kanban-tab"),
    ("Epics", "epics", "app-epics-tab"),
    ("工作项", "backlog", "app-backlog-tab"),
    ("提案", "proposals", "app-proposals-tab"),
    ("文档", "documents", "app-documents-tab"),
    ("成员与 Agents", "members", "app-members-tab"),
    ("设置", "settings", None),  # 复杂视图可保留 None
]
```

---

## 关联

- Epic 149（前端布局重建：基于 Home & Workspace 原型 v7 重构）
- Story 318（阶段2 ManagedListComponent 抽取 + projectSidebar 重构）
- Story 319（阶段3 八视图独立组件迁移）
- Bug #1290（成员与 Agents 视图空白）
- 修复 commit：695d7d9
- 评审：Task 1284 / 1286 → done（approve）
- Bug：Task 1290 → done（completed）
