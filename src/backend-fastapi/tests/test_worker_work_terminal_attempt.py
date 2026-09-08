from types import SimpleNamespace

from agentboard.features.scheduling.worker_work import terminal_attempt_matches


def test_terminal_attempt_matches_requires_agent_worker_and_token():
    row = SimpleNamespace(agent_id=7, worker_id="worker-a", lease_token="token-32")
    agent = SimpleNamespace(id=7)

    assert terminal_attempt_matches(row, SimpleNamespace(worker_id="worker-a", token="token-32"), agent)
    assert not terminal_attempt_matches(row, SimpleNamespace(worker_id="worker-b", token="token-32"), agent)
    assert not terminal_attempt_matches(row, SimpleNamespace(worker_id="worker-a", token="token-33"), agent)
    assert not terminal_attempt_matches(row, SimpleNamespace(worker_id="worker-a", token="token-32"), SimpleNamespace(id=8))
