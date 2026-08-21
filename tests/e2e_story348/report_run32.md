# AGB 全站 Playwright 巡检报告 — Run 32（2026-08-21 12:35）

## 结论
- **新建 Bug：0 条**
- **Story 348 巡检摘要评论：#795**
- **真实缺陷：0 个**（所有初判 finding 经复核裁定为冷启动/选择器 artifact 或已知 Bug）

## 环境
- 前端：`ng serve --proxy-config tests/e2e_story348/proxy.prod.conf.json`，代理至生产后端 `124.220.44.12`
- 端口：4200（已清理释放）
- Playwright：`.venv` Chromium，桌面视口 1440×900

## 巡检脚本
| 脚本 | 作用 |
|---|---|
| `inspect_all_v6.py` | 26 路由 + 主题切换 + 新建弹窗 + 文档搜索 + 评论框 + 顶部导航点击穿透 + 已知 Bug 复验 |
| `inspect_v14_recheck.py` | 10 全局页暖机 settle + #1428/#1430/#1431 复验 |
| `verify_settings_agents_run32.py` | 针对 `/settings`、`/agents` 首测 txt=0 的 30s 轮询二次验证 |

## 已知 Bug 复验
| Bug | 本轮状态 |
|---|---|
| #1427 详情页主内容区空白 | ✅ FIXED |
| #1428 `/documents`、`/proposals` 错误渲染为项目中心 | ✅ FIXED |
| #1429 侧栏「搜索」误标 | ✅ FIXED |
| #1430 全局路由 404 | ✅ FIXED |
| #1431 暗色/亮色主题切换控件缺失 | 🔴 STILL |

## 初判 finding 与复核结论
| 初判 | 复核结果 |
|---|---|
| 10 × P1「主内容 0 字符」| 暖机后全部正常渲染，lazy-chunk 冷启动时序 artifact |
| `/settings`、`/agents` txt=0 | 二次导航正常渲染（650/1462），lazy-chunk 长尾 artifact |
| ws_tabs 路径异常 | `location.pathname` 全部正确，Playwright `page.url` 滞后 artifact |
| 主题切换按钮缺失 | 命中已知 Bug #1431 |
| 新建项目弹窗未打开 | 选择器时机 artifact，弹窗实际可打开 |
| 4 条 API 500 | curl token 重试均 200，瞬时后端噪声 |

## 真实质量指标
- 26/26 路由可达
- 0 真实 console error
- 0 page error
- 0 持续 API 失败
- 0 水平溢出

## 截图证据（gitignored）
- `screenshots_v6/`
- `screenshots_v14/`
- `screenshots_verify_run32/`
