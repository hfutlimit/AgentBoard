import ast
from pathlib import Path

from agentboard import models
from agentboard.domains.documents.models import Document
from agentboard.domains.identity.models import User
from agentboard.domains.projects.models import Project
from agentboard.domains.proposals.models import Proposal
from agentboard.domains.scheduling.models import AgentSchedule
from agentboard.domains.work_items.models import Task

# 每个领域必须注册到共享 metadata 的核心表。
# 这里断言「包含关系」而非硬编码总数——新增领域表不应让本用例失败，
# 但任何核心表从 facade 中掉出去必须立刻暴露。
EXPECTED_CORE_TABLES = {
    "users", "api_keys", "notifications",                      # identity
    "projects", "epics", "stories", "sprints", "project_members",  # projects
    "tasks", "comments", "attachments", "audit_logs",
    "task_dependencies", "webhook_configs",                    # work_items
    "agent_schedules", "agent_runs",                           # scheduling
    "documents", "document_comments",                          # documents
    "proposals", "proposal_rounds", "proposal_questions",      # proposals (Epic 96 P0)
}


def test_legacy_model_facade_exports_domain_models():
    assert models.User is User
    assert models.Project is Project
    assert models.Task is Task
    assert models.AgentSchedule is AgentSchedule
    assert models.Document is Document
    assert models.Proposal is Proposal


def test_all_domain_tables_registered_on_shared_metadata():
    registered = set(models.Base.metadata.tables)
    missing = EXPECTED_CORE_TABLES - registered
    assert not missing, f"以下核心表未注册到共享 metadata：{sorted(missing)}"


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
