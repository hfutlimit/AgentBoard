"""Infrastructure layer: database, cache, mq, auth, object storage.

The actual implementations live here. The legacy top-level modules
(``agentboard.database``, ``agentboard.auth`` ...) are thin re-export
shims kept for backward compatibility with existing tests.
"""
from __future__ import annotations

# 重新导出旧 facade 公开符号,保证老 import 不破
from .database import (  # noqa: F401
    engine,
    SessionLocal,
    session_scope,
    get_session,
    init_db,
)
from .auth import (  # noqa: F401
    hash_password,
    verify_password,
    password_needs_rehash,
    make_token,
    parse_token,
    parse_token_details,
    get_user_by_id,
    validate_runtime_security,
    API_KEY_PREFIX,
    generate_api_key,
    hash_api_key,
    encode_permissions,
    decode_permissions,
    permission_allows,
)
from .cache import (  # noqa: F401
    get_cache,
    SimpleCache,
    CacheEntry,
    STATS_CACHE_TTL,
    API_CACHE_TTL,
)
from .cos_client import (  # noqa: F401
    CosClient,
    CosError,
    client as cos_client_singleton,
    ENV_KEYS as COS_ENV_KEYS,
)
