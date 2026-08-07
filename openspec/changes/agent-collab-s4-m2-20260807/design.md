# Design: 评审运营视图多数决投票进度提示（Epic 122 S4 M2）

## 1. 现状

- `service.get_review_stats()`（S3 M2）已返回项目级评审统计，但无多数决信息；
- `service` 已有 `get_review_mode()` / `get_review_quorum()`（S3 M3，env 驱动）与 `_review_vote_counts()`（approve/reject 计数）；
- 前端评审运营面板（S4 M1）已渲染统计卡/重派/工作量条，无投票进度。

## 2. 后端设计

`get_review_stats(s, *, project_id, days, user_id)` 返回结构纯增量：

```jsonc
{
  // ...既有字段不变
  "review_mode": "single" | "majority",     // get_review_mode()
  "review_quorum": 3,                        // get_review_quorum()
  "votes": [                                 // majority 下非空；single 恒 []
    {
      "kind": "story" | "task",
      "id": 123,
      "title": "...",
      "status": "pending_review" | "in_review",
      "approve": 2, "reject": 0, "cast": 2,  // _review_vote_counts 统计
      "quorum": 3
    }
  ]
}
```

- 仅遍历 pending 实体（`stories` 中 `status == "pending_review"`、`tasks` 中 `status == Status.IN_REVIEW`）；
- 结算后实体（ready/done/blocked）不出现（票已清）；
- single 模式直接返回空数组，零行为变化。

## 3. 前端设计

### models.ts

```ts
export interface ReviewVoteRow {
  kind: 'story' | 'task'; id: number; title: string; status: string;
  approve: number; reject: number; cast: number; quorum: number;
}
export interface ReviewStats {
  // ...既有字段
  review_mode?: 'single' | 'majority';
  review_quorum?: number;
  votes?: ReviewVoteRow[];   // 可选，向后兼容
}
```

### app.ts 纯函数

- `reviewModeLabel(mode)` → '多数决评审' / '单人评审'；
- `reviewVotePct({cast, quorum})` → min(100, round(cast/quorum*100))，quorum<=0 兜底 0；
- `reviewVoteReached({cast, quorum})` → cast >= quorum（达法定票可结算）。

### app.html

- 评审面板头部 `review-mode-badge`（single 灰 / majority 紫，majority 显示「· 法定 N 票」）；
- 统计卡之后、工作量条之前：`@if ((rs.review_mode === 'majority') && rs.votes?.length)` 渲染投票进度区块：
  - 每条：kind 徽章（Story/Task）+ 标题截断 + ✓ approve / ✗ reject / cast·quorum 票数 + 进度条；
  - `reached` 行绿色高亮 + 「已足额」。

### app.css

`.review-mode-badge` / `.review-vote-row` / `.review-vote-bar` 等（含暗色主题适配，复用 `--border`/`--bg-hover`/`--text` 变量）。

## 4. 测试设计

### 后端 `tests/test_epic122_s4m2.py`（8 用例）

1. review_mode 默认 single / env 覆盖 / 非法回退；
2. review_quorum 默认 3 / env / 超范围回退；
3. majority votes 结构（pending Story+Task 各一条、ready 不出现）；
4. majority votes 计数（approve/reject/cast 经真实写票）；
5. single 模式 votes 空数组；
6. API 透传（真实 uvicorn 子进程，空 votes 结构存在）；
7. API 透传（真实子进程 + pending + 2 票 → votes 含进度）。

### 前端 vitest（3 用例）

reviewModeLabel / reviewVotePct / reviewVoteReached 纯函数。

### E2E `tests/test_epic122_s4m2_e2e.py`

自起独立栈（uvicorn + web_app，AGENTBOARD_REVIEW_MODE=majority, quorum=3, 独立 SQLite, REQUIRE_AUTH=0）→ service 直连同库造 pending Story + 2 票 approve → Playwright 断言：

- `.review-mode-badge` 渲染「多数决评审 · 法定 3 票」且 majority 激活态；
- `#review-votes-block` 渲染 Story 标题 + ✓ 2 + ✗ 0 + 2/3 票 + 进度条 67%；
- 0 console / pageerror / js-css 404。
