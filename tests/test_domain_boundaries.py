import ast
from pathlib import Path

from agentboard import models
from agentboard.domains.identity.models import User
from agentboard.domains.projects.models import Project
from agentboard.domains.scheduling.models import AgentSchedule
from agentboard.domains.work_items.models import Task


def test_legacy_model_facade_exports_domain_models():
    assert models.User is User
    assert models.Project is Project
    assert models.Task is Task
    assert models.AgentSchedule is AgentSchedule
    assert len(models.Base.metadata.tables) == 13


def test_domains_do_not_depend_on_transport_or_entrypoints():
    forbidden = {"agentboard.api", "agentboard.mcp_server", "agentboard.scheduler"}
    for path in Path("agentboard/domains").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert imports.isdisjoint(forbidden), path
