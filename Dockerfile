# AgentBoard — 多阶段/单镜像部署
# API(8000) 与 Web(8080) 复用同一镜像，仅在 docker-compose 中通过 command 区分。
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

# SQLite 持久化目录（compose 中以命名卷挂载到这里）
RUN mkdir -p /app/data

# 默认入口：REST API。Web 服务在 compose 中覆盖 command。
# 数据库地址默认指向卷内 SQLite；生产可改用 MariaDB（见 docker-compose.yml）。
ENV AGENTBOARD_DB_URL="sqlite:////app/data/agentboard.db"
EXPOSE 8000 8001 8080

CMD ["uvicorn", "agentboard.api:app", "--host", "0.0.0.0", "--port", "8000"]
