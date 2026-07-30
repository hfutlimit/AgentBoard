# DESIGN.md — 前端问答工作台（Q&A Workbench）设计令牌

> 交付对象：原型构建师（Angular 单页原型）
> 设计系统基因：**Linear**（暗色优先 / 工程化 / 克制 / 信息密度适中）
> 配套依据：`ui-style-proposal.md`（v1 已确立 Linear 基准）+ `frontend/src/styles.css`（现有 token 命名）
> 说明：本令牌在「需求假定暗色变量」之上补全语义色与组件态，并尽量复用现有 CSS 变量名，便于直接落入 `styles.css`。

---

## 0. 设计系统推荐（候选对比）

| 方案 | 设计系统 | 匹配度 | 特征 | 适合原因 / 差异 |
|------|---------|--------|------|----------------|
| **A（推荐）** | **Linear** | ★★★★★ | 暗色优先、indigo/iris 主色、Inter、8px 圆角、1px 细边框、状态点、克制而信息密度适中 | **最契合**。需求假定（indigo #6366F1 + #0F1115 暗底 + 8px + 状态点）与 Linear 视觉语言几乎同构；且 AgentBoard 现有 `ui-style-proposal` 已把 Linear 定为基准。可直接继承全部组件语义。 |
| B | Vercel | ★★★★☆ | 纯黑/白单色、Geist 字体、极简、高对比、几何感 | 更「极简到单色」，可把 indigo 仅作极小点缀。差异：失去 indigo 品牌识别与多彩状态（五态流程会偏灰）；更轻、更「官网感」，不太像内部工具。 |
| C | Supabase | ★★★☆☆ | 暗色优先、Inter、但主色为翠绿 #3ECF8E、开发者/基础设施气质 | 同样暗色工程化，但**主色是绿不是 indigo**，与需求假定冲突需换色；更偏「数据库/DevOps 控制台」。作为「若未来想换品牌色」的备选。 |

**结论**：选 **A · Linear**。它是需求假定与现有 AgentBoard 系统的最大公约数，主色 indigo 与暗色变量无需大改即可落地。

---

## 1. Visual Theme（视觉主题）

**Philosophy**：少即是多——用克制的暗色界面、清晰的层级与语义状态点，让 PM/研发逐条审阅 AI 澄清问题时只关注内容本身。
**Direction**：modern-minimal + tech-utility 混合；dark-first；moderate density
**Personality**：precise, calm, professional, engineered
**Reference**：Linear 暗色工作台；AgentBoard `ui-style-proposal.md` v1
**Anchor**：单页、桌面优先（基准 1440px）、单屏可滚动、非响应式必需；左栏正文 + 右栏多轮问题卡片 + 顶栏状态流程 + 收敛定稿面板。

---

## 2. Color Palette（调色板）

> 色彩空间：以 **HEX 为权威值**；OKLCh 为 AI 生成时的感知近似（已标注，可直接消费）。
> 暗色主题为主；下表「暗色变量集」即需求假定基调，补全语义色与组件态。

### 2.1 背景层级 / 中性（暗色变量集 · 以需求假定为底）

| Token | HEX | OKLCh(近似) | 用途 |
|-------|-----|-------------|------|
| `--bg` | `#0F1115` | oklch(15% 0.006 250) | 页面底色（顶栏/左栏/右栏统一底） |
| `--surface` | `#1A1D24` | oklch(20% 0.008 255) | 卡片、问题卡、元信息卡背景 |
| `--surface-2` | `#161922` | oklch(18% 0.007 253) | 次级表面（顶栏磨砂底、输入框底、hover 态） |
| `--surface-3` | `#22262F` | oklch(24% 0.010 258) | 三级表面（胶囊底、分隔带、折叠态） |
| `--border` | `#2A2F3A` | oklch(27% 0.012 260) | 1px 细边框、分隔线 |
| `--border-strong` | `#3A4150` | oklch(33% 0.014 262) | hover/聚焦时边框提亮 |
| `--text` | `#E6E8EB` | oklch(91% 0.005 250) | 主文本、标题 |
| `--text-2` | `#9AA4B2` | oklch(68% 0.015 255) | 次文本、元信息、占位符 |
| `--text-3` | `#7E8898` | oklch(57% 0.022 258) | 弱文本、禁用、装饰（**仅装饰性/非必要文本**） |

