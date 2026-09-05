"""Project-scoped ownership switch: Durable and legacy must never both dispatch.

Keep this allow-list enabled until every durable run in a project is drained.
Malformed configuration raises instead of silently enabling legacy dispatch.
"""
import os


def durable_project_enabled(project_id: int) -> bool:
    values = os.getenv("AGENTBOARD_DURABLE_PROJECT_IDS", "").split(",")
    return project_id in {int(value.strip()) for value in values if value.strip()}


def require_legacy_task(s, task) -> None:
    from ...core.exceptions import InvalidValue
    if durable_project_enabled(task.project_id):
        raise InvalidValue("task is managed by durable workflow; legacy assignment is disabled")
