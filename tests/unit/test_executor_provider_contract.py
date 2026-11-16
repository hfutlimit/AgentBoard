"""Server-side half of the WorkerOwned provider contract.

The .NET worker registers an AgentInstance whose ``executor_type`` is its local
provider id, and ``upsert_agent_instance`` validates that value against
``EXECUTOR_TYPES``. The .NET startup tests stub every HTTP call with 200, so
they cannot prove the Server accepts a provider — this file does.

``_SUPPORTED_PROVIDERS`` (the legacy Durable ``/api/durable/agent-select``
roles fallback) must stay in sync with ``EXECUTOR_TYPES``: Python is now the
single source of truth for provider resolution, because the .NET
``DatabaseAgentSelector`` is a thin HTTP transport.

Isolation: every ``agentboard`` import is deferred into a fixture or test, and
the DB is a private in-memory engine. Importing ``agentboard`` at module scope
binds the global engine before sibling modules assign their own
``AGENTBOARD_DB_URL`` during collection, which breaks their alembic setup; this
file must not join that chain.
"""
import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

CLI_TOOLS = ("codex", "workbuddy", "cursor", "qoder")


@pytest.fixture(scope="module")
def engine():
    from agentboard.core.common.models import Base
    from agentboard.features.identity import models as _id_models  # noqa: F401
    from agentboard.features.projects import models as _proj_models  # noqa: F401
    from agentboard.features.scheduling import models as _sched_models  # noqa: F401
    from agentboard.features.work_items import models as _wi_models  # noqa: F401

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    s = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def registered_agent(session):
    """A Worker plus a logical Agent, ready to mount an instance."""
    from agentboard.features.projects.models import Agent as AgentRow
    from agentboard.features.scheduling import service as scheduling_service

    suffix = uuid.uuid4().hex[:8]
    worker_id = f"w-provider-{suffix}"
    scheduling_service.register_worker(session, worker_id=worker_id, hostname="provider-host")
    agent = AgentRow(
        agent_id=f"agent-provider-{suffix}", name="provider-agent",
        cli_command="", model="", auth_key="",
    )
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return worker_id, agent


@pytest.mark.parametrize("tool", CLI_TOOLS)
def test_instance_registration_accepts_every_cli_executor(session, registered_agent, tool):
    from agentboard.features.scheduling import service as scheduling_service

    worker_id, agent = registered_agent

    instance = scheduling_service.upsert_agent_instance(
        session, worker_id=worker_id, agent_id=agent.agent_id,
        cli_command="some-cli", executor_type=tool,
    )

    assert instance.executor_type == tool


def test_instance_registration_rejects_unknown_executor(session, registered_agent):
    from agentboard.core.exceptions import InvalidValue
    from agentboard.features.scheduling import service as scheduling_service

    worker_id, agent = registered_agent

    with pytest.raises(InvalidValue):
        scheduling_service.upsert_agent_instance(
            session, worker_id=worker_id, agent_id=agent.agent_id,
            cli_command="some-cli", executor_type="not-a-provider",
        )


@pytest.mark.parametrize("tool", CLI_TOOLS)
def test_durable_provider_resolution_accepts_every_cli_executor(tool):
    # router imports api at module scope; load the app first to avoid the cycle.
    import agentboard.api  # noqa: F401
    from agentboard.features.scheduling.router import _provider_from

    assert _provider_from(tool, None) == tool
    assert _provider_from(None, json.dumps(["planner", tool])) == tool


def test_durable_provider_resolution_fails_closed_on_unknown_role():
    import agentboard.api  # noqa: F401
    from agentboard.features.scheduling.router import _provider_from

    assert _provider_from(None, json.dumps(["not-a-provider"])) is None


def test_provider_whitelists_stay_in_sync():
    import agentboard.api  # noqa: F401
    from agentboard.features.scheduling import service as scheduling_service
    from agentboard.features.scheduling.router import _SUPPORTED_PROVIDERS

    expected = {"codex", "workbuddy", "cursor", "qoder", "fake", "scenario"}
    assert set(scheduling_service.EXECUTOR_TYPES) == expected
    assert _SUPPORTED_PROVIDERS == expected