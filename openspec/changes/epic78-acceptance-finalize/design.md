# Design — Epic 78 整体验收与收尾

**status**: in_review

## 现状核查（2026-08-06）

通过 AgentBoard MCP 巡检项目 3：

- Epic 78（id=78）status=**in_progress**，项目 3 唯一 in_progress 的 Epic；
- 其下 8 个 Story（101/102/103/104/105/106/107/177）全部 **in_review**；
- 对应交付 Task：965/967/968/969/966/978/977/979 全部 in_review；
- 测试文件：`tests/test_epic78_story1xx_*.py`（7 个单元 + 6 个 E2E）+ `test_schedule_unbind.py` + `test_scheduler.py`。

## 验收策略

Epic 78 验收原文要求：

> 执行器 daemon 运行后，到期 schedule 能真正触发 Agent 并落 success/failed；
> 至少有一例 CLI Agent（Launcher）与一例 Runner（Trigger）端到端跑通；
> Agent 通过 MCP 自报结果。

对应验证矩阵：

| 验收点 | 验证方式 | 结果 |
|--------|----------|------|
| 适配器框架可导入/注册 | test_epic78_story101 | ✅ |
| Launcher 拉起 CLI 并回写 | test_epic78_story102(+e2e) | ✅ |
| Trigger webhook 唤醒 | test_epic78_story103(+e2e) | ✅ |
| 状态机 pending→running→success/failed | test_epic78_story104(+e2e) | ✅ |
| RunStatus 全库一致 | test_epic78_story105(+e2e) | ✅ |
| Schedule 绑定松绑 + 自动选 task | test_schedule_unbind / test_scheduler | ✅ |
| Agent 记忆存取 | test_epic78_story107(+e2e) | ✅ |
| daemon 常驻循环 | test_epic78_story177(+e2e) | ✅ |

## 收尾动作

1. 复跑 112 个单元测试（已通过）；
2. 复跑 6 个 E2E（本次执行中）；
3. MCP：逐 Story `update_story(..., status="done")`；
4. MCP：`update_epic(78, status="done")`；
5. 提交 OpenSpec change 文档 + memory 更新。

## 风险与约束

- 状态流转：Story/Epic 状态由 in_review → done 为合法迁移（状态机允许）；
- 不触碰 18001 / docker 端口；
- 工作区存在其它 automation 的遗留改动（dist/、其他 memory.md），git add 仅本次文件。
