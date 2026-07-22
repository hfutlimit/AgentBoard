# Proposal: 任务列表批量指派 (v3.0 / Epic 42)

## 问题
任务列表批量操作面板（bulk-action-bar）已完成「批量修改状态 / 批量修改优先级 / 批量删除」
三件套（v2.9 补齐优先级），但**「批量指派」缺失**。单任务可在详情/行内指派指派人，
批量场景下却只能逐个操作，多任务统一分派给同一成员时操作成本高，与 Jira 体验不符。

后端 `bulk_update_tasks` 仅支持 `status / priority / sprint_id`，`BulkTaskUpdate`
模型缺少 `assignee_id`；`service.update_task` 早已支持 `assignee_id` 字段更新与通知，
因此只需在批量入口补齐该字段即可，无需改动核心模型契约。

## 目标
在批量操作面板「批量修改优先级」之后新增「批量指派」按钮，点击展开指派人下拉：
- 下拉内容为当前项目成员（`members()`）+「未指派」选项
- 选中成员后「应用」→ 调用 `bulkUpdateTasks(ids, { assignee_id })` 批量指派
- 选「未指派」→ 调用 `bulkUpdateTasks(ids, { clear_assignee: true })` 批量取消指派
- 完成后 `notify` 提示成功/失败条数，清除选择并刷新列表
- 复用现有 `bulkProgress` 进度与错误反馈通道

## 非目标
- 不改动 `User / Task` 等核心模型字段（仅为 `BulkTaskUpdate` 增加可选增量字段，向后兼容）
- 不做批量指派时的跨项目校验（沿用单任务 `assignee_id` 既有校验：成员须存在）
- 不做指派人头像/姓名以外的富展示

## 风险
低。后端为纯增量可选字段（`assignee_id: int | None = None` + `clear_assignee: bool = False`），
不影响既有 `status/priority/sprint_id` 调用；前端镜像已有的 `bulkUpdatePriority` 实现路径，
仅替换调用参数与 UI 面板。