> **对比度约束（P0 根因修正）**：`--text-3` 原值 `#6B7280` 在 `--surface #1A1D24` 上约 3.49:1、在 `--bg #0F1115` 上约 3.91:1，均低于 WCAG AA 4.5:1。已提亮至 `#7E8898`（在 `--surface` 上 ≈4.71:1、在 `--bg` 上 ≈4.93:1，裕量足够；若需更稳可用 `#8A93A3`≈5.9:1，但会压缩与 `--text-2` 的层级差）。**`--text-3` 仅用于装饰性、非必要文本（如禁用态、分隔说明）；任何需要被阅读的小字标签——meta-key、proposal-eyebrow、step-label 未来态、q-rationale「为什么问」、round-count、converged-note、brand-sub、bell-item time 等 11–12px 文本——必须使用 `--text-2`，不可用 `--text-3`。**

### 2.2 主色（Primary / Brand）

| Token | HEX | OKLCh(近似) | 用途 |
|-------|-----|-------------|------|
| `--primary` | `#6366F1` | oklch(58% 0.22 264) | 主按钮、聚焦环、激活态、状态流程当前步（**需求假定值，保留**） |
| `--primary-hover` | `#5457E5` | oklch(53% 0.21 264) | hover |
| `--primary-active` | `#4A4CE0` | oklch(49% 0.20 264) | active/pressed |
| `--primary-soft` | `rgba(99,102,241,0.12)` | — | 主色淡底（胶囊、选中行、focus 底） |
| `--primary-ring` | `rgba(99,102,241,0.28)` | — | 聚焦光环 |

> **主色决策说明**：需求假定 indigo `#6366F1`，与选定系统 **Linear 同属 indigo 家族**（Linear 规范色为 `#5E6AD2`，AgentBoard 现有 `--color-primary` 为 `#5B5BD6`、`--brand-500` 为 `#4f46e5`）。三者同调，无冲突；`@6366F1` 同时是现有品牌渐变 `--grad` 的首位停靠色，故**保留为权威主色**。若追求更严格的 Linear  fidelity，可整体替换为 `#5E6AD2`（见 §9 Quick Snippet 注释）。
> 品牌渐变（与现有系统一致）：`--grad: linear-gradient(135deg, #6366F1 0%, #8B5CF6 55%, #A855F7 100%);`

### 2.3 语义状态色（通用）

| Token | HEX | OKLCh(近似) | 用途 |
|-------|-----|-------------|------|
| `--success` | `#22C55E` | oklch(72% 0.22 149) | 成功、已提交 |
| `--success-text` | `#4ADE80` | oklch(80% 0.19 149) | 暗底上的成功文字 |
| `--warning` | `#F59E0B` | oklch(77% 0.19 70) | 警告（amber 胶囊） |
| `--warning-text` | `#FBBF24` | oklch(82% 0.17 72) | 暗底上的警告文字 |
| `--danger` | `#EF4444` | oklch(62% 0.22 25) | 危险/删除 |
| `--danger-text` | `#F87171` | oklch(72% 0.19 25) | 暗底上的危险文字 |
| `--info` | `#3B82F6` | oklch(62% 0.19 259) | 信息/链接 |
| `--info-text` | `#93B4FB` | oklch(74% 0.16 259) | 暗底上的信息文字 |
| `--violet` | `#8B5CF6` | oklch(64% 0.21 290) | 强调/次级品牌 |

### 2.4 五态流程色（draft→queued→analyzing→awaiting→converged）

> 顶栏状态流程胶囊专用。每态提供「点色 / 暗底淡底 / 暗底文字」三件套，保证暗色对比度 ≥ 4.5:1。

