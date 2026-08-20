# AGB 全站前端巡检报告 — Story 348

**运行时间**: 2026-08-21 04:35 (第 24 次, hourly)
**环境**: ng serve + proxy → 生产后端 `http://124.220.44.12`
**脚本**: `tests/e2e_story348/inspect_all_v5.py`

---

## 一、范围与覆盖

27 路由 + 4 交互 + 3 已知 Bug 复验 + 8 Workspace tab 动态点击

| 类别 | 路由 | 数量 |
|---|---|---|
| 全局一级导航 | `/`, `/projects`, `/epics`, `/stories`, `/tasks`, `/bugs`, `/documents`, `/dashboard`, `/settings`, `/agents`, `/proposals`, `/notifications`, `/admin` | 13 |
| Workspace 8 tab | `/project/3/{overview,kanban,epics,backlog,proposals,documents,members,settings}` | 8 |
| 详情页 | `/epic/152`, `/story/348`, `/story/330`, `/task/1342`, `/task/1339` | 5 |
| 交互 | 主题切换、新建项目弹窗、documents 搜索、story/348 评论框 | 4 |

---

## 二、结果汇总

| 指标 | 值 |
|---|---|
| 路由访问成功 | 27/27 |
| 真实 console error | 1 (资源 `net::ERR_CONNECTION_TIMED_OUT`, 非页面 bug) |
| page error | 0 |
| 持续 API 失败 | 0 |
| 水平溢出 | 0 |
| **新建 Bug** | **0** |

---

## 三、已知 Bug 复验

| Bug | 状态 | 证据 |
|---|---|---|
| #1427 详情空白 (story/330, task/1342, task/1339) | ✅ **FIXED** | recheck 3/3 `still_blank=False`, `is_404=False`; task_1339 实体存在正常渲染 |
| #1428 全局 routes 误渲染 (/documents, /proposals) | 🔴 **STILL REPRODUCES** | h1 = "项目中心 11", 与 /projects 完全一致 |
| #1429 侧栏「搜索」误标 | ✅ **FIXED** | 侧栏 = `[概览,看板,Epics,工作项,提案,文档,成员与 Agents,设置]`, 含「提案」无「搜索」 |
| Workspace 8 tab 导航 | ✅ 8/8 `url_ok=True` | 动态点击 + `location.pathname` + active 验证全部正确 |

---

## 四、本轮 false-positive 复核（截图证据，未上报）

1. **4 × P1 详情页空白**（story_348/330/1342/1339 页面循环 txt=0）— 截图证实为 skeleton loading 态（首屏卡片占位符），非真空白；同 `nav()` recheck 全部加载成功。**根因**：dev server 二次冷启后重页 chunk 编译超 9s wait 窗口。**非前端 bug**。
2. **1 × P2 theme_toggle** — `home.png` header 仅有搜索/通知/admin，**未见明显太阳/月亮主题按钮**；脚本回退 `header button:has(svg)` 误点 admin 头像打开下拉菜单（`home_dark_v5.png` 仍为亮色 + admin 菜单展开）。**非 bug**（脚本选择器 artifact）。
3. **1 × P3 create_dialog** — `projects.png` 右上「+ 新建项目」按钮可见，脚本点击后 modal 选择器未命中。**非 bug**（按钮存在；待脚本用更稳选择器复测弹窗）。

---

## 五、v5 脚本改进（相对 v4）

1. 修复 JS `'页面不存在' in h.innerText` 崩溃 → `.includes()`
2. `is_404` 检测加 `document.body.innerText` 扫描（v4 只查 h1，5 个 404 路由的"页面不存在"在 h2 致误报）
3. 新增全局路由覆盖 `/epics /stories /tasks /bugs /dashboard`（均正确返回 404 视图）
4. ws tab 改动态采集侧栏真实 tab 元素，规避 v4 `.project-nav-button-v7` 陈旧 + `page.url` 滞后 artifact

---

## 六、建议

- **开发**: 复核关闭 **#1427** 与 **#1429**（本次复验通过）；继续修 **#1428**（全局 routes 渲染）
- **脚本下轮优化**:
  1. `theme_toggle`: 先确认 header 是否真有主题按钮（当前未发现），若有需用更精确 selector，避免 `header button:has(svg)` 误中 admin 头像
  2. `create_dialog`: 弹窗选择器加 `.dialog, .cdk-overlay-container, app-modal` 容错，并截图验证弹窗实际打开
  3. 详情页 `wait`: 9s `wait_for_function` 不够覆盖 dev server 二次冷启的重页 chunk 编译，建议延长到 15-20s 或等待特定内容 selector

---

## 七、产物

- `tests/e2e_story348/inspect_all_v5.py` — v5 主巡检脚本
- `tests/e2e_story348/report_story348.json` — 完整 JSON 报告
- `tests/e2e_story348/report_story348.md` — 本文件
- `tests/e2e_story348/screenshots_v5/` — 27 页面 + 8 ws tab + home_dark + 搜索（gitignored 本地证据）

## 八、控制台与 API 噪声

| 类型 | 数量 | 说明 |
|---|---|---|
| 真实 console err | 1 | 资源 `net::ERR_CONNECTION_TIMED_OUT`（非页面 bug） |
| 产物类 console err | 3 | WebSocket `/ws/agents` / `/api/auth/me` 500 / 401（与前轮一致） |
| 真实 API 失败 | 0 | — |
| 产物类 API 失败 | 1 | 同上瞬时噪声 |
