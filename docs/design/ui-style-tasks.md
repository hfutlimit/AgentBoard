# AgentBoard UI 风格重设计 · 实施清单（Epic 11 扩展 Backlog）

> 本清单与 `ui-style-proposal.md` 配套。每一项都是**一个自动化任务周期的交付物**，
> 遵守 Epic 11 纪律：纯前端、不碰 `models.py`/`api.py`、净增 < ~80 行、可独立验证、改 DOM 补 Playwright。
> 优先级 P0（先做）→ P2；依赖项需在前置完成后认领。

---

## P-01 【P0·基础】设计 Token 体系
- **范围**：`style.css` `:root` + `[data-theme="dark"]`
- **内容**：落地提案 §3 的 `--brand-*`/`--grad`/`--success/warning/danger/info/violet`/`--text-2/3`/`--border-2`/`--surface-2/3`/`--sh-sm/md/lg/brand`/`--r-sm/md/lg`。保留现有 `--primary` 作为 `--brand-500` 别名以兼容旧类。
- **行数**：~40 行
- **依赖**：无
- **验收**：变量定义完整，页面无视觉回退（旧类仍可用）。

## P-02 【P0·基础】字体与排版升级
- **范围**：`style.css` `body`/`h2-h4`/`.stat-number` 等 + `index.html` 字体 `<link>`（自托管或系统栈兜底）
- **内容**：引入 Inter + JetBrains Mono；标题 `letter-spacing:-.02em`；统计数字与 ID 加 `font-variant-numeric:tabular-nums`；统一字号阶梯。
- **行数**：~30 行
- **依赖**：P-01
- **验收**：字号层级清晰，数字等宽对齐。

## P-03 【P0·品牌】Logo Mark 与品牌字
- **范围**：`index.html`（顶栏 logo 容器）+ `style.css`
- **内容**：新增内联 SVG 看板图标（渐变底）+ "Agent<b>Board</b>" 渐变描边文字，替换纯文字；加 favicon（data URI SVG）。
- **行数**：~35 行
- **依赖**：P-01
- **验收**：顶栏出现图形 logo；浅/暗下清晰；favicon 生效。

## P-04 【P1】顶栏磨砂与导航胶囊
- **范围**：`style.css` `.topbar`/`.topbar-nav`/`.global-search`/`.icon-btn`
- **内容**：顶栏 `backdrop-filter: blur` + 半透明；导航 active 改胶囊；搜索框聚焦品牌光环；图标按钮细化。
- **行数**：~25 行
- **依赖**：P-01
- **验收**：滚动时顶栏磨砂；导航高亮为胶囊。

## P-05 【P1】统计卡重设计
- **范围**：`app.js`（`renderDashboard` 统计卡 HTML）+ `style.css` `.stat*`
- **内容**：每张卡加语义色图标芯片（项目/Story/任务/完成率）+ `tabular-nums` 大数字 + 副标题 + 微趋势行；完成率卡用品牌强调。
- **行数**：`app.js`+18 / `style.css`+22（净增 ~40）
- **依赖**：P-01,P-02
- **验收**：仪表盘 5 张统计卡带图标与微趋势，数字等宽。

## P-06 【P1】项目卡强调条与进度
- **范围**：`app.js`（`.project-card` 渲染）+ `style.css`
- **内容**：卡顶 4px 项目色渐变条；hover 上浮+阴影+隐边框；底部环形（conic-gradient）或进度条展示完成度；key 改等宽徽章。
- **行数**：`app.js`+14 / `style.css`+26（净增 ~40）
- **依赖**：P-01
- **验收**：项目卡出现彩色顶条与进度，hover 有层次。

## P-07 【P1】状态徽章加引导点
- **范围**：`app.js`（`statusBadge`）+ `style.css` `.badge.status`
- **内容**：状态药丸前加 8px 色点（双编码语义）；复用现有 `STATUS_COLOR`。任务列表/详情/看板同步。
- **行数**：`app.js`+6 / `style.css`+10（净增 ~16）
- **依赖**：P-01
- **验收**：所有状态徽章含色点，色盲可辨。

