"""Compatibility facade for feature-owned API schemas.

New code should import request models from the feature that owns them.  This
module remains for existing integrations and older MCP clients.
"""

from .core.api.schemas import CommentIn, StatusIn
from .features.documents.schemas import *
from .features.identity.schemas import *
from .features.notifications.schemas import *
from .features.projects.schemas import *
from .features.proposals.schemas import *
from .features.scheduling.schemas import *
from .features.webhooks.schemas import *
from .features.work_items.schemas import *