| 状态 | Token（点色） | HEX | Token（淡底 soft） | Token（文字 text） | 含义 |
|------|--------------|-----|-------------------|-------------------|------|
| draft（灰） | `--flow-draft` | `#9AA4B2` | `rgba(154,164,178,0.12)` | `#9AA4B2` | 草稿，未进入队列 |
| queued（蓝） | `--flow-queued` | `#3B82F6` | `rgba(59,130,246,0.14)` | `#93B4FB` | 已排队待分析 |
| analyzing（青） | `--flow-analyzing` | `#22D3EE` | `rgba(34,211,238,0.14)` | `#67E8F9` | AI 分析中 |
| awaiting（琥珀） | `--flow-awaiting` | `#F59E0B` | `rgba(245,158,11,0.14)` | `#FBBF24` | 等待人工应答 |
| converged（绿） | `--flow-converged` | `#22C55E` | `rgba(34,197,94,0.14)` | `#4ADE80` | 已收敛，可定稿 |

> 状态流程的「已通过/当前/未来」三态渲染规则见 §4 状态胶囊。

---

## 3. Typography（排版）

### Font Stacks（复用现有，零新增依赖）
- **Sans（正文/UI）**：`'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans SC", "PingFang SC", sans-serif`
- **Mono（轮次号/ID/代码）**：`'JetBrains Mono', ui-monospace, Consolas, "Cascadia Code", monospace`
- 引入：`<link>` 或自托管 woff2（生产建议自托管 Inter + JetBrains Mono）。

### Scale（工程化工具密度：14px 基准正文）

| Level | Size | Weight | Line-height | 字距 | 用途 |
|-------|------|--------|-------------|------|------|
| Display | 18px / 1.125rem | 700 | 1.3 | -0.02em | 工作台主标题（左栏标题/右栏页头） |
| H2 | 15px / 0.938rem | 600 | 1.4 | -0.01em | 区块标题、Round 分组头 |
| H3 | 14px / 0.875rem | 600 | 1.4 | 0 | 卡片标题、问题标题 |
| Body | 14px / 0.875rem | 400 | 1.6 | 0 | 正文、Markdown 正文、回答输入 |
| Small | 13px / 0.813rem | 500 | 1.5 | 0 | 元信息、行内说明 |
| Meta | 12px / 0.75rem | 500 | 1.4 | 0.01em | 标签、状态文字、胶囊 |
| Micro | 11px / 0.688rem | 600 | 1.3 | 0.04em | 角标、轮次序号、大写 eyebrow |

- 数字统一 `font-variant-numeric: tabular-nums`（轮次 `第 N/M 轮`、未读角标、计数）。
- Markdown 正文：标题/列表/代码块沿用 `--font-sans` 与 `--font-mono`，代码块用 `--surface-2` 底 + 1px 边框。

---

## 4. Component Styles（组件样式）

### 4.1 Button
| 变体 | 背景 | 文字 | 边框 | 圆角 | hover | 用途 |
|------|------|------|------|------|-------|------|
| Primary | `var(--primary)` | `#FFFFFF` | 透明 | 8px | `var(--primary-hover)` + 轻微上浮 | 「一键提交本轮」「确认生成 Story」 |
| Secondary | `var(--surface-2)` | `var(--text)` | `1px solid var(--border)` | 8px | `var(--surface-3)` | 「委派」「取消」 |
| Ghost | 透明 | `var(--text-2)` | `1px solid var(--border)` | 8px | `var(--surface-3)` + `var(--text)` | 「不确定/skipped」标记、折叠触发 |
| Danger | 透明 | `var(--danger-text)` | `1px solid var(--danger)` | 8px | `rgba(239,68,68,0.12)` | 删除/放弃 |

- 通用：height 36px（sm 32px）；padding 0 16px（sm 0 12px）；font 14px/600；focus → `0 0 0 3px var(--primary-ring)`；transition 0.15s。
- 主 CTA「确认生成 Story」可加 `box-shadow: 0 8px 24px -6px rgba(99,102,241,0.45)`（沿用现有 `--sh-brand`）。

