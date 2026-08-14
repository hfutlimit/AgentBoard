"""[FACADE] agentboard.domains.work_items.models → agentboard.features.work_items.models"""
from ...features.work_items.models import *  # noqa: F401,F403
from ...features.work_items.models import (  # noqa: F401
    Task, TaskDependency, TaskStatusHistory, Comment, Attachment, AuditLog, WebhookConfig,
)
