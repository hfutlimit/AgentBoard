# 实现清单：轻量 Jira 核心与 Agent 开发闭环

## 1. 优先级与评论
- [x] 增加 Task.priority、Comment 模型与 Alembic 迁移
- [x] 服务层实现校验、筛选和评论 CRUD
- [x] REST 与 `/api/meta` 扩展
- [x] MCP 工具扩展
- [x] Web 优先级徽章、编辑控件和评论时间线
- [x] 自动化测试覆盖

## 2. Sprint
- [ ] 模型、迁移、状态机与单 active Sprint 约束
- [ ] REST/MCP CRUD 与任务规划接口
- [ ] Backlog/Sprint Web 视图和测试

## 3. 附件
- [ ] 元数据模型与安全文件存储
- [ ] REST 上传/下载/删除及 MCP 资源工具
- [ ] Web 附件区和安全测试

## 4. 定时 Agent 开发
- [ ] AgentSchedule/AgentRun 模型与迁移
- [ ] 幂等调度、租约、重试和取消
- [ ] Agent 适配器契约与 Codex/WorkBuddy/Qoder 配置示例
- [ ] Web 计划/运行历史与 MCP 协作工具