### 4.2 Input（行内 answer 输入）
- height 38px（多行 textarea 自适应，min 38px）；padding 9px 12px；border `1px solid var(--border)`；radius 8px；bg `var(--surface-2)`。
- focus：border `var(--primary)` + `box-shadow: 0 0 0 3px var(--primary-ring)`。
- placeholder：`var(--text-3)`；文字 `var(--text)`；font 14px/400。
- 「skipped 不确定」：以 Ghost chip 形式叠加在输入区右侧，激活态变 `var(--surface-3)` + `var(--text-2)` 描边。

### 4.3 Card（问题卡 / 元信息卡 / 定稿面板）
- bg `var(--surface)`；border `1px solid var(--border)`；radius 8px（面板可用 10–12px）；padding 16px（元信息卡 12–14px）。
- shadow：默认 `none`（靠边框分层）；hover：`border-color: var(--border-strong)`。
- 问题卡结构：标题行（question + rationale 折叠触发）→ rationale（默认收起，可展开）→ 行内 answer 输入 → 底部操作行（skipped 标记 + 提交）。

### 4.4 状态胶囊 / 顶栏状态流程（Stepper）
- 容器：横向 flex，gap 6px；每步 = 圆点(8px) + 文字(Meta 12px)。
- 渲染规则：
  - **已通过步**：点色实心 + 文字 `var(--text-2)`；
  - **当前步**：点色实心 + 文字 `var(--text)` + 外环 `0 0 0 3px var(--primary-soft)`；
  - **未来步**：点色 `var(--border)` + 文字 `var(--text-3)`。
- 五态色取自 §2.4（`--flow-*`）。步骤间用 1px 连接线（`var(--border)`），当前步连接线提亮。
- **amber warning 胶囊**（独立提示，如「有 N 条待应答」）：`bg rgba(245,158,11,0.14)` + `text var(--warning-text)` + 1px `var(--warning)` 描边，radius 9999px。

### 4.5 通知铃铛 + 未读角标
- 图标按钮：24×24，icon `var(--text-2)`，hover `var(--surface-3)` + `var(--text)`。
- 未读角标：绝对定位右上，`min 16px` 圆，`bg var(--danger)` + 白字 `#fff`，Micro 11px/700，`tabular-nums`；0 未读时隐藏。

### 4.6 折叠面板（历史轮次）
- 触发行：chevron(12px) + 「Round N」+ 右侧计数/状态点；height 40px；hover `var(--surface-2)`。
- 展开：下方渲染该轮问题卡列表（沿用 §4.3）；过渡 0.18s ease。
- 当前轮（可作答）默认展开，历史轮默认收起。

### 4.7 空态（Empty State）
- 居中：内联线性 SVG 插画（看板/对话框）+ 文案 `var(--text-2)` + 可选主按钮。
- 样式：`bg var(--surface-2)` + `2px dashed var(--border)` + radius 12px + padding 40px。

---

## 5. Layout（布局）

### Grid（桌面优先，基准 1440px）
```
.workbench {
  display: grid;
  grid-template-columns: 360px 1fr;   /* 左栏固定 360px，右主区自适应 */
  gap: 24px;
  max-width: 1360px;                  /* 内容居中，1440 视口留左右 gutter */
  margin: 0 auto;
  padding: 24px;
}
```
- **左栏**（sticky，全高可滚）：标题 + Markdown 正文 + 元信息卡（状态点 / 轮次 / 创建人）。
- **右主区**：按 round 分组的问题卡流；顶栏状态流程 + 轮次进度 + 铃铛。
- 顶栏：`height 52px`，半透明磨砂 `backdrop-filter: blur(12px)` + 1px 底边（沿用现有 `.topbar`）。

### Spacing Scale（4px 基准，复用现有命名）
| Token | Value | 用途 |
|-------|-------|------|
| `--space-1` | 4px | 行内微距 |
| `--space-2` | 8px | 紧凑间距、胶囊内距 |
| `--space-3` | 12px | 卡片内元素距 |
| `--space-4` | 16px | 默认间距、卡片 padding |
| `--space-5` | 20px | 区块内距 |
| `--space-6` | 24px | 栏间距、区块 padding |
| `--space-8` | 32px | 大区隔 |
| `--space-10` | 40px | 面板 padding |
| `--space-12` | 48px | 顶栏下方主区上距 |

