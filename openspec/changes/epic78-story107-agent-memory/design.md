# Design — Agent 记忆自动加载（MCP 工具）

**status**: in_review

## 现状

- `Document.type ∈ {memory, plan, knowledge, design}`（`domains/documents/models.py` CK 约束）。
- MCP 已有 `list_documents` / `create_document` / `update_document` / `search_documents` 等 10+ 工具，
  全部走 `_http`（REST）模式。
- `_doc_list(project_id, type, status, q, limit, offset)` → `GET /api/documents`（纯列表）；
  `_doc_create(...)` → `POST /api/documents`；`_doc_update(did, fields)` → `PATCH /api/documents/{did}`。

## 决策

| 备选方案 | 优劣 | 决策 |
|---|---|---|
| 给 Document 加 `agent` 列 + 专用 REST 端点 | 侵入 DB 模型、需 Alembic 迁移 + 中间件 + 契约变更，1h 不可独立收尾 | ❌ |
| 仅复用现有 document MCP 工具，由 Agent 自行拼接 | 无「自动加载 / 幂等累积 / 按 agent 隔离」语义，仍健忘 | ❌ |
| **title 前缀约定 + 2 个专用 MCP 工具，复用 `/api/documents`** | 零 REST/DB 变更；语义明确；幂等累积天然成立 | ✅ |

### 关键设计点

1. **零契约变更**：不新增 REST 端点、不新增 DB 列；隔离靠 title 约定：
   - 项目级 `项目记忆`；Agent 级 `Agent 记忆 · {agent}`（前缀 `Agent 记忆 · ` 判定层级）。
2. **幂等累积**：`append_agent_memory` 先 `_doc_list(type=memory)` 精确匹配 title，
   命中 → `PATCH content = old + "\n\n" + new`；未命中 → `POST` 新建。同名文档永远只有一份。
3. **Agent 级隔离**：`get_project_memory(agent=X)` 过滤为
   `title == "项目记忆" or title == "Agent 记忆 · X"`，其他 Agent 专属记忆不可见；
   `agent=None` 返回全部（运维/人工视角）。
4. **会话自动加载语义**：返回 `combined` 单字段拼接文本（`[标题]\n内容` 分节），
   Agent 一次调用即可注入系统提示词，无需遍历 documents。
5. **Epic 97 护栏兼容**：新代码全部走 `_http`，无 `_api(` 调用；AST 护栏覆盖。

## 数据流

```
Agent 会话启动
  └─ get_project_memory(project_id, agent)
       └─ _doc_list(type=memory) ──→ GET /api/documents?project_id=&type=memory
       └─ 过滤（项目级 + 该 agent）→ {documents[], combined}
Agent 学到新约定
  └─ append_agent_memory(project_id, content, agent)
       └─ _doc_list(type=memory) 找 title 精确匹配
       ├─ 命中 → _doc_update(content=old+new)  （幂等累积）
       └─ 未命中 → _doc_create(type=memory)     （首次创建）
```

## 风险与缓解

- title 约定可能被人工文档撞名：概率低；`项目记忆` 语义明确，撞名即合并（可接受）。
- 大记忆文档 content 变长：memory 为 Text 列，无硬上限；combined 拼接为 O(n)，单项目记忆量级下可忽略。
- MCP 容器（18001）内存中仍是旧代码：本次修复仅自包含测试验证（与 Epic 97 同策略），
  容器重部署留独立运维窗口；`dist/` 发布产物同步由 `package_windows.py` 重建。
