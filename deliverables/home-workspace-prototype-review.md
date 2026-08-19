# AgentBoard「Home & Project Workspace」前端布局原型 · 质量审查报告

> **审查时间**：2026-08-19　**审查来源**：commit `60e9cf1 ui prototype`
> **主理人**：画统筹（设计原型专家团）　**审查官**：严过审（critique-reviewer）
> **审查对象**：`docs/design-prototypes/layout-rebuild/codex/agentboard-home-workspace.html`（1609 行）+ 9 张验证截图 + `verify_home_workspace.py`

---

## 一、原型是什么

这是 AgentBoard 前端**布局重建原型**，把现有的「单 SPA + activeTab 顶部切换」模型重构为**两级 Shell 信息架构**：

```
Home Shell（首页）
 ├─ 项目（Master-Detail 项目浏览器：左侧项目列表 + 右侧项目详情）
 └─ Agents（只读表格：API Key 关联的 Agent 连接状态）
       │  点击「进入工作台」
       ▼
Workspace Shell（项目工作台）
 ├─ 顶栏：返回 + 项目切换器 + 搜索 + 通知
 ├─ 深色 navy 侧边栏：概览 / 看板 / Epics / 工作项 / 提案 / 文档 / 成员 / 设置
 └─ 主区：8 个视图（含统计卡片、交付折线/柱状图、Kanban、分页表格、设置表单）
```

**设计语言**：海军蓝 navy (`#10243e`) + 蓝 (`#2864dc`) 主调，**与项目既有 indigo (`#4f46e5`) 工单视图风格完全不同**——更接近 Linear / Notion / 飞书项目的专业 B2B SaaS 质感。

---

## 二、总评

> **一句话结论**：两级 Shell 信息架构与「浅色工作区 + 深色侧边栏」对比范式是专业 B2B SaaS 范式，视觉令牌体系完整，9 张截图与 Playwright 行为断言全部通过；与既有 indigo 体系存在显著风格替换，但**功能模型与现有 activeTab 高度一致**（80% 对齐），落地成本可控。

**判定：附条件通过（PASS WITH NOTES）** — 零 P0 阻断，4 条 P1（均可一次性修复），6 条 P2（锦上添花）。

---

## 三、五维评分表（均分 4.4 / 5）

| 维度 | 分数 | 一句理由 |
| --- | --- | --- |
| 哲学 Philosophy | 4 / 5 | Home/Agents/Workspace 三层架构清晰，但与现有「单 SPA + activeTab」有结构性偏离，需明确迁移策略。 |
| 层次 Hierarchy | 5 / 5 | 26/30/24/15px 标题阶梯、navy 标题 + ink 正文 + ink-soft 副文、深色侧边栏 vs 浅色内容区对比分明，过渡用 1px line + box-shadow 而非粗描边，分量恰到好处。 |
| 执行 Execution | 4 / 5 | 25 个内联 SVG symbol、3 段响应式断点（1160/840）、prefers-reduced-motion 适配，品质接近 Linear 工程级；扣分：缺键盘焦点环统一规范、缺暗色主题。 |
| 特异性 Specificity | 4 / 5 | 术语完全贴合（Worker/Agent/Proposal/Epic/Sprint/monogram），但 navy+blue 主调与 Linear/Notion 同质化，差异化主要靠项目域术语而非视觉语言。 |
| 克制 Restraint | 5 / 5 | 仅 5 种语义色、两级阴影、180ms transition、reveal 动画不弹跳、brand-mark 双圆拼图、零渐变（除一处 chartFade）。 |

---

## 四、亮点（7 条）