## P-08 【P2】优先级箭头图标
- **范围**：`app.js`（`priorityBadge`）+ `style.css` `.prio`
- **内容**：用内联 SVG 箭头（最高↑↑/高↑/中◆/低↓/最低↓↓）替换 `⇈↑◆↓⇊` 符号；配色沿用优先级语义。
- **行数**：`app.js`+12 / `style.css`+8（净增 ~20）
- **依赖**：P-01
- **验收**：优先级显示为箭头+文字，颜色语义正确。

## P-09 【P2】空状态线性插画
- **范围**：`app.js`（`emptyState` 辅助，扩展示例参数）+ `style.css` `.empty svg`
- **内容**：提供 2~3 个内联 SVG 插画（归档盒/看板/空列表），替换 emoji；结构保持"插画+文案+按钮"。
- **行数**：`app.js`+20 / `style.css`+12（净增 ~32）
- **依赖**：P-01
- **验收**：空状态显示线性插画而非 emoji。

## P-10 【P2】头像组件（用户/Agent）
- **范围**：`app.js`（新增 `avatar(name)` 辅助）+ `style.css` `.avatar`
- **内容**：圆形渐变底+首字母；Agent 名加 `🤖` chip 区分；用于评论、活动流、顶栏用户。
- **行数**：`app.js`+14 / `style.css`+10（净增 ~24）
- **依赖**：P-01
- **验收**：评论/活动出现首字母头像；Agent 有标记。

## P-11 【P1】按钮与聚焦态精炼
- **范围**：`style.css` `button.btn`/`.btn-primary`/`.ghost`/`:focus-visible`
- **内容**：主按钮品牌渐变阴影；统一 `:focus-visible` 品牌光环（`outline:2px var(--brand-500);outline-offset:2px`）；圆角 10px。
- **行数**：~22 行
- **依赖**：P-01
- **验收**：按钮聚焦有品牌光环；主按钮有品牌阴影。

## P-12 【P1】深度与表面分级
- **范围**：`style.css` `.card`/`.stat`/`.pcard`/`.panel` 等
- **内容**：用 `--sh-sm/md/lg` 与 `--surface-2/3` 建立层次；卡片 hover 升 `--sh-md`；剥离"全白平铺"。
- **行数**：~28 行
- **依赖**：P-01
- **验收**：页面出现清晰深度层次，非全白平铺。

## P-13 【P0·基础】暗色主题与新 Token 同步
- **范围**：`style.css` `[data-theme="dark"]`
- **内容**：按提案 §3.3 覆盖中性/品牌提亮，确保暗色下品牌渐变仍清晰；核对所有新类在暗色下的可读性。
- **行数**：~30 行
- **依赖**：P-01~P-12（最后做，统一校准）
- **验收**：暗色模式品牌感一致，对比度达标。

## P-14 【P2·可选】仪表盘 Hero 条
- **范围**：`app.js`（`renderDashboard` 顶部）+ `style.css` `.hero`
- **内容**：品牌渐变 hero 显示当前项目名 + 健康度摘要 + "N 个 Agent 在线"胶囊。
- **行数**：`app.js`+12 / `style.css`+18（净增 ~30）
- **依赖**：P-01,P-03
- **验收**：仪表盘顶部出现渐变 hero 条。

## P-15 【P2·可选】Agent 活动面板
- **范围**：`app.js`（仪表盘新增活动列）+ `style.css` `.act`
- **内容**：右侧"近期动态 / Agent 活动"面板，复用 `avatar()`，展示 agent 运行/认领/完成流。
- **行数**：`app.js`+24 / `style.css`+16（净增 ~40）
- **依赖**：P-10
- **验收**：仪表盘出现活动流，agent 有头像与状态 chip。

---

## 遗留 Epic 11 原生 Backlog（一并可派发）
- **A-16** 复制深链（#/task/123）按钮
- **A-17** 路由过渡动画（淡入/滑入）
- **A-18** 面包屑高亮当前级
- **A-19** 列表项 hover 快捷操作（编辑/删除图标）
- **A-20** 前端偏好本地存储（记忆上次视图）

---

## 建议派发顺序（自动化任务队列）
`P-01 → P-02 → P-03 → P-13(末校准)` 为骨架；
`P-04/P-05/P-06/P-11/P-12` 为观感主力（可并行不同文件）；
`P-07/P-08/P-09/P-10` 为组件细化；
`P-14/P-15` 与 `A-16~A-20` 为增强，按需派发。
