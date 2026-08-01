"""Proposal 澄清回路的消息总线（Epic 96 · Story 156 P2-1）。

P1 的派发靠 DB 轮询：每个 Worker 定期全量拉 ``/api/proposals/pending`` 与
``?status=answered``。这条路能跑通闭环，但有三个结构性问题：

1. **派发延迟**——用户提交后最坏要等一个完整轮询周期才被拾起；
2. **空转随 Worker 数线性放大**——N 个 Worker 各自全量轮询，绝大多数是空转；
3. **毒消息无隔离通道**——反复失败的工作项只能靠翻日志发现。

本模块把「轮询拉」换成「事件推」，并补上死信队列。

设计要点
--------
**DB 始终是状态的唯一事实源，消息只是一句「去看一眼」的提示。**
消息体只放 ``proposal_id`` 与 ``round``，不放状态、不放正文。消费者收到消息后
一律回查 REST 再决策，因此：

- 消息丢了 → 轮询兜底 / reclaim-stale 重投，不会永久丢单；
- 消息重投（at-least-once）→ 服务端 CAS 认领 + ``(proposal_id, round_no)``
  唯一约束双重兜底，重复消费不会产生重复轮次；
- 消息过期（提案早已被处理）→ 回查后发现状态不可认领，直接 ack 丢弃。

这套「消息只做通知、状态回查数据库」的取舍，让 MQ 可以随时被摘掉而不影响正确性
（``AGENTBOARD_MQ_URL`` 为空即整体回退 P1 轮询）。

选型：pika 而非 aio-pika
------------------------
``worker.py`` 与 ``api.py`` 的提案链路全是同步代码（httpx.Client + 同步
SQLAlchemy Session）。引入 aio-pika 会强制 Worker 异步重写，并逼着同步端点在
请求线程里自建事件循环——收益为零，复杂度陡增。pika 的 BlockingConnection 与现
有同步架构天然契合。

代价是 ``BlockingConnection`` **非线程安全**：这里用 ``ProposalPublisher`` 加锁
串行化发布，并在连接失效时自动重连一次。

拓扑
----
::

    exchange <ns>            (direct, durable)
        └── <ns>.work        (durable, x-dead-letter-exchange=<ns>.dlx)
    exchange <ns>.dlx        (direct, durable)
        └── <ns>.dead        (durable)

命名空间由 ``AGENTBOARD_MQ_NAMESPACE`` 决定，默认 ``agentboard.proposals``。
测试用唯一命名空间，因此可以安全地跑在与其它项目共享的 broker 上。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

log = logging.getLogger("agentboard.mq")

DEFAULT_NAMESPACE = "agentboard.proposals"
ROUTING_DISPATCH = "dispatch"
ROUTING_DEAD = "dead"

# 发布原因（仅用于排障与指标，消费侧不依赖它做决策）
REASON_QUEUED = "queued"
REASON_ANSWERED = "answered"
REASON_RECLAIMED = "reclaimed"


# ===================== 异常 =====================

class MQError(Exception):
    """消息总线相关错误基类。"""


class MQMessageError(MQError):
    """消息载荷非法（毒消息）——必须进死信，绝不重投。"""


class MQUnavailable(MQError):
    """broker 不可达 / 未安装驱动。调用方应降级而非崩溃。"""


# ===================== 拓扑与配置 =====================

@dataclass(frozen=True)
class Topology:
    """一组命名空间下的交换机与队列名。发布方与消费方各自幂等声明。"""

    namespace: str = DEFAULT_NAMESPACE

    @property
    def exchange(self) -> str:
        return self.namespace

    @property
    def work_queue(self) -> str:
        return f"{self.namespace}.work"

    @property
    def dlx_exchange(self) -> str:
        return f"{self.namespace}.dlx"

    @property
    def dead_queue(self) -> str:
        return f"{self.namespace}.dead"

    @property
    def queue_arguments(self) -> dict:
        """主队列参数：被 reject(requeue=False) 的消息自动路由到死信队列。"""
        return {
            "x-dead-letter-exchange": self.dlx_exchange,
            "x-dead-letter-routing-key": ROUTING_DEAD,
        }


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        log.warning("环境变量 %s=%r 不是整数，回退默认值 %s", name, raw, default)
        return default


@dataclass
class MQConfig:
    """消息总线配置。``url`` 为空即禁用，整体回退 P1 轮询。"""

    url: str = ""
    namespace: str = DEFAULT_NAMESPACE
    prefetch: int = 1
    connect_timeout: float = 5.0

    @property
    def enabled(self) -> bool:
        return bool(str(self.url or "").strip())

    @property
    def topology(self) -> Topology:
        return Topology(self.namespace)

    @classmethod
    def from_env(cls) -> "MQConfig":
        return cls(
            url=(os.getenv("AGENTBOARD_MQ_URL") or "").strip(),
            namespace=(os.getenv("AGENTBOARD_MQ_NAMESPACE") or DEFAULT_NAMESPACE).strip()
            or DEFAULT_NAMESPACE,
            prefetch=max(1, _env_int("AGENTBOARD_MQ_PREFETCH", 1)),
            connect_timeout=float(_env_int("AGENTBOARD_MQ_CONNECT_TIMEOUT", 5)),
        )


# ===================== 消息 =====================

@dataclass(frozen=True)
class ProposalMessage:
    """派发消息。刻意只带定位信息，不带业务状态——状态一律回查数据库。"""

    proposal_id: int
    round: int = 0
    reason: str = ""
    ts: str = ""

    def to_dict(self) -> dict:
        return {
            "proposal_id": int(self.proposal_id),
            "round": int(self.round or 0),
            "reason": self.reason or "",
            "ts": self.ts or datetime.now(timezone.utc).isoformat(),
        }

    def to_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Any) -> "ProposalMessage":
        if not isinstance(data, dict):
            raise MQMessageError(
                f"消息体必须是 JSON 对象，实际为 {type(data).__name__}")
        raw = data.get("proposal_id")
        # 布尔是 int 的子类，显式挡掉，避免 True 被当成 proposal_id=1
        if isinstance(raw, bool) or raw is None:
            raise MQMessageError(f"消息缺少合法 proposal_id：{data!r}")
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            raise MQMessageError(f"proposal_id={raw!r} 不是整数") from None
        if pid <= 0:
            raise MQMessageError(f"proposal_id={pid} 必须为正整数")
        rnd = data.get("round") or 0
        try:
            rnd = int(rnd)
        except (TypeError, ValueError):
            rnd = 0
        return cls(
            proposal_id=pid,
            round=max(0, rnd),
            reason=str(data.get("reason") or ""),
            ts=str(data.get("ts") or ""),
        )

    @classmethod
    def from_bytes(cls, body: bytes | str) -> "ProposalMessage":
        if isinstance(body, bytes):
            try:
                body = body.decode("utf-8")
            except UnicodeDecodeError as e:
                raise MQMessageError(f"消息不是合法 UTF-8：{e}") from None
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError) as e:
            raise MQMessageError(f"消息不是合法 JSON：{e}") from None
        return cls.from_dict(data)


# 消费回调：返回 True=ack，False=拒收进死信；抛异常同样进死信。
MessageHandler = Callable[[ProposalMessage], bool]


class Broker(Protocol):  # pragma: no cover - 协议声明
    def declare_topology(self) -> None: ...
    def publish(self, message: ProposalMessage) -> bool: ...
    def consume(self, handler: MessageHandler, *, max_messages: int | None = None,
                idle_timeout: float | None = None,
                stop: threading.Event | None = None) -> dict: ...
    def close(self) -> None: ...


# ===================== 内存 broker（单测与降级） =====================

class InMemoryBroker:
    """进程内 broker。语义与 PikaBroker 对齐（含死信），供单测与离线降级使用。"""

    def __init__(self, config: MQConfig | None = None):
        self.config = config or MQConfig(url="memory://", namespace=DEFAULT_NAMESPACE)
        self.topology = self.config.topology
        self._lock = threading.Lock()
        self._queue: list[bytes] = []
        self._dead: list[bytes] = []
        self.declared = False
        self.published = 0

    def declare_topology(self) -> None:
        self.declared = True

    def publish(self, message: ProposalMessage) -> bool:
        with self._lock:
            self._queue.append(message.to_bytes())
            self.published += 1
        return True

    def publish_raw(self, body: bytes) -> None:
        """直接投递原始字节——用于构造毒消息验证死信路径。"""
        with self._lock:
            self._queue.append(body)

    def queue_depth(self, dead: bool = False) -> int:
        with self._lock:
            return len(self._dead if dead else self._queue)

    def dead_letters(self) -> list[bytes]:
        with self._lock:
            return list(self._dead)

    def purge(self) -> None:
        with self._lock:
            self._queue.clear()
            self._dead.clear()

    def consume(self, handler: MessageHandler, *, max_messages: int | None = None,
                idle_timeout: float | None = None,
                stop: threading.Event | None = None) -> dict:
        stats = {"consumed": 0, "acked": 0, "dead": 0}
        deadline = time.monotonic() + (idle_timeout or 0) if idle_timeout else None
        while True:
            if stop is not None and stop.is_set():
                break
            if max_messages is not None and stats["consumed"] >= max_messages:
                break
            with self._lock:
                body = self._queue.pop(0) if self._queue else None
            if body is None:
                if deadline is None or time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
                continue
            stats["consumed"] += 1
            if self._dispatch(handler, body):
                stats["acked"] += 1
            else:
                with self._lock:
                    self._dead.append(body)
                stats["dead"] += 1
        return stats

    def _dispatch(self, handler: MessageHandler, body: bytes) -> bool:
        try:
            msg = ProposalMessage.from_bytes(body)
        except MQMessageError as e:
            log.warning("丢弃毒消息（载荷非法）：%s", e)
            return False
        try:
            return bool(handler(msg))
        except Exception:
            log.exception("消息处理抛出未预期异常，转入死信：proposal_id=%s",
                          msg.proposal_id)
            return False

    def close(self) -> None:
        return None


# ===================== RabbitMQ broker =====================

class PikaBroker:
    """基于 pika BlockingConnection 的 RabbitMQ 实现。

    非线程安全（BlockingConnection 的固有限制）：每个线程各持一个实例，或经
    ``ProposalPublisher`` 加锁串行化。
    """

    def __init__(self, config: MQConfig):
        if not config.enabled:
            raise MQUnavailable("未配置 AGENTBOARD_MQ_URL，消息总线未启用")
        self.config = config
        self.topology = config.topology
        self._conn = None
        self._channel = None
        self._declared = False

    # ---------- 连接 ----------

    @staticmethod
    def _pika():
        try:
            import pika  # noqa: PLC0415 - 惰性导入，未启用 MQ 时不应硬依赖
        except ImportError as e:
            raise MQUnavailable(f"未安装 pika，无法接入 RabbitMQ：{e}") from None
        return pika

    def _connect(self):
        pika = self._pika()
        try:
            params = pika.URLParameters(self.config.url)
            params.socket_timeout = self.config.connect_timeout
            params.blocked_connection_timeout = self.config.connect_timeout
            self._conn = pika.BlockingConnection(params)
            self._channel = self._conn.channel()
            # 发布确认：basic_publish 在 broker 未确认时直接抛错，避免静默丢消息
            self._channel.confirm_delivery()
        except MQUnavailable:
            raise
        except Exception as e:
            self._conn = self._channel = None
            raise MQUnavailable(f"连接 RabbitMQ 失败（{self.config.url}）：{e}") from None
        return self._channel

    @property
    def channel(self):
        if self._channel is None or self._channel.is_closed:
            self._declared = False
            return self._connect()
        return self._channel

    def close(self) -> None:
        try:
            if self._conn is not None and self._conn.is_open:
                self._conn.close()
        except Exception:  # pragma: no cover - 关闭期异常无需上抛
            log.debug("关闭 RabbitMQ 连接时出错", exc_info=True)
        finally:
            self._conn = self._channel = None
            self._declared = False

    def __enter__(self) -> "PikaBroker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- 拓扑 ----------

    def declare_topology(self, force: bool = False) -> None:
        """幂等声明交换机、主队列、死信交换机与死信队列。"""
        if self._declared and not force:
            return
        ch = self.channel
        t = self.topology
        ch.exchange_declare(t.dlx_exchange, exchange_type="direct", durable=True)
        ch.queue_declare(t.dead_queue, durable=True)
        ch.queue_bind(t.dead_queue, t.dlx_exchange, routing_key=ROUTING_DEAD)
        ch.exchange_declare(t.exchange, exchange_type="direct", durable=True)
        ch.queue_declare(t.work_queue, durable=True, arguments=t.queue_arguments)
        ch.queue_bind(t.work_queue, t.exchange, routing_key=ROUTING_DISPATCH)
        self._declared = True

    def queue_depth(self, dead: bool = False) -> int:
        """被动查询队列深度（不改变拓扑），供测试与运维观测。"""
        self.declare_topology()
        name = self.topology.dead_queue if dead else self.topology.work_queue
        res = self.channel.queue_declare(name, durable=True, passive=True)
        return int(res.method.message_count)

    def purge(self) -> None:
        self.declare_topology()
        self.channel.queue_purge(self.topology.work_queue)
        self.channel.queue_purge(self.topology.dead_queue)

    def teardown(self) -> None:
        """删除本命名空间的队列与交换机——测试收尾用，避免污染共享 broker。"""
        try:
            ch = self.channel
            t = self.topology
            ch.queue_delete(t.work_queue)
            ch.queue_delete(t.dead_queue)
            ch.exchange_delete(t.exchange)
            ch.exchange_delete(t.dlx_exchange)
        except Exception:  # pragma: no cover
            log.debug("清理 MQ 拓扑失败", exc_info=True)

    # ---------- 发布 ----------

    def publish(self, message: ProposalMessage) -> bool:
        return self.publish_raw(message.to_bytes())

    def publish_raw(self, body: bytes) -> bool:
        """投递原始字节（毒消息测试直接走这里）。发布确认失败会抛 MQError。"""
        pika = self._pika()
        self.declare_topology()
        props = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,  # 持久化：broker 重启不丢消息
        )
        try:
            self.channel.basic_publish(
                exchange=self.topology.exchange,
                routing_key=ROUTING_DISPATCH,
                body=body,
                properties=props,
                mandatory=True,
            )
        except Exception as e:
            raise MQError(f"发布消息失败：{e}") from None
        return True

    # ---------- 消费 ----------

    def consume(self, handler: MessageHandler, *, max_messages: int | None = None,
                idle_timeout: float | None = None,
                stop: threading.Event | None = None) -> dict:
        """竞争消费。

        ``prefetch=1`` 让 broker 按「谁空闲谁拿」分发，配合服务端 CAS 认领构成
        双重仲裁。``idle_timeout`` 为「连续空闲多久就返回」，便于测试收敛与优雅退出。
        """
        self.declare_topology()
        ch = self.channel
        ch.basic_qos(prefetch_count=max(1, self.config.prefetch))
        stats = {"consumed": 0, "acked": 0, "dead": 0}
        tick = 0.5 if idle_timeout is None else min(0.5, max(0.05, idle_timeout))
        idle_started = time.monotonic()
        try:
            for method, _props, body in ch.consume(
                self.topology.work_queue, inactivity_timeout=tick,
            ):
                if method is None:  # 空闲心跳
                    if stop is not None and stop.is_set():
                        break
                    if (idle_timeout is not None
                            and time.monotonic() - idle_started >= idle_timeout):
                        break
                    continue
                idle_started = time.monotonic()
                stats["consumed"] += 1
                ok = self._dispatch(handler, body)
                if ok:
                    ch.basic_ack(method.delivery_tag)
                    stats["acked"] += 1
                else:
                    # requeue=False → 经 x-dead-letter-exchange 落入死信队列
                    ch.basic_nack(method.delivery_tag, requeue=False)
                    stats["dead"] += 1
                if stop is not None and stop.is_set():
                    break
                if max_messages is not None and stats["consumed"] >= max_messages:
                    break
        finally:
            try:
                ch.cancel()
            except Exception:  # pragma: no cover
                log.debug("取消消费者失败", exc_info=True)
        return stats

    def _dispatch(self, handler: MessageHandler, body: bytes) -> bool:
        try:
            msg = ProposalMessage.from_bytes(body)
        except MQMessageError as e:
            log.warning("丢弃毒消息（载荷非法），转入死信队列：%s", e)
            return False
        try:
            return bool(handler(msg))
        except Exception:
            log.exception("消息处理抛出未预期异常，转入死信：proposal_id=%s",
                          msg.proposal_id)
            return False


# ===================== 工厂 =====================

def build_broker(config: MQConfig | None = None) -> PikaBroker | None:
    """按配置构造 broker；未启用或驱动缺失时返回 None（调用方降级轮询）。"""
    cfg = config or MQConfig.from_env()
    if not cfg.enabled:
        return None
    try:
        return PikaBroker(cfg)
    except MQUnavailable as e:
        log.warning("消息总线不可用，降级为轮询：%s", e)
        return None


# ===================== 发布器（API 侧） =====================

class ProposalPublisher:
    """API 侧发布器：**best-effort**，永不让 MQ 故障影响 REST 返回。

    - 加锁串行化：BlockingConnection 非线程安全，而 FastAPI 同步端点跑在线程池里；
    - 断线自愈：发布失败先重连再试一次，仍失败则记告警返回 False；
    - 未启用时所有调用都是静默 no-op，既有行为完全不变。
    """

    def __init__(self, config: MQConfig | None = None,
                 broker: Any | None = None):
        self.config = config or MQConfig.from_env()
        self._lock = threading.Lock()
        self._broker = broker
        self._injected = broker is not None

    @property
    def enabled(self) -> bool:
        return self._injected or self.config.enabled

    def _get_broker(self):
        if self._broker is None:
            self._broker = PikaBroker(self.config)
        return self._broker

    def publish(self, proposal_id: int, round_no: int = 0,
                reason: str = "") -> bool:
        """发布一条派发消息。返回是否成功；失败仅告警，不抛异常。"""
        if not self.enabled:
            return False
        msg = ProposalMessage(
            proposal_id=int(proposal_id), round=int(round_no or 0), reason=reason,
            ts=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            for attempt in (1, 2):
                try:
                    return bool(self._get_broker().publish(msg))
                except Exception as e:
                    log.warning("发布提案 #%s 派发消息失败（第 %s 次）：%s",
                                proposal_id, attempt, e)
                    if self._injected or attempt == 2:
                        break
                    # 连接可能已失效，丢弃后重建再试一次
                    try:
                        self._broker.close()  # type: ignore[union-attr]
                    except Exception:
                        pass
                    self._broker = None
        return False

    def close(self) -> None:
        with self._lock:
            if self._broker is not None and not self._injected:
                try:
                    self._broker.close()
                except Exception:  # pragma: no cover
                    pass
            self._broker = None


_publisher: ProposalPublisher | None = None
_publisher_lock = threading.Lock()


def get_publisher() -> ProposalPublisher:
    """进程级单例发布器（按首次调用时的环境变量初始化）。"""
    global _publisher
    if _publisher is None:
        with _publisher_lock:
            if _publisher is None:
                _publisher = ProposalPublisher()
    return _publisher


def set_publisher(publisher: ProposalPublisher | None) -> None:
    """注入/重置发布器——测试用。"""
    global _publisher
    with _publisher_lock:
        if _publisher is not None and publisher is not _publisher:
            try:
                _publisher.close()
            except Exception:  # pragma: no cover
                pass
        _publisher = publisher


def publish_proposal_event(proposal_id: int, round_no: int = 0,
                           reason: str = "") -> bool:
    """给 API 层用的一行式发布入口：**任何情况下都不抛异常**。"""
    try:
        return get_publisher().publish(proposal_id, round_no, reason)
    except Exception:  # pragma: no cover - 兜底，MQ 绝不影响主流程
        log.warning("发布提案 #%s 派发消息时出现未预期异常", proposal_id,
                    exc_info=True)
        return False


def unique_namespace(prefix: str = "agentboard.test") -> str:
    """生成唯一命名空间——测试在共享 broker 上隔离，避免干扰其它项目队列。"""
    return f"{prefix}.{uuid.uuid4().hex[:12]}"
