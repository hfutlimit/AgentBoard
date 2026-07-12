# AgentBoard — Angular 构建 + Python 运行时
FROM node:22.22-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim

# 非交互、无缓冲、不写 .pyc，便于容器日志与层缓存
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖（利用层缓存：requirements 不变时不会重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷源码
COPY . .
COPY --from=frontend-build /frontend/dist/frontend/browser /app/frontend/dist/frontend/browser

# SQLite 持久化目录（compose 中以命名卷挂载到这里）
RUN mkdir -p /app/data

# 默认入口：REST API。Web 服务在 compose 中覆盖 command。
# 数据库地址默认指向卷内 SQLite；生产可改用 MariaDB（见 docker-compose.yml）。
ENV AGENTBOARD_DB_URL="sqlite:////app/data/agentboard.db"
EXPOSE 8000 8001 8080

CMD ["uvicorn", "agentboard.api:app", "--host", "0.0.0.0", "--port", "8000"]
