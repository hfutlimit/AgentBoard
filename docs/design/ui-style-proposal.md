# AgentBoard UI 风格重设计提案（v1）

> 目标：在 **不破坏后端契约、不引入新框架/打包依赖、单交付 < ~80 行（Epic 11 纪律）** 的前提下，
> 把当前"功能完整但偏朴素"的 SPA 提升为「有专业项目管理气质的工具」——对标 Linear / Height / Shortcut 的视觉语言，
> 而非默认的"白底 + 通用蓝 + emoji 标题"观感。
> 配套原型见 `docs/design/mockup.html`（可浏览器直接打开，含明/暗切换）。

---

## 1. 现状诊断（为什么显得"朴素"）

| 维度 | 当前实现 | 观感问题 |
|------|----------|----------|
| 字体 | `-apple-system, "Segoe UI", "Noto Sans SC"` | 无品牌感，中文西文混排字号跳变 |
| 主色 | `#2563eb`（Tailwind 默认蓝） | 过于"通用 SaaS"，无辨识度 |
| 表面 | 全白卡片 + 1px 灰边 + 极淡阴影 | 层级扁平，缺乏深度与焦点 |
| 标题 | `📂 项目总览` `⚡ 快捷操作` 用 emoji | 业余感，专业工具避免 emoji 当标题 |
| 统计卡 | 纯文字 + 单张高亮顶边 | 信息密度低、无视觉锚点 |
| 状态徽章 | 彩色药丸，无引导点 | 尚可，但缺"状态点"这种一眼可扫的语义 |
| 空状态 | `📋` + 文案 | emoji 插画，缺专属线性插画 |
| 品牌 | 纯文字 logo，无图形 mark / favicon | 无记忆点 |
| 深色模式 | 仅变量覆盖，未与品牌色统一 | 暗色下品牌感丢失 |

**结论**：骨架与交互（A-01~A-15）已经专业，差的是**视觉语言系统**——颜色、字体、深度、图形品牌、信息密度。

---

## 2. 设计原则

1. **品牌优先**：一个稳定的渐变 `--grad`（indigo→violet）作为唯一"签名色"，用于 logo、主按钮、强调条、Agent 标记。
2. **克制的层次**：用阴影尺度（sm/md/lg）+ 表面分级（surface / surface-2 / surface-3）替代"全白"。
3. **语义化状态**：状态/优先级全部用"色点 + 文字"双编码，色盲可读。
4. **密度与节奏**：统一 8px 基准间距，标题字距收紧（`letter-spacing:-.02em`），数字用 `tabular-nums`。
5. **图形而非 emoji**：logo mark、空状态插画、状态用内联 SVG，保持 crisp 与一致性。
6. **明暗同源**：同一套 token 驱动两套主题，品牌渐变在暗色下提亮而非替换。

---

## 3. 设计 Token 系统（新）

### 3.1 颜色
```css
:root{
  /* Brand —— 唯一签名渐变 */
  --brand-500:#4f46e5; --brand-600:#4338ca; --brand-700:#3730a3;
  --brand-soft:#eef2ff; --brand-ring:rgba(79,70,229,.18);
  --grad:linear-gradient(135deg,#6366f1 0%,#8b5cf6 55%,#a855f7 100%);

  /* 语义色（与状态机对齐，保证对比度 ≥ 4.5:1） */
  --success:#059669; --warning:#d97706; --danger:#dc2626;
  --info:#0891b2;    --violet:#7c3aed;

  /* 中性（slate 体系，替代原 gray） */
  --text:#0f172a; --text-2:#475569; --text-3:#94a3b8;
  --border:#e2e8f0; --border-2:#eef2f6;
  --bg:#f6f8fb; --surface:#ffffff; --surface-2:#f8fafc; --surface-3:#f1f5f9;

  /* 深度 */
  --sh-sm:0 1px 2px rgba(15,23,42,.05),0 1px 3px rgba(15,23,42,.06);
  --sh-md:0 4px 12px -2px rgba(15,23,42,.08),0 2px 6px -2px rgba(15,23,42,.05);
  --sh-lg:0 12px 28px -6px rgba(15,23,42,.14);
  --sh-brand:0 8px 24px -6px rgba(99,102,241,.45);

  --r-sm:8px; --r-md:12px; --r-lg:16px;
}
```
> 状态色映射：`backlog→text-3(slate)` `todo→#2563eb` `in_progress→warning` `in_review→violet` `verifying→info` `done→success`，与原 `STATUS_COLOR` 一致，仅把蓝从 `#3b82f6` 提到 `#2563eb` 增强对比。

