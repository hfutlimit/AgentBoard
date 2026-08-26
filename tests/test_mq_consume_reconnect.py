"""PikaBroker.consume 断线重连单测（2026-08-12）。

背景：MQ broker 重启 / 网络抖动时，pika ``consume`` 迭代器抛
``AMQPConnectionError``（含 ``StreamLostError``）。修复前该异常未捕获、
直接冒泡导致 worker 进程整体崩溃退出（生产 07:10:47 已发生）。

本测试用「先断线后恢复」的 fake channel 验证：
1. 断线后自动重连并继续消费（consumed/acked 跨连接累计）；
2. 断线重试期间 stop 置位可立即退出（不无限重连）；
3. 消息语义不变：ack / nack(死信) / max_messages / idle_timeout。

2026-08-13：抽出 fake channel 到 ``tests/mq_fakes.py`` 复用，本文件
只剩 4 个断言用例。
"""
import os
import sys
import threading
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentboard.mq import MQConfig  # noqa: E402

from tests.mq_fakes import FlakyPikaBroker, _FlakyChannel  # noqa: E402


def _ok_handler(_msg) -> bool:
    return True


def _nack_handler(_msg) -> bool:
    return False


def test_consume_reconnects_after_stream_lost():
    """断线 → 自动重连 → 继续消费，统计跨连接累计。"""
    ch1 = _FlakyChannel("ch1", yield_items=2, drop=True)   # 消费 2 条后断线
    ch2 = _FlakyChannel("ch2", yield_items=1, drop=False, revive=True)  # 恢复
    broker = FlakyPikaBroker(
        [ch1, ch2],
        config=MQConfig(url="amqp://x@localhost/%2F", namespace="t"),
    )

    stats = broker.consume(_ok_handler, max_messages=3, idle_timeout=0.2)

    assert broker._connect_calls == 2, "应重连一次"
    assert stats == {"consumed": 3, "acked": 3, "dead": 0, "retried": 0}
    assert ch1.acked == [1000, 1001]
    assert ch2.acked == [1000]
    assert ch1.qos == 1 and ch2.qos == 1


def test_consume_nack_goes_dead_across_reconnect():
    """死信语义跨连接保持：nack 计数累计。"""
    ch1 = _FlakyChannel("ch1", yield_items=2, drop=True)
    ch2 = _FlakyChannel("ch2", yield_items=1, drop=False, revive=True)
    broker = FlakyPikaBroker(
        [ch1, ch2],
        config=MQConfig(url="amqp://x@localhost/%2F", namespace="t"),
    )

    stats = broker.consume(_nack_handler, max_messages=3, idle_timeout=0.2)

    assert stats == {"consumed": 3, "acked": 0, "dead": 3, "retried": 0}
    assert len(ch1.nacked) == 2 and ch1.nacked[0][1] is False
    assert len(ch2.nacked) == 1


def test_consume_stop_exits_during_retry_backoff():
    """断线重连退避期间 stop 置位 → 立即退出，不无限重连。"""
    ch1 = _FlakyChannel("ch1", yield_items=1, drop=True)
    broker = FlakyPikaBroker(
        [ch1],
        config=MQConfig(url="amqp://x@localhost/%2F", namespace="t"),
    )
    stop = threading.Event()

    def _stopper():
        time.sleep(0.05)
        stop.set()

    t = threading.Thread(target=_stopper)
    t.start()
    start = time.monotonic()
    stats = broker.consume(_ok_handler, max_messages=10, stop=stop)
    elapsed = time.monotonic() - start
    t.join()

    assert stats["consumed"] == 1, "断线前已消费 1 条"
    assert elapsed < 1.5, f"应在退避期被 stop 打断，实际 {elapsed:.2f}s"
    assert broker._connect_calls >= 1


def test_consume_idle_timeout_still_works():
    """恢复连接后 idle_timeout 仍正常触发退出。"""
    ch1 = _FlakyChannel("ch1", yield_items=0, drop=True)
    ch2 = _FlakyChannel("ch2", yield_items=0, drop=False, revive=True)
    broker = FlakyPikaBroker(
        [ch1, ch2],
        config=MQConfig(url="amqp://x@localhost/%2F", namespace="t"),
    )

    start = time.monotonic()
    stats = broker.consume(_ok_handler, idle_timeout=0.15)
    elapsed = time.monotonic() - start

    assert stats == {"consumed": 0, "acked": 0, "dead": 0, "retried": 0}
    assert elapsed < 3, "恢复通道空闲后应在 idle_timeout 内退出"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
