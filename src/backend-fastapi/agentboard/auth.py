"""[FACADE] 旧 import 路径:``from agentboard.auth import ...``。

实际实现已迁至 ``agentboard.core.infrastructure.auth``,本文件仅为
向后兼容保留的薄壳 re-export。新代码请直接 import core.infrastructure.auth。
"""
from .core.infrastructure.auth import (  # noqa: F401
    hash_password,
    verify_password,
    password_needs_rehash,
    make_token,
    parse_token,
    parse_token_details,
    get_user_by_id,
    validate_runtime_security,
    validate_mcp_runtime_security,
    API_KEY_PREFIX,
    generate_api_key,
    hash_api_key,
    encode_permissions,
    decode_permissions,
    permission_allows,
)

__all__ = [
    "hash_password",
    "verify_password",
    "password_needs_rehash",
    "make_token",
    "parse_token",
    "parse_token_details",
    "get_user_by_id",
    "validate_runtime_security",
    "validate_mcp_runtime_security",
    "API_KEY_PREFIX",
    "generate_api_key",
    "hash_api_key",
    "encode_permissions",
    "decode_permissions",
    "permission_allows",
]
