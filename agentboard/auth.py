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
import time
import json
from sqlalchemy.orm import Session

from . import models

_SECRET = os.getenv("AGENTBOARD_SECRET", "dev-insecure-secret-change-me").encode()
_LEGACY_PBKDF2_ROUNDS = 100_000
_PBKDF2_ROUNDS = 600_000
_TOKEN_TTL_SECONDS = int(os.getenv("AGENTBOARD_TOKEN_TTL_SECONDS", "172800"))


def validate_runtime_security() -> None:
    """生产环境拒绝明显不安全的默认值；本地开发保持零配置可运行。"""
    if os.getenv("AGENTBOARD_ENV", "development").lower() != "production":
        return
    if _SECRET == b"dev-insecure-secret-change-me" or len(_SECRET) < 32:
        raise RuntimeError("production requires AGENTBOARD_SECRET with at least 32 bytes")
    if os.getenv("AGENTBOARD_REQUIRE_AUTH", "0").lower() not in {"1", "true", "yes"}:
        raise RuntimeError("production requires AGENTBOARD_REQUIRE_AUTH=1")
    origins = os.getenv("AGENTBOARD_CORS_ORIGINS", "*")
    if "*" in {x.strip() for x in origins.split(",")}:
        raise RuntimeError("production requires an explicit AGENTBOARD_CORS_ORIGINS allowlist")


def hash_password(password: str) -> str:
    """返回包含算法和迭代次数的可升级密码哈希。"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
        if len(parts) == 3:  # 兼容早期 `algorithm$salt$hash` 格式
            algo, salt, expected = parts
            rounds = _LEGACY_PBKDF2_ROUNDS
        elif len(parts) == 4:
            algo, rounds_s, salt, expected = parts
            rounds = int(rounds_s)
        else:
            return False
        if rounds <= 0 or rounds > 10_000_000:
            return False
        salt_bytes = bytes.fromhex(salt)
    except (ValueError, TypeError):
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, rounds)
    return hmac.compare_digest(dk.hex(), expected)


def password_needs_rehash(stored: str) -> bool:
    parts = stored.split("$")
    return len(parts) != 4 or parts[1] != str(_PBKDF2_ROUNDS)


def make_token(user_id: int, *, ttl_seconds: int | None = None) -> str:
    """签发带过期时间的 HMAC Token：`v1.<uid>.<exp>.<signature>`。"""
    ttl = _TOKEN_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    if ttl <= 0:
        raise ValueError("token ttl must be positive")
    expires_at = int(time.time()) + ttl
    payload = f"v1.{user_id}.{expires_at}"
    sig = hmac.new(_SECRET, payload.encode("utf-8"), "sha256").hexdigest()
    return f"{payload}.{sig}"


def parse_token_details(token: str | None) -> tuple[int, int] | None:
    """校验 Token 并返回 `(user_id, expires_at)`；非法或过期返回 None。"""
    if not token:
        return None
    try:
        version, uid_s, exp_s, sig = token.split(".", 3)
        expires_at = int(exp_s)
        uid = int(uid_s)
    except (ValueError, TypeError):
        return None
    if version != "v1" or expires_at <= int(time.time()):
        return None
    payload = f"{version}.{uid_s}.{exp_s}"
    expect = hmac.new(_SECRET, payload.encode("utf-8"), "sha256").hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    return uid, expires_at


def parse_token(token: str | None) -> int | None:
    """校验 Token 并返回 user_id；非法/篡改返回 None。"""
    details = parse_token_details(token)
    return details[0] if details else None


def get_user_by_id(s: Session, user_id: int) -> models.User | None:
    return s.get(models.User, user_id)


API_KEY_PREFIX = "abk_"


def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext, display prefix, digest). Plaintext is shown once."""
    plaintext = API_KEY_PREFIX + secrets.token_urlsafe(32)
    display_prefix = plaintext[:12]
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, display_prefix, digest


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def encode_permissions(permissions: list[str]) -> str:
    return json.dumps(sorted(set(permissions)), separators=(",", ":"))


def decode_permissions(value: str) -> list[str]:
    try:
        decoded = json.loads(value)
        return decoded if isinstance(decoded, list) else []
    except (TypeError, json.JSONDecodeError):
        return []
