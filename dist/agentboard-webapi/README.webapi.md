# AgentBoard WebAPI 部署包

FastAPI/uvicorn 实现的 REST API，作为 Windows 服务（NSSM）跑在 `127.0.0.1:8000`，由 IIS 反向代理对外暴露为 `/api`。

## 部署步骤
1. 服务器安装 Python 3.13（勾选「Add to PATH」）与 MariaDB。
2. 建库建用户：
   ```sql
   CREATE DATABASE agentboard CHARACTER SET utf8mb4;
   CREATE USER 'agentboard'@'127.0.0.1' IDENTIFIED BY 'agentboard';
   GRANT ALL PRIVILEGES ON agentboard.* TO 'agentboard'@'127.0.0.1';
   FLUSH PRIVILEGES;
   ```
3. 复制 `env.webapi.example` 为 `.env`，填入 `AGENTBOARD_SECRET`、`AGENTBOARD_DB_URL`、`AGENTBOARD_CORS_ORIGINS`。
   - 首次创建管理员：临时设 `AGENTBOARD_ALLOW_REGISTRATION=1`，启动后用 Web 注册，再把该项改回 `0`。
4. 以**管理员** PowerShell 运行：
   ```powershell
   .\install-service.ps1
   ```
   该脚本会创建 `.venv`、安装依赖，并以 `AgentBoard-WebAPI` 服务自启。
5. 验证：`curl http://127.0.0.1:8000/api/meta`。

## 文件说明
- `run-webapi.ps1` — 启动入口（NSSM 调用），含自动建 venv。
- `install-service.ps1` — 安装为 Windows 服务。
- `make-mcp-token.py` — 为 MCP 生成长期 API Key（见 MCP 包说明）。
- `agentboard/`、`migrations/`、`alembic.ini`、`requirements.txt` — 应用源码与依赖。
