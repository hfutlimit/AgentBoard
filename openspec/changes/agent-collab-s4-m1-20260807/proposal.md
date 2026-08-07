# Proposal：评审运营前端视图（S4 M1）

> ID: agent-collab-s4-m1-20260807 · Epic 122 / Story 233 / Task 1016
> 上游：文档 #50「多 Agent 自动协作闭环：需求与方案」§8 切片 3（评审统计与运营视图）

## 1. 问题

Epic 122 S3 M2/M3 已交付评审运营**后端**能力：

- `GET /api/review-stats` —— 项目级评审统计（Story/Task approved/rejected/pending/blocked、
  平均轮次、驳回率、超时未决数、按 reviewer 聚合工作量）；
- `POST /api/review-stats/reassign-timeout` —— 超时重派自愈（轮次上限 blocked 护栏）；
- 多数决评审（review_votes 投票表 + quorum 结算）。

但前端**没有任何评审运营视图**：项目统计页只展示任务维度（总任务/开发中/完成率/每日新增完成），
评审闭环的运营数据（驳回率、超时未决、评审人工作量）对用户不可见，超时重派只能靠 Worker
轮询或直接 curl API 触发，缺乏人机可操作的运营入口。

## 2. 目标

在项目详情页「统计」Tab 新增**评审运营面板**（纯前端增量，零后端契约变更）：

1. **统计卡片**：Story 已通过评审（x/y + 待评/驳回/blocked 明细）、Task 同、平均评审轮次、驳回率；
2. **超时未决数 + 一键重派**：「扫描超时并重派」按钮（30min 无活动口径，max 20/run），
   触发后展示结果摘要（Story 重派数 / Task 重派数 / 置 blocked / 无候选 / 多数决结算数）；
3. **评审人工作量条**：按 reviewer 聚合 reviewed/approved/rejected，条形图可视化；
4. **空态与降级**：无评审数据展示友好占位；API 失败展示错误 + 重试按钮；
   评审统计加载失败不影响任务统计渲染（Promise.all + catch 降级 null）。

## 3. 约束

- 纯前端改动：api.service.ts / models.ts / app.ts / app.html / app.css 五个文件；
- 复用既有 `stat-card` / `badge` / `ghost-sm` / `tab-load-error` / `empty-inline` 组件与样式变量
  （`--card-bg` / `--border` / `--text-muted` 自动适配暗色主题）；
- 零新增第三方依赖；不触碰 18001；不破坏既有 REST 契约。

## 4. 验收

- 评审面板在项目「统计」Tab 渲染正常（有数据 / 空态两态）；
- 重派按钮触发 `POST /api/review-stats/reassign-timeout`，成功后刷新统计 + 结果摘要展示；
- 0 控制台错误；既有前端测试零回归（`npm run build` 通过 + Playwright E2E 0 报错）。
