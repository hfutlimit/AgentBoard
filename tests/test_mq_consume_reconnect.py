"""PikaBroker.consume 断线重连单测（2026-08-12）。

背景：MQ broker 重启 / 网络抖动时，pika ``consume`` 迭代器抛
``AMQPConnectionError``（含 ``StreamLostError``）。修复前该异常未捕获、
直接冒泡导致 worker 进程整体崩溃退出（生产 07:10:47 已发生）。

本测试用「先断线后恢复」的 fake channel 验证：
1. 断线后自动重连并继续消费（consumed/acked 跨连接累计）；
2. 断线重试期间 stop 置位可立即退出（不无限重连）；
3. 消息语义不变：ack / nack(死信) / max_messages / idle_timeout。
"""
import os
import sys
import threading
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pika.exceptions as pika_exc  # noqa: E402

from agentboard import mq  # noqa: E402
from agentboard.mq import MQConfig, PikaBroker, ProposalMessage  # noqa: E402


class _Method:
    def __init__(self, tag):
        self.delivery_tag = tag


class _FlakyChannel:
    """可编程 fake channel：

    - ``consume`` 返回迭代器，先产出 ``yield_items`` 条消息，然后
      抛 ``StreamLostError``（模拟断线）；若 ``revive_after`` 指定了第几次
      连接后恢复，则后续连接返回空迭代器（配合 idle_timeout 退出）。
    - ``close()`` 置 ``is_closed``，供 broker 判定通道失效。
    """

    def __init__(self, name: str, yield_items: int = 0, drop: bool = True,
                 revive: bool = False):
        self.name = name
        self.yield_items = yield_items
        self.drop = drop          # 消费中是否模拟断线
        self.revive = revive      # 本通道是否「恢复」（空迭代 + idle 退出）
        self.is_closed = False
        self.acked: list[int] = []
        self.nacked: list[int] = []
        self.qos = 0
        self.cancelled = False

    # ---- pika Channel 接口 ----
    def basic_qos(self, prefetch_count: int) -> None:
        self.qos = prefetch_count

    def basic_ack(self, delivery_tag) -> None:
        self.acked.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue: bool = False) -> None:
        self.nacked.append((delivery_tag, requeue))

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.is_closed = True

    # ---- 拓扑声明（declare_topology 调用，no-op） ----
    def exchange_declare(self, *a, **kw):
        return None

    def queue_declare(self, *a, **kw):
        return None

    def queue_bind(self, *a, **kw):
        return None

    def consume(self, queue: str, inactivity_timeout: float | None = None):
        def _iter():
            for i in range(self.yield_items):
                body = ProposalMessage(proposal_id=100 + i, round=1).to_bytes()
                yield (_Method(1000 + i), None, body)
            if self.drop:
                raise pika_exc.StreamLostError(
                    "Stream connection lost: ConnectionResetError(10054, '测试断线')"
                )
            # 恢复通道：空迭代（pika 空闲心跳 yield (None, None, None)），
            # 由上层 idle_timeout / stop 退出
            while True:
                yield (None, None, None)
        return _iter()

    # ---- broker 判定用 ----
    @property
    def _is_closed(self) -> bool:
        return self.is_closed


class _FlakyBroker(PikaBroker):
    """注入 fake channel 序列的 PikaBroker。

    每次 ``_connect`` 按顺序弹出一个 channel：首个断线、后续恢复。
    """

    def __init__(self, channels: list[_FlakyChannel]):
        super().__init__(MQConfig(url="amqp://x@localhost/%2F", namespace="t"))
        self._channels = list(channels)
        self._connect_calls = 0

    def _connect(self):
        ch = self._channels[self._connect_calls % len(self._channels)]
        self._connect_calls += 1
        self._channel = ch
        self._conn = object()  # 非 None，供 close() 判空
        return ch


def _ok_handler(_msg) -> bool:
    return True


def _nack_handler(_msg) -> bool:
    return False


def test_consume_reconnects_after_stream_lost():
    """断线 → 自动重连 → 继续消费，统计跨连接累计。"""
    ch1 = _FlakyChannel("ch1", yield_items=2, drop=True)   # 消费 2 条后断线
    ch2 = _FlakyChannel("ch2", yield_items=1, drop=False, revive=True)  # 恢复
    broker = _FlakyBroker([ch1, ch2])

    stats = broker.consume(_ok_handler, max_messages=3, idle_timeout=0.2)

    assert broker._connect_calls == 2, "应重连一次"
    assert stats == {"consumed": 3, "acked": 3, "dead": 0}
    assert ch1.acked == [1000, 1001]
    assert ch2.acked == [1000]
    assert ch1.qos == 1 and ch2.qos == 1


def test_consume_nack_goes_dead_across_reconnect():
    """死信语义跨连接保持：nack 计数累计。"""
    ch1 = _FlakyChannel("ch1", yield_items=2, drop=True)
    ch2 = _FlakyChannel("ch2", yield_items=1, drop=False, revive=True)
    broker = _FlakyBroker([ch1, ch2])

    stats = broker.consume(_nack_handler, max_messages=3, idle_timeout=0.2)

    assert stats == {"consumed": 3, "acked": 0, "dead": 3}
    assert len(ch1.nacked) == 2 and ch1.nacked[0][1] is False
    assert len(ch2.nacked) == 1


def test_consume_stop_exits_during_retry_backoff():
    """断线重连退避期间 stop 置位 → 立即退出，不无限重连。"""
    ch1 = _FlakyChannel("ch1", yield_items=1, drop=True)
    broker = _FlakyBroker([ch1])
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
    broker = _FlakyBroker([ch1, ch2])

    start = time.monotonic()
    stats = broker.consume(_ok_handler, idle_timeout=0.15)
    elapsed = time.monotonic() - start

    assert stats == {"consumed": 0, "acked": 0, "dead": 0}
    assert elapsed < 3, "恢复通道空闲后应在 idle_timeout 内退出"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
