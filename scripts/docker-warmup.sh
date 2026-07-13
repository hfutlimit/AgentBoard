#!/bin/bash
# AgentBoard Docker 镜像预热脚本
# 用途：预先拉取所有 Docker Compose 服务所需的镜像，避免首次启动时下载延迟
# 使用方法：./scripts/docker-warmup.sh

set -e

echo "=========================================="
echo "AgentBoard 镜像预热"
echo "=========================================="

# 定义需要的镜像列表
IMAGES=(
    "python:3.13-slim"
    "mariadb:11"
)

# 从 docker-compose.yml 提取所有 build 镜像
echo "正在分析 docker-compose.yml..."

# 预热基础镜像
echo ""
echo "步骤 1/2: 预热基础镜像..."
for img in "${IMAGES[@]}"; do
    echo "  拉取 $img..."
    docker pull "$img" || echo "  警告: 无法拉取 $img"
done

# 预热服务镜像（如果已构建）
echo ""
echo "步骤 2/2: 检查已构建的镜像..."
for svc in api web mcp; do
    img=$(docker-compose images -q "$svc" 2>/dev/null || echo "")
    if [ -n "$img" ]; then
        echo "  $svc: 镜像已存在"
    else
        echo "  $svc: 需要构建（首次运行 docker compose up -d --build）"
    fi
done

echo ""
echo "=========================================="
echo "预热完成！"
echo "=========================================="
echo ""
echo "启动命令："
echo "  开发模式：docker compose up -d"
echo "  完整构建：docker compose up -d --build"
echo ""
