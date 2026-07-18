"""为 MCP 服务生成一枚长期有效的 API Key（无过期），输出明文 abk_ 令牌。

前置：
  1. 已按 env.webapi.example 创建 .env（含 AGENTBOARD_DB_URL / AGENTBOARD_SECRET）。
  2. 已通过 Web 注册并提升了一名管理员账号（AGENTBOARD_ALLOW_REGISTRATION=1 时注册，
     再在 Web 里把该用户设为管理员；或临时放开后设置）。
  3. 已创建 .venv（运行过 run-webapi.ps1 一次即可）。

用法（在 webapi 包目录内）：
  .venv\Scripts\python.exe make-mcp-token.py
输出形如：
  MCP_API_KEY=abk_xxxx
把等号右侧的值填入 mcp 包的 .env 的 AGENTBOARD_MCP_TOKEN 字段。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_dotenv(path):
    """极简 .env 解析，仅用于本地脚本；不覆盖已有环境变量。"""
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


load_dotenv(os.path.join(HERE, ".env"))
sys.path.insert(0, HERE)

from agentboard.database import SessionLocal  # noqa: E402
from agentboard import service  # noqa: E402
from sqlalchemy import text  # noqa: E402


def main():
    with SessionLocal() as s:
        row = s.execute(text("SELECT id FROM users WHERE is_admin=1 LIMIT 1")).fetchone()
        if not row:
            print("ERROR: 未找到管理员用户。请先通过 Web 注册账号并将其设为管理员，然后重试。")
            print("提示：可在 webapi 的 .env 中临时设 AGENTBOARD_ALLOW_REGISTRATION=1 以便注册。")
            sys.exit(2)
        user_id = row[0]
        item, plaintext = service.create_api_key(
            s, user_id=user_id, name="mcp-service", permissions=["api:*"]
        )
        s.commit()
        print("MCP_API_KEY=" + plaintext)
        print("=> 请将上面 abk_ 开头的值填入 mcp 包 .env 的 AGENTBOARD_MCP_TOKEN。")


if __name__ == "__main__":
    main()
