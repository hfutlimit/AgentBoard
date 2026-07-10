"""AgentBoard 轻量鉴权：密码哈希 + 无状态 Bearer Token。

设计取舍（MVP）：
- 不引入额外依赖：密码用 pbkdf2_hmac（标准库），Token 用 HMAC 签名（标准库）。
- Token 为无状态：`{user_id}.{hmac}`，服务端用 AGENTBOARD_SECRET 校验，无需存储会话。
- 仅提供注册 / 登录 / 当前用户；不强制保护现有单用户 CRUD 接口（保持 MCP/Web 兼容）。
"""
import hashlib
import hmac
import os
import secrets
from sqlalchemy.orm import Session

from . import models

_SECRET = os.getenv("AGENTBOARD_SECRET", "dev-insecure-secret-change-me").encode()
_PBKDF2_ROUNDS = 100_000


def hash_password(password: str) -> str:
    """返回 `pbkdf2_sha256$<salt_hex>$<hash_hex>`。"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, expected = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ROUNDS)
    return hmac.compare_digest(dk.hex(), expected)


def make_token(user_id: int) -> str:
    sig = hmac.new(_SECRET, str(user_id).encode("utf-8"), "sha256").hexdigest()
    return f"{user_id}.{sig}"


def parse_token(token: str | None) -> int | None:
    """校验 Token 并返回 user_id；非法/篡改返回 None。"""
    if not token:
        return None
    try:
        uid_s, sig = token.split(".", 1)
    except ValueError:
        return None
    expect = hmac.new(_SECRET, uid_s.encode("utf-8"), "sha256").hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        return int(uid_s)
    except ValueError:
        return None


def get_user_by_id(s: Session, user_id: int) -> models.User | None:
    return s.get(models.User, user_id)