### Radius
- `--r-sm: 8px`（**需求假定值，交互组件统一**）
- `--r-md: 12px`（卡片/面板）
- `--r-lg: 16px`（大面板/弹层）
- `--r-full: 9999px`（胶囊/角标/头像）

---

## 6. Depth & Elevation（深度与层级）

| Level | Shadow | 用途 |
|-------|--------|------|
| Flat | none | 默认表面（靠 1px 边框分层，Linear 风格） |
| Raised | `0 1px 2px rgba(0,0,0,0.4)` | 卡片 hover、下拉 |
| Floating | `0 8px 24px rgba(0,0,0,0.5)` | 折叠展开内容、铃铛菜单、popover |
| Overlay | `0 12px 32px rgba(0,0,0,0.6)` | 定稿 spec 预览弹层、模态 |

### Z-index Scale
- Base: 0 ｜ Sticky 顶栏: 100 ｜ Dropdown/铃铛菜单: 200 ｜ Modal/定稿预览: 300 ｜ Toast: 400

---

## 7. Cautions（注意事项）

### Never Do
- 不在大面积区域使用高饱和填充（仅状态点/主按钮/聚焦环可用饱和色）。
- 不混用圆角（交互组件统一 8px，禁止 4px/16px 随机混搭）。
- 暗色下不依赖「白底+灰边」的思维；用 `--surface` 分级 + 边框表达层级。
- 不为状态流程引入 emoji 图标；一律用「色点 + 文字」双编码（色盲可读）。
- 不引入第 2 套无衬线字体；标题不靠字体变化，靠字重/字距/层级。

### Prefer
- 用 `var(--primary-soft)` 淡底表达「选中/当前」，而非描粗边框。
- 状态点 + 文字双编码；长文案用 Markdown 渲染而非纯文本。
- 历史轮次默认折叠，当前轮默认展开，控制单屏信息量。
- 数字统一 `tabular-nums`，对齐轮次与计数。

---

## 8. Responsive Behavior（响应式行为）

> 需求：桌面优先、单屏可滚动、非响应式必需。下为**可选降级**，不作为验收项。

| Name | Width | Behavior |
|------|-------|----------|
| Desktop | ≥ 1280px | 完整双栏（360px / 1fr） |
| Condensed | 1024–1280px | 左栏缩至 300px，仍双栏 |
| Stack | < 1024px（可选） | 单栏：左栏转为顶部可收起概要，右栏问题卡流置下 |

- 顶栏状态流程在 < 1024px 可横向滚动，不换行截断。

---

## 9. Agent Prompt Guide（Agent 生成指南）

### Key Instructions
- 默认 **dark-first**：所有表面用 `--surface*` 分级，不用白底思维。
- 全部颜色/间距/圆角**只引用 CSS 变量**，禁止硬编码散落；新增 token 写入 `:root`（暗色集），与现有 `styles.css` 同文件共存。
- 主色用 `var(--primary)`（`#6366F1`）；如需更严格 Linear fidelity 可整体替换为 `#5E6AD2`（见下）。
- 五态流程严格用 `var(--flow-*)` 三件套（点/淡底/文字），保证暗色对比度。
- 字体只用 Inter + JetBrains Mono；数字 `tabular-nums`。
- 组件圆角统一 8px（`--r-sm`），面板可 12px。
- Angular 观感：用 `<mat-*>`/原生组件时覆写样式映射到上述 token；避免 Material 默认亮色主题。

