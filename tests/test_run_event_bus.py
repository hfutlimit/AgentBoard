"""P1-7: the public surface for AgentRun event pub/sub is now an
``IRunEventBus`` protocol. The in-process implementation is exercised
here so a future broker-backed implementation can drop in without
breaking the contract.
"""
import asyncio

from agentboard.features.scheduling.run_event_bus import (
    InProcessRunEventBus,
    RunEventSubscription,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_in_process_bus_subscribe_then_broadcast_round_trip():
    bus = InProcessRunEventBus()

    async def scenario():
        subscription = bus.subscribe(42)
        try:
            assert isinstance(subscription, RunEventSubscription)
            payload = {"id": 1, "run_id": 42, "type": "agent.output"}
            bus.broadcast(42, payload)
            received = await asyncio.wait_for(subscription.queue.get(), timeout=0.5)
            assert received == payload
        finally:
            bus.unsubscribe(42, subscription)

    _run(scenario())


def test_broadcast_only_targets_matching_run_id():
    bus = InProcessRunEventBus()

    async def scenario():
        sub_a = bus.subscribe(1)
        sub_b = bus.subscribe(2)
        try:
            bus.broadcast(1, {"id": "a"})
            bus.broadcast(2, {"id": "b"})
            a = await asyncio.wait_for(sub_a.queue.get(), timeout=0.5)
            b = await asyncio.wait_for(sub_b.queue.get(), timeout=0.5)
            assert a["id"] == "a" and b["id"] == "b"
        finally:
            bus.unsubscribe(1, sub_a)
            bus.unsubscribe(2, sub_b)

    _run(scenario())


def test_unsubscribe_stops_delivery_and_cleans_state():
    bus = InProcessRunEventBus()

    async def scenario():
        sub = bus.subscribe(7)
        bus.unsubscribe(7, sub)
        # broadcast after unsubscribe should not enqueue.
        bus.broadcast(7, {"id": 99})
        # queue.get with a small timeout must raise TimeoutError.
        with __import__("pytest").raises(asyncio.TimeoutError):
            await asyncio.wait_for(sub.queue.get(), timeout=0.05)

    _run(scenario())


def test_broadcast_from_another_thread_round_trips_via_call_soon_threadsafe():
    """The bus must be safe when ``broadcast`` is invoked from a
    worker thread while the subscriber's queue lives on the asyncio
    loop that drives the test.
    """
    import threading
    bus = InProcessRunEventBus()

    async def scenario():
        sub = bus.subscribe(11)
        try:
            done = threading.Event()
            captured: list[dict] = []

            def fire():
                bus.broadcast(11, {"id": "thread"})
                done.set()

            t = threading.Thread(target=fire)
            t.start()
            t.join(timeout=1.0)
            assert done.is_set()
            received = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
            captured.append(received)
            assert captured == [{"id": "thread"}]
        finally:
            bus.unsubscribe(11, sub)

    _run(scenario())
