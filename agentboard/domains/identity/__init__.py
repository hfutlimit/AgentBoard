"""[FACADE] agentboard.domains.identity → agentboard.features.identity"""
from ...features.identity import models  # noqa: F401
from ...features.identity.models import (  # noqa: F401
    User, ApiKey, Notification,
)
