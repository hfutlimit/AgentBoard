"""Compatibility facade for the messaging infrastructure.

New implementation code should import from
``agentboard.core.infrastructure.messaging``.  This module remains for older
workers, tests, and integrations.
"""

from .core.infrastructure.messaging.rabbitmq import *