### 3.2 字体（渐进增强，不引入构建依赖）
- 主字体：`'Inter', -apple-system, "Segoe UI", "Noto Sans SC", sans-serif`
  - 生产建议：**自托管 Inter woff2**（放入 `static/`），或用系统栈兜底；原型用 Google Fonts `<link>` 仅作演示。
- 等宽（项目 key / ID / 代码）：`'JetBrains Mono', ui-monospace, Consolas, monospace`
- 字号阶梯：12 / 14 / 16 / 18 / 20 / 24 / 30 / 36（与现有 8px 间距对齐）
- 数字：`font-variant-numeric: tabular-nums`

### 3.3 暗色主题
沿用上述 token，仅覆盖中性与品牌提亮：
```css
[data-theme="dark"]{
  --brand-500:#818cf8; --brand-soft:#1e1b4b; --brand-ring:rgba(129,140,248,.25);
  --text:#e8edf5; --text-2:#aab4c5; --border:#273344;
  --bg:#0b1120; --surface:#121a2b; --surface-2:#0f1626; --surface-3:#1a2336;
  --sh-md:0 6px 16px -4px rgba(0,0,0,.5);
}
```

---

## 4. 组件重设计（Before → After）

### 4.1 顶栏 Topbar
- **Before**：白底 + 阴影，文字 logo，emoji 搜索图标。
- **After**：半透明磨砂（`backdrop-filter: blur`）+ 1px 底边；**渐变 logo mark（内联 SVG 看板图标）+ 渐变描边文字 "AgentBoard"**；导航用胶囊 active；搜索框圆角 + 聚焦品牌光环。

### 4.2 统计卡 Stat Cards
- **After**：每张卡带**语义色图标芯片**（项目/Story/任务/完成率各一色）+ 大号 `tabular-nums` 数字 + 副标题 + 一行微趋势（如 `本周 +18`）。完成率卡用品牌渐变强调。
- 取代原"仅文字 + 单张高亮顶边"。

### 4.3 项目卡 Project Card
- **After**：顶部 4px **按项目 hue 的渐变强调条**；hover 上浮 + 阴影 + 边框隐去；底部**进度条**或**环形进度**（conic-gradient）展示完成度；key 用等宽徽章。
- 取代原纯白卡片 + 单调 stats 行。

### 4.4 状态 / 优先级徽章
- **After**：状态 = **引导色点 + 文字**（`.b-status-*`），一眼可扫；优先级用**箭头 SVG + 颜色**（最高红→最低灰），不再用 `⇈` 这类符号。
- 复用现有 `STATUS_COLOR` / `STATUS_LABEL`，仅改渲染结构。

### 4.5 空状态 Empty State
- **After**：**专属线性 SVG 插画**（如归档盒、看板框），替代 emoji；保持"图标 + 引导文案 + 主按钮"结构，复用现有 `emptyState()` 辅助。

### 4.6 头像 Avatar
- **After**：圆形**渐变底 + 首字母**（`JA`/`CB`/`WB`），用于用户与 Agent，统一"谁在操作"的视觉。Agent 加 `🤖` chip 区分。

### 4.7 按钮 / 表单
- **After**：主按钮带品牌渐变阴影 + 聚焦光环；ghost 按钮描边；统一圆角 `10px`、最小高度 `36px`（触摸友好，沿用 A-11）。

### 4.8 仪表盘 Hero（新增，可选）
- **After**：顶部**品牌渐变 hero 条**，显示当前项目名 + 一句健康度摘要 + "N 个 Agent 在线"胶囊。提升"项目管理工作台"的归属感。

---

## 5. 实施策略（贴合 Epic 11 纪律）

- 全部为 **纯前端**（改 `style.css` / `app.js` / `index.html`），**不碰 `models.py` / `api.py`**。
- 每项独立、可验证、净增 **< ~80 行**；超限拆分。
- 改动 DOM/通用函数时补 Playwright 用例（FR-10 已规划）。
- 设计 token 集中在 `:root`，组件仅引用变量，保证一致性。

---

## 6. 实施清单（交给"自动化任务"逐条实现）

> 见同目录 `ui-style-tasks.md`，已按 Epic 11 格式拆为 P-01 ~ P-15，标注优先级与依赖。
> 每项即一个自动化任务周期的交付物。

---

## 7. 验收

- [ ] 视觉不再"朴素"：出现稳定品牌渐变、图形 logo、深度层次、语义状态色
- [ ] 字体层级清晰，数字对齐（tabular-nums）
- [ ] 明暗两套主题品牌感一致
- [ ] 所有新增样式复用 `:root` token，无硬编码散落
- [ ] 现有 A-01~A-15 交互不被破坏，Playwright 回归通过
