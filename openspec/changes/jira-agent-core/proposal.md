# 变更提案：轻量 Jira 核心与 Agent 开发闭环

## 背景
AgentBoard 已具备项目树、Task/Bug、状态、description/spec、REST、Web 与 MCP，但缺少日常项目管理不可或缺的优先级、评论、附件和 Sprint，也没有把“按时让开发 Agent 执行任务”建模为可审计流程。

## 目标
- 保留轻量部署体验，同时补齐简易 Jira 的核心元素。
- 让人类和 Codex、WorkBuddy、Qoder 等开发 Agent 共享同一任务事实源。
- 支持用户为任务配置一次或周期计划，并安全、幂等地记录 Agent 执行结果。
- Web 保持紧凑、现代、有层次的 Jira 风格，而非仅提供朴素 CRUD 表单。

## 范围
本变更包含优先级、评论、附件、Sprint、AgentSchedule/AgentRun，以及相应的 REST、MCP、Web 与测试。首个交付切片为“优先级 + 评论”；其他能力按 `tasks.md` 后续交付。

## 非目标
- 本阶段不实现企业级 RBAC、多租户、通知中心、复杂报表或任意脚本执行平台。
- 调度器不直接信任任务文本并拼接 shell 命令；执行必须通过受控 Agent 适配器。

## 成功标准
- 人和 Agent 都能在一个任务上读取 description/spec、设置状态与优先级、追加评论。
- Sprint、附件与定时执行均有明确数据契约、状态约束和验收任务。
- 所有新增能力具备数据库迁移和自动化回归测试；现有客户端保持兼容。
