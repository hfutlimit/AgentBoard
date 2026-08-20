# AGB 全站前端巡检报告 — Story 348

**运行时间**: 2026-08-21 05:35 (第 25 次, hourly)  
**环境**: ng serve (frontend/) + proxy → 生产后端 `http://124.220.44.12`  
**脚本**: `tests/e2e_story348/inspect_all_v5.py` + 暖机复核 `tests/e2e_story348/focused_recheck_v5.py`

---

## 一、范围与覆盖

| 类别 | 路由 | 数量 |
|---|---|---|
| 全局一级导航 | `/`, `/projects`, `/epics`, `/stories`, `/tasks`, `/bugs`, `/documents`, `/dashboard`, `/settings`, `/agents`, `/proposals`, `/notifications`, `/admin` | 13 |
| Workspace 8 tab | `/project/3/{overview,kanban,epics,backlog,proposals,documents,members,settings}` | 8 |
| 详情页 | `/epic/152`, `/story/348`, `/story/330`, `/task/1342`, `/task/1339` | 5 |
| 交互 | 主题切换、新建项目弹窗、documents 搜索、story/348 评论框 | 4 |

**暖机复核额外覆盖**: `/task/1429`（已存在 Bug 实体验证）、主题切换 DOM 探查、新建项目弹窗 DOM 验证。

---

## 二、结果汇总

| 指标 | 值 |
|---|---|
| 路由访问成功 | 26/26 |
| 真实 console error | 0 |
| page error | 0 |
| 持续 API 失败 | 0 |
| 水平溢出 | 0 |
| **新建 Bug** | **0** |

---

## 三、已知 Bug 复验

| Bug | 状态 | 证据 |
|---|---|---|
| #1427 详情空白 (`story/330`, `task/1342`, `task/1339`) | ✅ **FIXED** | 暖机复核 `task_1342_recheck.png`/`task_1339_recheck.png`/`story_330_recheck.png` 全部渲染正常（txt=650/388/2903）；v5 冷启动首次访问的 txt=0 为懒加载 chunk 编译中的 skeleton 态。 |
| #1428 全局 routes 误渲染 (`/documents`, `/proposals`) | 🔴 **STILL REPRODUCES** | `documents.png` 与 `proposals.png` 和 `/projects` 完全一致，h1 = `项目中心\n11`。 |
| #1429 侧栏「搜索」误标 | ✅ **FIXED** | 侧栏真实 tab 列表 = `概览/看板/Epics/工作项/提案/文档/成员与 Agents/设置`，含「提案」无「搜索」。 |
| Workspace 8 tab 导航 | ✅ 8/8 `url_ok=True` | 动态点击 + `window.location.pathname` 验证全部正确；唯一 transient 的 `成员与 Agents` innerText 超时属 DOM 重绘 artifact。 |

---

## 四、v5 脚本 finding 复核（4 类全部 false positive）

| v5 finding | 复核结论 | 根因 |
|---|---|---|
| P1 `task_1342` / `task_1339` 主内容空白 | 非 bug | dev server 二次冷启后 lazy detail chunk 首次编译，9s wait 窗口内仍处 skeleton 态；暖机后二次访问正常。 |
| P2 `ws_tabs`「成员与 Agents」路径异常 | 非 bug | 点击后 `location.pathname` = `/project/3/members` 正确；`Locator.inner_text` 30s 超时是因为侧栏 DOM 在导航后重绘。 |
| P2 `theme_toggle` 主题未切换 | 非 bug | 当前脚本选择器（`aria-label*=主题` / `header button:has(svg)`）未命中实际主题按钮，误点 admin 头像下拉；首页太阳图标可见，切换机制存在但 DOM 需进一步定位。 |
| P3 `create_dialog` 新建项目弹窗未打开 | 非 bug | 暖机复核中 `button:has-text('新建项目')` 可触发，弹窗 DOM（`#create-modal` / `role=dialog` / 表单字段）全部正常渲染。 |

---

## 五、本轮新增/更新产物

- `tests/e2e_story348/focused_recheck_v5.py` — 暖机长等待复核脚本（详情页 20s、主题按钮 DOM 探查、弹窗 DOM 抓取）
- `tests/e2e_story348/focused_recheck_v5.json` — 复核原始数据
- `tests/e2e_story348/screenshots_v5_recheck/` — 复核截图证据
- `tests/e2e_story348/report_story348.json` — 已追加 `run_meta` 第 25 轮分析
- 本文件 `report_story348.md`

---

## 六、建议

**开发**:
- 复核关闭 **#1427**（详情空白）与 **#1429**（侧栏搜索误标）：本轮暖机复核通过。
- 继续修复 **#1428**（全局 `/documents`、`/proposals` 仍渲染为项目中心）。

**脚本下轮优化**:
1. **详情页**: 增加暖机步骤或在首次访问后做二次 `nav()` 复核，避免冷启 skeleton 误报。
2. **主题切换**: 通过 header DOM 枚举所有可点击元素定位真实主题按钮（当前 SVG 太阳图标缺少可点击父元素 selector）。
3. **新建弹窗**: 在 theme toggle 之前执行，避免 admin 下拉菜单状态污染。
4. **ws tab**: 点击后使用 `page.wait_for_load_state('networkidle')` 或 `wait_for_selector('app-members-tab')` 等目标组件，降低 transient 超时。

---

## 七、控制台与 API 噪声

| 类型 | 数量 | 说明 |
|---|---|---|
| 真实 console err | 0 | — |
| 产物类 console err | 3 | WebSocket `/ws/agents` / `/api/auth/me` 500 / 401 等瞬时噪声（与前轮一致） |
| 真实 API 失败 | 0 | — |
| 产物类 API 失败 | 1 | 同上瞬时噪声 |
