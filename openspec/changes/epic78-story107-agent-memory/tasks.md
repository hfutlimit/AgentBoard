# Tasks — Agent 记忆自动加载（get_project_memory / append_agent_memory MCP 工具）

**status**: in_review

## Task 1 — MCP 工具 `get_project_memory`

- [x] `agentboard/mcp_server.py` 新增 `get_project_memory(project_id, agent=None)`：
  `_doc_list(project_id, type="memory")` → agent 给定时过滤（项目级 + 该 Agent 级）→
  返回 `{project_id, agent, documents[], combined}`（combined 按 `[标题]\n内容` 拼接）
- [x] 未加载到列表时安全降级（返回空 documents / 空 combined）

## Task 2 — MCP 工具 `append_agent_memory`

- [x] 目标 title 约定：`项目记忆`（agent=None）/ `Agent 记忆 · {agent}`（agent 给定）
- [x] 幂等累积：`_doc_list(type="memory")` 精确匹配 title → 命中 `_doc_update` 追加
  （`old + "\n\n" + new`）、未命中 `_doc_create(type="memory")`
- [x] 返回 `{document_id, title, appended, content_length}`

## Task 3 — 测试（`tests/test_epic78_story107_memory_mcp.py`，自包含）

- [x] 工具注册（`asyncio.run(mcp.list_tools())` 含两工具）
- [x] 首次 append 创建 / 二次 append 同一 title 幂等累积（同一 document_id，content 含两段）
- [x] get combined 含累积内容与 `[项目记忆]` 标题标注
- [x] Agent 级隔离：agent-a 取不到 agent-b 专属记忆；三者共享项目级
- [x] AST 静态护栏无未定义调用（含 builtins）
- [x] **5 passed**（真实 uvicorn 子进程 + 工具 `.fn` 直调）

## Task 4 — E2E（`tests/test_epic78_story107_memory_mcp_e2e.py`）

- [x] MCP 工具写入 memory 文档 → Web 项目文档 Tab 可见标题，0 控制台 / 0 JS / 0 404
- [x] **1 passed**

## Task 5 — 回归与验收

- [x] 聚焦回归（Story 104/105 等相邻模块）无新增失败
- [x] 零 REST / DB 契约变更（纯 MCP 客户端增量）
- [x] 未触碰端口 18001 / docker