### Quick CSS Snippet（可直接并入 `styles.css`）
```css
:root {
  /* —— 暗色变量集（需求假定为底，补全语义色与组件态）—— */
  --bg: #0F1115;
  --surface: #1A1D24;
  --surface-2: #161922;
  --surface-3: #22262F;
  --border: #2A2F3A;
  --border-strong: #3A4150;
  --text: #E6E8EB;
  --text-2: #9AA4B2;
  --text-3: #7E8898;  /* 提亮以满足 WCAG AA 4.5:1（原 #6B7280 仅 3.5–3.9:1）；仅供装饰性/非必要文本，可读小字请用 --text-2 */

  /* 主色（需求假定 #6366F1；如需 Linear 规范色改为 #5E6AD2） */
  --primary: #6366F1;
  --primary-hover: #5457E5;
  --primary-active: #4A4CE0;
  --primary-soft: rgba(99,102,241,0.12);
  --primary-ring: rgba(99,102,241,0.28);
  --grad: linear-gradient(135deg, #6366F1 0%, #8B5CF6 55%, #A855F7 100%);

  /* 通用语义色 */
  --success: #22C55E; --success-text: #4ADE80;
  --warning: #F59E0B; --warning-text: #FBBF24;
  --danger: #EF4444;  --danger-text: #F87171;
  --info: #3B82F6;    --info-text: #93B4FB;
  --violet: #8B5CF6;

  /* 五态流程色 draft→queued→analyzing→awaiting→converged */
  --flow-draft: #9AA4B2;       --flow-draft-soft: rgba(154,164,178,0.12); --flow-draft-text: #9AA4B2;
  --flow-queued: #3B82F6;      --flow-queued-soft: rgba(59,130,246,0.14);  --flow-queued-text: #93B4FB;
  --flow-analyzing: #22D3EE;   --flow-analyzing-soft: rgba(34,211,238,0.14);--flow-analyzing-text: #67E8F9;
  --flow-awaiting: #F59E0B;    --flow-awaiting-soft: rgba(245,158,11,0.14); --flow-awaiting-text: #FBBF24;
  --flow-converged: #22C55E;   --flow-converged-soft: rgba(34,197,94,0.14); --flow-converged-text: #4ADE80;

  /* 排版 */
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans SC", "PingFang SC", sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, Consolas, "Cascadia Code", monospace;

  /* 间距（4px 基准） */
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px;
  --space-5: 20px; --space-6: 24px; --space-8: 32px; --space-10: 40px; --space-12: 48px;

  /* 圆角 */
  --r-sm: 8px; --r-md: 12px; --r-lg: 16px; --r-full: 9999px;

  /* 深度 */
  --sh-raised: 0 1px 2px rgba(0,0,0,0.4);
  --sh-floating: 0 8px 24px rgba(0,0,0,0.5);
  --sh-overlay: 0 12px 32px rgba(0,0,0,0.6);

  /* 布局 */
  --topbar-h: 52px;
  --workbench-maxw: 1360px;
  --col-left: 360px;
}
```

---

## 10. 布局与组件映射速查（供原型构建师）

| 区域 | 组件 | 关键 token |
|------|------|-----------|
| 顶栏 | 状态流程胶囊（draft→…→converged） | `--flow-*` 三件套 + Stepper 渲染规则 §4.4 |
| 顶栏 | 轮次进度「第 N/M 轮」 | `--font-mono` + `tabular-nums` + `--text-2` |
| 顶栏 | 通知铃铛 + 未读角标 | §4.5，`--danger` 角标 |
| 左栏 | 标题 + Markdown 正文 | `--text` / H2 15px / `--font-sans` |
| 左栏 | 元信息卡（状态点/轮次/创建人） | 卡片 §4.3 + 状态点 `--flow-*` |
| 右主区 | Round 分组问题卡 | §4.3 + §4.6 折叠 |
| 右主区 | question + rationale + 行内 answer 输入 + skipped | §4.2 输入 + §4.1 Ghost |
| 右主区 | 每轮「一键提交本轮」 | §4.1 Primary |
| 收敛后 | 「定稿 spec 预览」面板 | §4.3 面板 + §6 Overlay |
| 收敛后 | 「确认生成 Story」主按钮 | §4.1 Primary + `--sh-brand` |

*文档结束 — 可直接交给原型构建师落入 `frontend/src/styles.css` 并构建 Angular 单页原型。*
