# AgentBoard

轻量项目管理工具，内嵌 **OpenSpec / Superpowers 风格的规范能力**：任务的 `spec` 字段存放 markdown 规范文档，并通过 **MCP** 暴露给 AI 编程工具。

## 功能

- 层级结构：`Project → Epic → Story → Task/Bug`（Task 为最底层，不嵌套）
- Task 携带 `description`(markdown) 与 `spec`(markdown)
- MCP 服务：项目树 CRUD、spec 读写、关键字搜索、状态流转、生成变更提案
- 简易 Web UI（FastAPI 服务端渲染，markdown 渲染）
- 双存储：调试用 SQLite，生产用 MariaDB（通过 `AGENTBOARD_DB_URL` 切换，代码不感知具体库）

## 目录结构

```
agentboard/
  models.py       # SQLAlchemy 模型（Project/Epic/Story/Task）
  database.py     # 引擎工厂（SQLite/MariaDB 切换）+ session
  service.py      # 业务服务层（CRUD / spec / 搜索 / 状态机）
  mcp_server.py   # FastMCP 工具集
  api.py          # FastAPI Web 页面 + 表单
  web/templates/  # Jinja2 模板
tests/test_smoke.py
docs/requirements.md   # 需求分析
docs/tasks.md          # 任务列表（Epic/Story/Task）
```

## 运行

```bash
pip install -r requirements.txt

# Web UI（默认 SQLite）
uvicorn agentboard.api:app --reload
# 浏览器打开 http://127.0.0.1:8000

# MCP 服务（stdio）
python -m agentboard.mcp_server
```

生产切换到 MariaDB：设置环境变量 `AGENTBOARD_DB_URL=mysql+pymysql://user:pass@host:3306/agentboard`

## 测试（smoke test）

```bash
PYTHONPATH=. python tests/test_smoke.py
```

## 需求与任务

见 `docs/requirements.md` 与 `docs/tasks.md`。开发遵循 Superpowers / OpenSpec 规范驱动方式。
