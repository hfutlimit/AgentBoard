# Design：评审运营前端视图（S4 M1）

> ID: agent-collab-s4-m1-20260807 · Epic 122 / Story 233 / Task 1016
> 上游：Proposal agent-collab-s4-m1-20260807；后端接口由 S3 M2 交付（service.get_review_stats / scan_review_timeouts）

## 1. 目标与范围

在项目详情页「统计」Tab 内追加**评审运营面板**，展示评审闭环运营数据并提供超时重派入口。
纯前端增量：不新增后端端点、不修改 REST 契约、不改动项目 Tab 结构（评审面板挂在既有
`stats` Tab 内容区，随 stats 一起加载）。

## 2. 数据流

```
项目页 selectProjectTab('stats')
  └─ loadProjectTab('stats', projectId)
      ├─ Promise.all([
      │    getProjectStats(projectId)              → projectStats 信号（既有）
      │    getReviewStats(projectId).catch(()=>null) → reviewStats 信号（新增，降级 null）
      │  ])
      └─ 渲染：既有统计卡/图表 + 评审运营面板（reviewStats 非 null 时）
```

触发重派：

```
triggerReassignTimeout()
  └─ POST /api/review-stats/reassign-timeout { timeout_minutes:30, max_per_run:20 }
      ├─ 成功 → reviewReassignResult 信号（结果摘要）+ notify 轻提示 + loadReviewStats 刷新
      └─ 失败 → notify(error)（reviewReassignBusy 防重入）
```

## 3. 前端改动清单

### 3.1 models.ts（新增类型）

| 类型 | 字段 | 说明 |
|---|---|---|
| `ReviewBucketStats` | total / approved / rejected / pending / blocked | Story 或 Task 评审统计桶 |
| `ReviewReviewerAgg` | reviewer_id / reviewer_name? / reviewed / approved / rejected | 按评审人聚合 |
| `ReviewStats` | stories / tasks / rounds / reject_rate / timeout_pending / by_reviewer | GET /api/review-stats 响应 |
| `ReviewTimeoutResult` | stories_reassigned / tasks_reassigned / blocked / no_candidate / stories_settled / tasks_settled | 重派结果 |

### 3.2 api.service.ts（新增 2 方法）

- `getReviewStats(projectId, days=7)` → GET `/api/review-stats`（params: project_id, days）；
- `reassignReviewTimeout(projectId?, body)` → POST `/api/review-stats/reassign-timeout`
  （带 project_id 时走 project_access_middleware 项目级权限；Worker 全局场景不传）。

### 3.3 app.ts（信号 + 方法）

| 信号 | 用途 |
|---|---|
| `reviewStats` | 评审统计数据（null = 未加载/失败降级） |
| `reviewStatsLoading` / `reviewStatsError` | 加载态 / 错误文案 |
| `reviewReassignBusy` | 重派防重入 |
| `reviewReassignResult` | 重派结果摘要 |

方法：`loadReviewStats(projectId)`（错误降级 null + error 信号）、
`triggerReassignTimeout()`（POST → 成功刷新 + notify + 结果）、
`maxReviewerReviewed(rs)`（条形图最大值）。

### 3.4 app.html（stats Tab 追加区块）

- 面板头：标题「评审运营」+ 超时未决数 + 「🔁 扫描超时并重派」按钮（busy 时禁用）；
- 结果摘要（badge 组：Story 重派 / Task 重派 / 置 blocked / 无候选 / 多数决结算）；
- 统计卡 grid（复用 `.stat-card`）：Story 通过 x/y、Task 通过 x/y、平均轮次、驳回率；
- 评审人工作量条（`.review-reviewer-row`：名字 + 渐变条 + badge 计数）；
- 空态 `暂无评审数据…`；错误态复用 `.tab-load-error` + 重试按钮。

### 3.5 app.css（新增样式）

`.review-ops-panel`（上边框分隔）、`.review-ops-header`、`.review-ops-actions`、
`.review-ops-hint`、`.review-reassign-result`、`.review-ops-grid`、`.stat-sub`、
`.review-by-reviewer`、`.review-reviewer-row/bar/fill/counts/name` —— 全部基于既有 CSS 变量，
暗色主题自动适配。

## 4. 边界与降级

- `getReviewStats` 失败（404/网络）→ catch 降级 `null`，**不阻断**既有任务统计渲染；
  面板内展示错误 + 重试；
- 空数据（stories.total === 0 && tasks.total === 0）→ 友好占位；
- `by_reviewer` 为空 → 隐藏工作量区块；
- 重派失败 → notify error，不破坏页面其它状态。

## 5. 测试策略

- 构建期：`npm run build` TypeScript 编译零错误（新类型/信号/方法签名校验）；
- E2E：项目「统计」Tab 打开 → 评审面板渲染（数据/空态）→ 点重派 → 无控制台错误、
  无 js/css 404；既有 E2E 回归零失败。

## 6. 验收

1. 评审面板在项目统计 Tab 正常渲染（数据/空态两态）；
2. 重派按钮交互完整（busy 态、结果摘要、统计刷新、错误提示）；
3. 0 控制台错误；`npm run build` 通过；既有前端测试零回归。
