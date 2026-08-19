# Epic 149 / Story 317 自动化验证报告

- **Story**: 阶段1 外壳先行：令牌合并 + 两级 Shell 替换 topbar/tab
- **Story ID**: 317 ｜ 项目 AGB (project_id=3) ｜ 验证时间: 2026-08-19
- **触发人**: AGB 自动化测试代理 (WebappTestingExpert, hourly)

## 1. 验证方法

| 维度 | 做法 |
|---|---|
| 数据源 | 生产后端 http://124.220.44.12/api (持有 AGB 项目 3 / Epic 149 真实数据) |
| 前端 | 本地 ng serve 127.0.0.1:4200，编译源含 commit 2f3d327 (Story 317) + 工作树 app.html/app.ts 改动 |
| API 桥 | tests/e2e_epic149/proxy.conf.json 将 /api、/ws 转发到生产后端，浏览器同源通信不受生产 CORS 限制 |
| 鉴权 | 生产 /api/auth/login (admin/admin123) 拿 JWT 注入 localStorage.agentboard_token |
| 浏览器 | Playwright Chromium 1440x900，--no-proxy-server 关闭浏览器直连代理 |
| 脚本 | tests/e2e_epic149/test_story317_e2e.py (主)、pinpoint_chars.py (字符图标溯源) |
| 报告 | tests/e2e_epic149/report_story317.json |

## 2. 验证项与结果（317 改造范围）

| # | 验证项 | 结果 | 证据 |
|---|---|---|---|
| 1 | Home Shell 渲染为原型（项目浏览器 Master-Detail + Agents） | PASS | screenshots/home_shell.png |
| 2 | Workspace Shell 渲染为原型（深色 navy 侧边栏 + 项目切换器 + 返回按钮） | PASS | screenshots/workspace_shell.png |
| 3 | navy 令牌 #10243e 实际应用到 shell | PASS | DOM 扫描 home=1、workspace=3 元素 backgroundColor=rgb(16,36,62) |
| 4 | 现有视图在新外壳内无回归 | PASS | home dashboard/metrics/chart、workspace project header/tabs/metrics/team/epics 全部正常 |
| 5 | SVG symbol 已替换 shell 字符图标 | PASS | sidebar/topbar 区域 SVG <use> 12-13 处；pinpoint 扫描 shell 字符图标命中=0 |
| 6 | 双令牌 navy+blue 共存无冲突 | PASS | 构建通过；blue #2864dc 出现在主操作按钮 |
| 7 | 无控制台报错 / 无页面 JS 异常 | PASS | console.error=0, console.warning=0, pageerror=0 |
| 8 | 侧边栏导航可交互 | PASS | nav_0/nav_1：点击 Agents/项目列表 均 SPA 内导航无崩溃 |
| 9 | 响应式断点（1160/840） | 未覆盖 | 本次默认 1440 桌面宽；建议下一轮加 viewport 切换复测 |

317 范围结论：通过 (PASS)。外壳层（侧边栏、顶栏、令牌、SVG 图标）改造全部到位，无回归与报错。

## 3. 范围外发现（不构成 317 失败，供 319 参考）

字符图标 1 处：workspace 主区 tab-btn.tab-icon (INTERNAL)，出现在横排按钮的"设置"项。
317 scope 明确写"内部视图暂用现有逻辑，仅外壳层换原型结构"，属预期遗留，归 Story 319。

pinpoint 证据：
- ch: ⚙, count: 2
- sample: BUTTON.tab-btn "⚙设置" INTERNAL；SPAN.tab-icon "⚙" INTERNAL
- shell 区域（aside/topbar）字符图标命中数 = 0

## 4. 自动化处置

- dev Task 1282 in_review -> done (reason: completed)
- QA Task 1283 todo -> in_progress -> in_review（提交本报告作为测试结果，移交复核关闭）
- Story 317 in_review -> done
- Story 317 / dev 1282 留 markdown 评论：测试通过结论、范围、脚本与截图路径、范围外遗留说明

## 5. 产物清单

- tests/e2e_epic149/proxy.conf.json
- tests/e2e_epic149/test_story317_e2e.py
- tests/e2e_epic149/pinpoint_chars.py
- tests/e2e_epic149/ngserve.log（首次构建 11.8s 通过）
- tests/e2e_epic149/screenshots/home_shell.png
- tests/e2e_epic149/screenshots/workspace_shell.png
- tests/e2e_epic149/screenshots/nav_0.png
- tests/e2e_epic149/screenshots/nav_1.png
- tests/e2e_epic149/screenshots/pinpoint_workspace.png
- tests/e2e_epic149/report_story317.json（原始）
- tests/e2e_epic149/report_story317.md（本文）
