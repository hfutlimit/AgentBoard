"""为 MCP 服务生成 API Key，并关联到指定用户（权限与用户一致）。

前置：
  1. 已按 env.webapi.example 创建 .env（含 AGENTBOARD_DB_URL / AGENTBOARD_SECRET）。
  2. 已创建 .venv（运行过 run-webapi.ps1 一次即可）。

用法（在脚本目录内）：
  # 默认：自动创建非管理员 mcp-service 用户并生成 key
  .venv\Scripts\python.exe make-mcp-token.py

  # 为指定已有用户生成 key（权限与该用户一致）
  .venv\Scripts\python.exe make-mcp-token.py --user jason

输出形如：
  MCP_API_KEY=abk_xxxx
把等号右侧的值填入 mcp 包的 .env 的 AGENTBOARD_MCP_TOKEN 字段。

**安全设计**：
- API Key 关联的用户即为 MCP 的身份；权限完全等同于该用户。
- 默认的 ``mcp-service`` 用户是**非管理员**，只能访问自己被邀请（成员）的
  项目。如果 MCP 需要对特定项目执行管理操作，请先将 mcp-service 添加为该
  项目成员，或使用 ``--user`` 指定已有用户。
- 不再默认为管理员创建 key，杜绝 MCP 越权浏览全量项目。
"""
import argparse
import os
import secrets
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


def _find_user(s, username):
    row = s.execute(text("SELECT id, is_admin FROM users WHERE username=:u"), {"u": username}).fetchone()
    if not row:
        return None
    return row[0], bool(row[1])


def _create_mcp_user(s):
    """创建非管理员 mcp-service 用户并返回 user_id。"""
    import hashlib
    import base64

    username = "mcp-service"
    # 用随机密码 + username + secret 生成确定性密码
    seed = f"mcp-{username}-{secrets.token_hex(8)}"
    password = base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest())[:16].decode()

    from agentboard import auth
    from agentboard.domains.identity.models import User

    user = User(
        username=username,
        password_hash=auth.hash_password(password),
        is_admin=False,
    )
    s.add(user)
    s.flush()
    return user.id


def main():
    parser = argparse.ArgumentParser(
        description="为 MCP 服务生成 API Key",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--user",
        metavar="USERNAME",
        help="关联的用户名（默认：自动创建非管理员 mcp-service 用户）",
    )
    parser.add_argument(
        "--name",
        default="mcp-service",
        help="API Key 名称（默认: mcp-service）",
    )
    parser.add_argument(
        "--permissions",
        default="api:*",
        help="API Key 权限（默认: api:*）",
    )
    args = parser.parse_args()
    permissions = [p.strip() for p in args.permissions.split(",") if p.strip()]

    with SessionLocal() as s:
        if args.user:
            uid = _find_user(s, args.user)
            if not uid:
                print(f"ERROR: 用户 '{args.user}' 不存在。请先注册该账号，然后重试。")
                sys.exit(2)
            user_id, is_admin = uid
            if is_admin:
                print(
                    f"WARNING: '{args.user}' 是管理员。MCP 将拥有管理员权限，"
                    f"可访问全部项目。如需限制，请使用普通用户。"
                )
        else:
            uid = _find_user(s, "mcp-service")
            if uid:
                user_id = uid[0]
                print("[INFO] 使用已有的 mcp-service 用户")
            else:
                user_id = _create_mcp_user(s)
                s.commit()
                print("[INFO] 已创建非管理员 mcp-service 用户")

        item, plaintext = service.create_api_key(
            s, user_id=user_id, name=args.name, permissions=permissions,
        )
        s.commit()

        print("MCP_API_KEY=" + plaintext)
        print("=> 请将上面 abk_ 开头的值填入 MCP 服务的 AGENTBOARD_MCP_TOKEN 环境变量。")
        print("=> MCP 的权限与该用户完全一致（非管理员用户仅见自己被邀请的项目）。")


if __name__ == "__main__":
    main()
