from types import SimpleNamespace

# The scheduling router exposes the shared agent_state_hub from api.py.  Load
# the application facade first so the split-router import is fully initialized.
from agentboard import api  # noqa: F401
from agentboard.features.scheduling.router import _event_to_wire, _format_sse


def test_run_event_payload_is_an_object_on_the_wire():
    event = SimpleNamespace(
        id=7,
        run_id=42,
        event_type="agent.output",
        payload='{"message":"working"}',
        created_at=SimpleNamespace(isoformat=lambda: "2026-08-24T12:00:00"),
    )

    wire = _event_to_wire(event)
    assert wire["payload"] == {"message": "working"}
    sse = _format_sse(wire)
    assert '"payload": {"message": "working"}' in sse
    assert "id: 7\n" in sse
    assert "event: agent.output\n" in sse
