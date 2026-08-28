"""AgentBoard 轻量鉴权:密码哈希 + 无状态 Bearer Token。

设计取舍(MVP):
- 不引入额外依赖:密码用 pbkdf2_hmac(标准库),Token 用 HMAC 签名(标准库)。
- Token 为无状态:``{user_id}.{hmac}``,服务端用 AGENTBOARD_SECRET 校验,无需存储会话。
- 仅提供注册 / 登录 / 当前用户;不强制保护现有单用户 CRUD 接口(保持 MCP/Web 兼容)。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ``models`` 只在函数体里用(get_user_by_id 拿 User 类),避免顶部 import 触发循环。
# 类型 hint 用 TYPE_CHECKING 守门。
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ... import models  # noqa: F401  (仅供类型检查)

_SECRET = os.getenv("AGENTBOARD_SECRET", "dev-insecure-secret-change-me").encode()
_LEGACY_PBKDF2_ROUNDS = 100_000
_PBKDF2_ROUNDS = 600_000
_TOKEN_TTL_SECONDS = int(os.getenv("AGENTBOARD_TOKEN_TTL_SECONDS", "172800"))

# B-A5 / Story 291 / Epic 145：dev 模式下视为「可接受的不安全默认值」,
# 仅记录 WARNING 日志提升可见性,不阻断本地开发（向后兼容）。
# production 模式下这些项必须显式收紧,否则 validate_runtime_security() fail-fast。
# 字段：(env var, 代码层默认值, 不安全取值集合, 描述)
# 默认值必须与 api_helpers.py / features/auth/router.py / core/config.py 的 os.getenv 兜底一致。
_DEV_INSECURE_DEFAULTS = (
    ("AGENTBOARD_REQUIRE_AUTH", "0", {"0", "no", "false", ""}, "anonymous CRUD (REQUIRE_AUTH=0)"),
    ("AGENTBOARD_ALLOW_REGISTRATION", "1", {"1", "true", "yes"}, "open registration (ALLOW_REGISTRATION=1)"),
    ("AGENTBOARD_CORS_ORIGINS", "*", {"*"}, "wildcard CORS (CORS_ORIGINS=*)"),
)


def _has_wildcard_cors() -> bool:
    origins = os.getenv("AGENTBOARD_CORS_ORIGINS", "*")
    return "*" in {x.strip() for x in origins.split(",")}


def validate_runtime_security() -> None:
    """生产环境拒绝明显不安全的默认值;本地开发保持零配置可运行。

    行为分层（B-A5 / Story 291 / Epic 145 强化）::

        development / staging  → 不 raise;若检测到不安全默认值,记录 WARNING 日志
                                  提升可见性（向后兼容,不阻断本地开发）。
        production             → fail-fast:弱 SECRET / REQUIRE_AUTH=0 / CORS=* 直接 raise;
                                  ALLOW_REGISTRATION=1 记录 WARNING（维护窗口非阻塞,
                                  避免阻断 README 文档化的临时注册流程）。
    """
    env = os.getenv("AGENTBOARD_ENV", "development").lower()

    # ---- dev / staging:仅记录 WARNING,不阻断 ----
    if env != "production":
        insecure_flags = []
        if _SECRET == b"dev-insecure-secret-change-me":
            insecure_flags.append("AGENTBOARD_SECRET is the default dev secret")
        for var, default_val, bad_values, desc in _DEV_INSECURE_DEFAULTS:
            actual = os.getenv(var, default_val)
            if actual.lower() in bad_values:
                insecure_flags.append(f"{var}={actual} → {desc}")
        if insecure_flags:
            logger.warning(
                "AgentBoard running in %s mode with insecure defaults "
                "(acceptable for local dev only, DO NOT use in production): %s",
                env, " | ".join(insecure_flags),
            )
        return

    # ---- production:fail-fast ----
    if _SECRET == b"dev-insecure-secret-change-me" or len(_SECRET) < 32:
        raise RuntimeError("production requires AGENTBOARD_SECRET with at least 32 bytes")
    if os.getenv("AGENTBOARD_REQUIRE_AUTH", "0").lower() not in {"1", "true", "yes"}:
        raise RuntimeError("production requires AGENTBOARD_REQUIRE_AUTH=1")
    if _has_wildcard_cors():
        raise RuntimeError("production requires an explicit AGENTBOARD_CORS_ORIGINS allowlist")
    # ALLOW_REGISTRATION=1 在 production 不 raise（维护窗口需临时注册新 Agent 账号,
    # 详见 README「生产环境部署前必读」）；仅记录 WARNING 提醒事后恢复为 0。
    if os.getenv("AGENTBOARD_ALLOW_REGISTRATION", "1").lower() in {"1", "true", "yes"}:
        logger.warning(
            "AGENTBOARD_ALLOW_REGISTRATION=1 in production — this is a temporary "
            "maintenance window; set it back to 0 immediately after user creation. "
            "Leaving it open lets anyone register accounts."
        )


def validate_mcp_runtime_security() -> None:
    """Fail closed at the independent MCP process boundary in production.

    The REST API and MCP transport run as separate processes/containers. API
    startup must not depend on MCP-only configuration, while the MCP process
    itself must never expose write-capable tools anonymously in production.
    """
    env = os.getenv("AGENTBOARD_ENV", "development").lower()
    require_auth = os.getenv("AGENTBOARD_MCP_REQUIRE_AUTH", "0").lower() in {
        "1", "true", "yes",
    }
    if env != "production":
        if not require_auth:
            logger.warning(
                "AgentBoard MCP transport is unauthenticated in %s mode "
                "(acceptable for local stdio/dev only, DO NOT expose it to a network)",
                env,
            )
        return

    if _SECRET == b"dev-insecure-secret-change-me" or len(_SECRET) < 32:
        raise RuntimeError(
            "production MCP requires AGENTBOARD_SECRET with at least 32 bytes"
        )
    if not require_auth:
        raise RuntimeError(
            "production requires AGENTBOARD_MCP_REQUIRE_AUTH=1 "
            "(the MCP HTTP transport exposes write-capable tools)"
        )


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
    """签发带过期时间的 HMAC Token:``v1.<uid>.<exp>.<signature>``。"""
    ttl = _TOKEN_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    if ttl <= 0:
        raise ValueError("token ttl must be positive")
    expires_at = int(time.time()) + ttl
    payload = f"v1.{user_id}.{expires_at}"
    sig = hmac.new(_SECRET, payload.encode("utf-8"), "sha256").hexdigest()
    return f"{payload}.{sig}"


def parse_token_details(token: str | None) -> tuple[int, int] | None:
    """校验 Token 并返回 ``(user_id, expires_at)``;非法或过期返回 None。"""
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
    """校验 Token 并返回 user_id;非法/篡改返回 None。"""
    details = parse_token_details(token)
    return details[0] if details else None


def get_user_by_id(s: Session, user_id: int) -> "models.User | None":
    from ... import models  # 延迟 import,避免循环
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


def permission_allows(permissions: list[str], required: str) -> bool:
    """Return whether an API-key permission set grants a namespaced capability."""
    if required in permissions:
        return True
    namespace = required.split(":", 1)[0]
    return f"{namespace}:*" in permissions
