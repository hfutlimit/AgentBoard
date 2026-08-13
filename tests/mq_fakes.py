"""MQ 测试 fake：可编程 pika Channel / Broker 替身。

用途：
- ``_FlakyChannel``：模拟「先产出 N 条消息，然后抛 ``StreamLostError``（断线）」
  或「空迭代器（恢复后空闲）」，供 ``PikaBroker.consume`` /
  ``PikaWorkflowBroker.consume`` 断线自愈回归测试用。
- ``_FlakyBroker``：注入 fake channel 序列，每次 ``_connect`` 弹出一个，
  验证「断线后自动重连并继续消费」。

本模块只暴露 pika Channel 协议的最小子集（``basic_qos`` / ``basic_ack`` /
``basic_nack`` / ``cancel`` / ``consume`` / ``close`` / 拓扑声明 no-op）。
真实 ``PikaBroker`` / ``PikaWorkflowBroker`` 通过 ``_connect`` 注入，
``_dispatch`` 内部 handler 收到的是 ``ProposalMessage`` /
``WorkflowMessage``（与生产路径同构）。

2026-08-13：从 ``tests/test_mq_consume_reconnect.py`` 抽出，供
``test_wf_mq_consume_reconnect.py`` 复用。
"""
from __future__ import annotations

import sys
import types
from typing import Callable, Iterable

# pika 缺失时构造一个 stub module（AMQPConnectionError / AMQPChannelError /
# StreamLostError）注入 sys.modules，让 ``agentboard.mq`` 的 _pika() 惰性 import
# 拿到同一个 stub 类。fake channel 抛本地类，PikaBroker.consume() 的
# ``except pika.exceptions.AMQPConnectionError`` 才能 catch 到 → 触发重连逻辑。
try:
    import pika.exceptions as pika_exc  # noqa: E402
except ImportError:  # pragma: no cover - pika 缺失时的兜底
    _pika_exc = types.ModuleType("pika.exceptions")

    class AMQPConnectionError(Exception):
        pass

    class AMQPChannelError(Exception):
        pass

    class StreamLostError(AMQPConnectionError):
        pass

    _pika_exc.AMQPConnectionError = AMQPConnectionError
    _pika_exc.AMQPChannelError = AMQPChannelError
    _pika_exc.StreamLostError = StreamLostError

    _pika = types.ModuleType("pika")
    _pika.exceptions = _pika_exc  # 关键：mq.py 用 pika.exceptions.AMQPConnectionError
    sys.modules["pika"] = _pika
    sys.modules["pika.exceptions"] = _pika_exc
    pika_exc = _pika_exc

from agentboard.mq import ProposalMessage, WorkflowMessage  # noqa: E402


class _Method:
    def __init__(self, tag: int):
        self.delivery_tag = tag


class _FlakyChannel:
    """可编程 fake channel：先产 N 条消息，再视 ``drop`` 抛断线或空转。

    - ``yield_items`` 产出的消息用 ``make_body(i)`` 构造（默认 ProposalMessage）；
    - ``drop=True`` 时产完抛 ``StreamLostError``（模拟断线）；
    - ``drop=False, revive=True`` 时空迭代（pika 空闲心跳 yield (None,None,None)），
      配合 ``idle_timeout`` 退出。
    """

    def __init__(
        self,
        name: str,
        yield_items: int = 0,
        drop: bool = True,
        revive: bool = False,
        make_body: Callable[[int], bytes] | None = None,
    ):
        self.name = name
        self.yield_items = yield_items
        self.drop = drop          # 消费中是否模拟断线
        self.revive = revive      # 本通道是否「恢复」（空迭代 + idle 退出）
        self.is_closed = False
        self.acked: list[int] = []
        self.nacked: list[tuple[int, bool]] = []
        self.qos = 0
        self.cancelled = False
        self._make_body = make_body or self._default_proposal_body

    @staticmethod
    def _default_proposal_body(i: int) -> bytes:
        return ProposalMessage(proposal_id=100 + i, round=1).to_bytes()

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

    def consume(self, queue: str, inactivity_timeout: float | None = None) -> Iterable:
        def _iter():
            for i in range(self.yield_items):
                yield (_Method(1000 + i), None, self._make_body(i))
            if self.drop:
                raise pika_exc.StreamLostError(
                    f"Stream connection lost: ConnectionResetError(10054, '{self.name} 测试断线')",
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


def make_workflow_body(i: int) -> bytes:
    """WorkflowMessage body 工厂，供 wf broker reconnect 测试用。

    使用 ``task.available``（WORKFLOW_EVENTS 白名单内的事件），构造合法载荷。
    """
    return WorkflowMessage(
        event="task.available", entity_type="task", entity_id=200 + i, ref_id=None,
    ).to_bytes()


class _FlakyBrokerMixin:
    """注入 fake channel 序列的 mixin（直接子类化 PikaBroker / PikaWorkflowBroker）。

    每次 ``_connect`` 按顺序弹出一个 channel：首个断线、后续恢复。
    """

    def _init_flaky(self, channels: list[_FlakyChannel]) -> None:
        self._channels = list(channels)
        self._connect_calls = 0

    def _connect(self):
        ch = self._channels[self._connect_calls % len(self._channels)]
        self._connect_calls += 1
        self._channel = ch
        self._conn = object()  # 非 None，供 close() 判空
        return ch


def _make_flaky_pika_broker_cls():
    """运行时构造 FlakyPikaBroker（双继承 PikaBroker + Mixin）。"""
    from agentboard.mq import PikaBroker  # noqa: PLC0415

    class _Cls(_FlakyBrokerMixin, PikaBroker):
        def __init__(self, channels: list[_FlakyChannel], **broker_kwargs):
            PikaBroker.__init__(self, **broker_kwargs)
            self._init_flaky(channels)

    return _Cls


FlakyPikaBroker = _make_flaky_pika_broker_cls()


def _make_flaky_pika_broker_cls():
    """运行时构造 FlakyPikaWorkflowBroker（双继承 PikaWorkflowBroker + Mixin）。"""
    from agentboard.mq import PikaWorkflowBroker  # noqa: PLC0415

    class _Cls(_FlakyBrokerMixin, PikaWorkflowBroker):
        def __init__(self, channels: list[_FlakyChannel], **broker_kwargs):
            PikaWorkflowBroker.__init__(self, **broker_kwargs)
            self._init_flaky(channels)

    return _Cls


FlakyPikaWorkflowBroker = _make_flaky_pika_broker_cls()
