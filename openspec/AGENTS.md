# AgentBoard — OpenSpec 指引（给 AI Agent）

本仓库使用 OpenSpec 规范驱动开发。规则：

- `openspec/specs/<capability>/spec.md` 是**当前行为的唯一事实来源**（已实现的功能）。
- 任何**尚未实现或要变更的**功能，先在本仓库 `openspec/changes/<change-id>/` 下创建变更提案：
  - `proposal.md`：为什么做、改什么、影响范围
  - `design.md`：技术方案
  - `tasks.md`：可勾选的实现清单（`- [ ]`）
- 实现时按 `tasks.md` 推进；完成后把变更归档（移入 `openspec/changes/archive/`）并同步更新 `specs/` 对应 capability。
- 变更 id 用小写短横线命名，例如 `mariadb-alembic`。

注意：AgentBoard 产品本身把任务的 OpenSpec/Superpowers 规范存放在**任务的 `spec` 字段**（数据库），而本 `openspec/` 目录只用于**管理 AgentBoard 项目自身的开发规格**，二者互不冲突。
