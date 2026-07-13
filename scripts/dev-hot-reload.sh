#!/bin/bash
# AgentBoard 开发模式启动脚本（带热重载）
# 用途：启动 API 服务并监听代码变化自动重载
# 使用方法：./scripts/dev-hot-reload.sh

set -e

echo "=========================================="
echo "AgentBoard 开发模式（热重载）"
echo "=========================================="
echo ""
echo "提示：前端静态文件通过 volume mount 自动同步"
echo "      修改前端代码后，刷新浏览器即可看到变化"
echo ""

# 导出开发环境变量
export AGENTBOARD_DB_URL="sqlite:////app/data/agentboard.db"
export AGENTBOARD_RATE_LIMIT_ENABLED="0"

# 启动 API 服务（带热重载）
echo "启动 API 服务（热重载模式）..."
docker compose run --rm \
  -p 8000:8000 \
  -v "$(pwd)/agentboard:/app/agentboard" \
  -v "$(pwd)/migrations:/app/migrations" \
  -v "$(pwd)/data:/app/data" \
  api \
  uvicorn agentboard.api:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app
