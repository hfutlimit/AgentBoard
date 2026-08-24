"""AgentBoard — 轻量项目管理 + OpenSpec/Superpowers 风格规范能力。

层级：Project → Epic → Story → Task/Bug
Task 携带 description(md) 与 spec(md)，通过 MCP 暴露给 AI Agent。
存储：调试 SQLite，生产 MariaDB（AGENTBOARD_DB_URL 切换）。
"""