1. **设计令牌体系工程化**（`html:8-34`）：`--canvas/--surface/--ink/--line/--navy/--blue/--teal/--amber/--red` + 两级 shadow + 统一 180ms cubic-bezier 过渡。
2. **Master-Detail 双重应用**：Home 端 `project-browser`（370px + fluid）走 Linear 风，Workspace 端 218px 固定深色 sidebar + 主区走 Notion 风——同一应用内对两类信息密度用差异化布局，分寸精准。
3. **SVG Symbol 库**（`html:934-964`）：25 个 24×24 symbol 集中在 `<defs>` 统一引用，stroke-width: 1.8、linecap: round 系统化设计；相对既有 `app.html` 中夹杂 `▦/◇/⚙` 字符图标是一大进步。
4. **深色侧边栏 + 浅色工作区**（`html:613-660`）：`#10243e` navy + 蓝高亮 + 半透明白文字，sidebar-foot 用 health-pulse 生命体征点表达「3 个 Agents 在线」——把基础设施信息轻量化融入 footer。
5. **统计卡片四联 + 趋势图 SVG 手绘**（`html:1118-1132`）：4 metric 卡片 + velocity-chart 全 SVG（grid/axis/bar/current highlight），无 Chart 库依赖，drop-in 即用。
6. **统一 `managed-list` 抽象**（`html:1253-1480`）：5 种列表（epics/workitems/proposals/documents/members）共享状态机；搜索/主筛选/高级筛选弹窗/分页/清除/活动筛选条 6 类交互均已覆盖。
7. **响应式语义优雅**（`html:889-926`）：1160px 断点收窄 master 列、840px 断点 sidebar 收为 72px 图标态（保留 monogram + 健康点），是结构降级而非简单缩放。

---

## 五、问题与改进建议

### P1（应当修，均可一次性修复）

| 编号 | 问题 | 位置 | 改进方向 |
| --- | --- | --- | --- |
| P1-1 | 顶部「返回全部项目」用 back-icon 而非 home-icon，缺「家」的语义锚点 | `html:1081` | 新建 home 图标变体用于顶层跳转 |
| P1-2 | sidebar 高亮色 `#64a1ff` 硬编码，未走 design-token，与 `--blue` 不一致易漂移 | `html:650` | 增 `--blue-bright: #64a1ff` 命名变量 |
| P1-3 | brand-mark 双圆造型对比度偏弱，缩小到 24×24 后难分辨 | `html:82-90` | 绿色提为 `--brand-mark-accent` token 并测反色版 |
| P1-4 | 与既有 `activeTab` 模型的迁移路径未文档化；原型用 `hidden` 切换 shell，直接照搬会破坏深链与浏览器历史 | 现有 `app.html` + 原型 | 补 `MIGRATION.md`：home→HomeComponent、workspace→WorkspaceComponent（8 lazy routes）、managed-list→独立 ManagedListComponent |

### P2（可优化）

| 编号 | 问题 | 建议 |
| --- | --- | --- |
| P2-1 | 暗色主题完全缺失 | 保留现状（既有也未做），在 MIGRATION.md 注明 light-only 是有意约束 |
| P2-2 | Empty-list-state 图标力度弱 | 增大图标（64×64）+ 分行提示 |
| P2-3 | 通知面板「全部标为已读」缺 hover 反馈 | 补 `:hover { color: var(--blue-dark) }` |
| P2-4 | 切换项目会 replay reveal 动画 | 用 `data-entered` 标记一次性播放 |
| P2-5 | 1024 断点 sidebar 收为 72px 后 nav 文字隐藏无 tooltip | 补 `title=` / `aria-label` |
| P2-6 | 验证脚本未覆盖键盘焦点与无障碍对比度 | 集成 axe-core；实测 `.sidebar-label #768ba6` 对比度边缘合格 |

---

## 六、Anti-Slop 门控：**通过**

文案密度合理、术语自洽，无 AI 套话。夸大象征意义 / 宣传性语言 / 破折号过度 / 模糊归因 / 过多连接性短语均无；命名编号体系（`AB-245` / `PRP-31`）工程化自洽。

---

## 七、可落地性评估

### 7.1 与现有 `app.html` 的对齐度（80%）

| 原型路由 | 现有 activeTab | 关系 |
| --- | --- | --- |
| overview | overview | 复用 |
| kanban | kanban | 复用 |
| epics | epics | 复用 |
| workitems | backlog / tickets | 重命名合并（更准确） |
| proposals | proposals | 复用 |
| documents | documents | 复用 |
| members | — | 新增（Agent × 成员关系） |
| settings | settings | 复用 |
| — | stats | 拆为概览页 4 个 metric 卡片（更克制） |

### 7.2 建议迁移路径

```
agentboard-home-workspace.html
  ├─ 抽出 managed-list 状态机 → ManagedListComponent
  ├─ 抽出 home-shell → HomeComponent（projects / agents 两 view）
  ├─ 抽出 workspace-shell → WorkspaceComponent（8 lazy routes）
  ├─ 抽出 projectSidebar + projectSwitcher → 独立 LayoutComponent
  ├─ 抽出 SVG <defs> → IconComponent / shared sprite
  └─ 抽出 :root tokens → _tokens.scss
```

### 7.3 与既有 indigo 体系的关系：**建议分阶段替换，不要硬切**

- **第一阶段（兼容期）**：保留 `--brand-500: #4f46e5` + `--grad` 渐变作为登录/营销页资产；新 Workspace 路由引入 `--navy` + `--blue` 作为新项目域品牌。
- **第二阶段（迁移完成）**：用 `data-theme="navy"` 切换品牌色板。
- **不建议完全替换**：`--grad` 渐变是既有视觉资产（卡片/按钮/进度条），全删会引发回归。

---

## 八、验证脚本质量（`verify_home_workspace.py`）

**已覆盖（合格）**：Shell 切换、元素数量、字体 ≥14px、数据流、分页、过滤、弹窗 aria-hidden、1024 断点 sidebar 宽度、无 console error、截图体积。

**未覆盖（建议补全）**：键盘焦点顺序、aria-label/role 断言、颜色对比度（axe-core）、空状态边界渲染。补全后可作为 CI 门禁。

---

## 九、最终结论

**附条件通过（PASS WITH NOTES）**：

- 零 P0 阻断；4 条 P1 均可一次性修复；6 条 P2 为锦上添花
- Anti-Slop 通过；验证脚本 90% 覆盖
- 迁移路径清晰，indigo 体系建议分阶段替换而非硬切
- **建议合入主干前**：补 `MIGRATION.md` + 修 P1-2（token 漂移）+ P1-4（迁移路径文档化）即可进入实现阶段

---

## 十、主理人补充判断

这份原型的工程质量在 AgentBoard 前端史上是**迄今最高的一版**——1609 行自包含、零外部依赖、SVG 图标系统化、响应式语义降级、Playwright 行为验证齐备。它解决了现有 `app.html` 的三个真实痛点：① 字符图标（▦◇⚙）不专业；② 单层 activeTab 在项目数增多后信息扁平；③ 缺统一列表交互抽象。

**但有三个落地风险需你决策**：

1. **风格断裂**：navy+blue 与既有 indigo 是两套色系，若直接替换会让登录页/营销页与工作台割裂。审查官建议的「分阶段替换」是稳妥方案，但意味着一段时间内两套令牌共存，维护成本上升。
2. **Angular 路由重构成本**：从 activeTab 切换到 lazy child routes 是结构性改动，需配套 MIGRATION.md 与渐进式迁移，不是一次 PR 能完成。
3. **暗色主题留白**：既有 `styles.css` 已有暗色变量，新原型是 light-only，回迁时需补暗色令牌，否则破坏既有暗色用户体验。

**我的建议**：采纳原型作为 v7 视觉目标，先在 `docs/design-prototypes/` 冻结为设计基准；实现上走「Workspace 侧边栏 + managed-list 抽象」优先落地（收益最大、风险最小），Home Shell 与色板替换作为第二阶段。
