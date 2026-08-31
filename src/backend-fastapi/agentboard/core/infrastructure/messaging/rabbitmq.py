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


class MessageRetry(MQError):
    """瞬时错误 —— handler 要求 requeue 重投（网络抖动 / server 暂不可用）。

    与死信的区别：死信假定「这条消息永远处理不了」；MessageRetry 假定
    「稍等片刻就能成功」。消费循环收到该异常按 requeue=True 处理，
    不进死信。handler 应自带退避与重试上限（如 ProposalWorker
    MSG_RETRY_BACKOFF），避免 server 长期宕机时无限空转。
    """


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
        stats = {"consumed": 0, "acked": 0, "dead": 0, "retried": 0}
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
            verdict = self._dispatch(handler, body)
            if verdict == "retry":
                # 瞬时失败：放回队头立即重投（handler 自带退避）
                with self._lock:
                    self._queue.insert(0, body)
                stats["retried"] += 1
            elif verdict == "ack":
                stats["acked"] += 1
            else:
                with self._lock:
                    self._dead.append(body)
                stats["dead"] += 1
        return stats

    def _dispatch(self, handler: MessageHandler, body: bytes) -> str:
        """三态判定："ack" 成功 / "dead" 永久失败 / "retry" 瞬时失败 requeue。"""
        try:
            msg = ProposalMessage.from_bytes(body)
        except MQMessageError as e:
            log.warning("丢弃毒消息（载荷非法）：%s", e)
            return "dead"
        try:
            return "ack" if handler(msg) else "dead"
        except MessageRetry as e:
            log.warning("消息处理瞬时失败，requeue 重投：proposal_id=%s（%s）",
                        msg.proposal_id, e)
            return "retry"
        except Exception:
            log.exception("消息处理抛出未预期异常，转入死信：proposal_id=%s",
                          msg.proposal_id)
            return "dead"

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
            params.heartbeat = 30  # 显式设置心跳，防NAT/防火墙静默切断长连接
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

        断线自愈（2026-08-12）：broker 重启 / 网络抖动时 pika 的 ``consume``
        迭代器会抛 ``AMQPConnectionError``（含 ``StreamLostError``）。此前该异常
        未捕获、直接冒泡导致 worker 进程整体崩溃退出。现在捕获后按指数退避
        重建连接继续消费；``stop`` 置位时立即退出，``max_messages`` /
        ``idle_timeout`` 语义保持不变。
        """
        pika = self._pika()
        stats = {"consumed": 0, "acked": 0, "dead": 0, "retried": 0}
        tick = 0.5 if idle_timeout is None else min(0.5, max(0.05, idle_timeout))
        retry_delay = 1.0  # 首次重连等待（秒），此后指数退避，封顶 30s
        max_retry_delay = 30.0
        idle_started = time.monotonic()
        _ch = None  # 当前迭代通道，返回前优雅取消消费

        def _should_stop() -> bool:
            return stop is not None and stop.is_set()

        def _cancel_and_return() -> dict:
            if _ch is not None:
                try:
                    _ch.cancel()
                except Exception:  # pragma: no cover - 连接已断时 cancel 失败无害
                    log.debug("取消消费者失败", exc_info=True)
            return stats

        while True:
            if _should_stop():
                break
            try:
                self.declare_topology()
                ch = self.channel
                _ch = ch
                ch.basic_qos(prefetch_count=max(1, self.config.prefetch))
                for method, _props, body in ch.consume(
                    self.topology.work_queue, inactivity_timeout=tick,
                ):
                    if method is None:  # 空闲心跳
                        if _should_stop():
                            return _cancel_and_return()
                        if (idle_timeout is not None
                                and time.monotonic() - idle_started >= idle_timeout):
                            return _cancel_and_return()
                        continue
                    idle_started = time.monotonic()
                    stats["consumed"] += 1
                    verdict = self._dispatch(handler, body)
                    if verdict == "retry":
                        # 瞬时失败（网络抖动 / server 5xx）：requeue 立即重投，
                        # handler 自带退避与次数上限，不进死信
                        ch.basic_nack(method.delivery_tag, requeue=True)
                        stats["retried"] += 1
                    elif verdict == "ack":
                        ch.basic_ack(method.delivery_tag)
                        stats["acked"] += 1
                    else:
                        # requeue=False → 经 x-dead-letter-exchange 落入死信队列
                        ch.basic_nack(method.delivery_tag, requeue=False)
                        stats["dead"] += 1
                    if _should_stop():
                        return _cancel_and_return()
                    if (max_messages is not None
                            and stats["consumed"] >= max_messages):
                        return _cancel_and_return()
                # 迭代器正常结束（broker 主动关闭消费）→ 视同断线，重连继续
                raise pika.exceptions.AMQPConnectionError(
                    "consume 迭代器提前结束（broker 关闭消费）"
                )
            except pika.exceptions.AMQPConnectionError as e:
                if _should_stop():
                    break
                log.warning("MQ 消费连接中断（%s），%.1fs 后重连…", e, retry_delay)
                self.close()
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            except pika.exceptions.AMQPChannelError as e:
                # 通道级错误（如队列被删）重连无法解决，但重建连接重试一次更稳妥
                if _should_stop():
                    break
                log.warning("MQ 消费通道异常（%s），%.1fs 后重连…", e, retry_delay)
                self.close()
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
        return _cancel_and_return()

    def _dispatch(self, handler: MessageHandler, body: bytes) -> str:
        """三态判定："ack" 成功 / "dead" 永久失败 / "retry" 瞬时失败 requeue。"""
        try:
            msg = ProposalMessage.from_bytes(body)
        except MQMessageError as e:
            log.warning("丢弃毒消息（载荷非法），转入死信队列：%s", e)
            return "dead"
        try:
            return "ack" if handler(msg) else "dead"
        except MessageRetry as e:
            log.warning("消息处理瞬时失败，requeue 重投：proposal_id=%s（%s）",
                        msg.proposal_id, e)
            return "retry"
        except Exception:
            log.exception("消息处理抛出未预期异常，转入死信：proposal_id=%s",
                          msg.proposal_id)
            return "dead"


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


# =====================================================================
# 通用工作流事件总线（Epic 122 · Story 230 · S1 M2）
# ---------------------------------------------------------------------
# 在 Proposal 专用总线之外**增量追加**，二者命名空间完全隔离：
#   - Proposal：agentboard.proposals（direct + 单 work 队列，上文不变）
#   - Workflow：agentboard.workflow（topic + 广播/定向双队列，见下）
#
# 拓扑（文档 #51 §6.2）::
#
#     exchange agentboard.workflow          (topic, durable)
#       ├── queue <ns>.broadcast            绑定 workflow.broadcast.#
#       └── queue <ns>.agent.{agent_id}     绑定 workflow.agent.{agent_id}
#     exchange agentboard.workflow.dlx      (direct, durable)
#       └── queue <ns>.dead
#
# 路由语义：
#   - 广播（认领型，竞争消费）：story.created / story.ready / task.*
#   - 定向（目标 Agent 专属）：review.requested / review.rejected / comment.replied
# 铁律与 Proposal 一致：消息只带定位信息，业务状态一律回查 DB；
# AGENTBOARD_MQ_URL 为空 → 发布 no-op，调用方回退轮询。
# =====================================================================

WORKFLOW_DEFAULT_NAMESPACE = "agentboard.workflow"
WORKFLOW_ENV_NAMESPACE = "AGENTBOARD_WORKFLOW_NAMESPACE"

# routing key 前缀（RabbitMQ topic 交换机的绑定模式）
ROUTING_WORKFLOW_BROADCAST = "workflow.broadcast"
ROUTING_WORKFLOW_AGENT = "workflow.agent"
# PR-4：内部编排事件路由（Python workflow_worker 专用，不进 .NET 消费）
# 用途：FastAPI 状态转换时发 internal 事件，Python 选 reviewer / unlock
# successor 等；.NET 只听 broadcast + agent 路由，不抢 internal
ROUTING_WORKFLOW_INTERNAL = "workflow.internal"

# ---- 事件常量（切片 1：Story 评审闭环；task.* 预留切片 2）----
EVENT_STORY_CREATED = "story.created"
EVENT_REVIEW_REQUESTED = "review.requested"
EVENT_REVIEW_REJECTED = "review.rejected"
# 切片 3 M3：多数决投票已记录（未达法定票数，等待更多票）
EVENT_REVIEW_VOTE_CAST = "review.vote_cast"
EVENT_COMMENT_REPLIED = "comment.replied"
EVENT_STORY_READY = "story.ready"
# 切片 2 预留（消费侧未实现，先占事件名保证白名单稳定）
EVENT_TASK_AVAILABLE = "task.available"
EVENT_TASK_READY_FOR_REVIEW = "task.ready_for_review"
EVENT_TASK_REVIEWED = "task.reviewed"
EVENT_TASK_REJECTED = "task.rejected"
# Proposal → Ticket 异步转化（2026-08-08 文档 #59）：
# ticket_requested —— 转换请求已创建（worker 消费，拉起 agent 生成 ticket）；
# ticket_created   —— ticket 生成成功（通知接力，供 workflow_worker / 定向队列感知）
EVENT_TICKET_REQUESTED = "proposal.ticket_requested"
EVENT_TICKET_CREATED = "proposal.ticket_created"
# Ticket 全流程（2026-08-09）：用户确认 Story 开始（人工闸门）→ 触发 agent 自动处理
EVENT_STORY_CONFIRMED = "story.confirmed"
# Agent MQ 消费（2026-08-09）：task.assigned —— 定向投递到指定 Agent 的 direct queue
# （任务被显式指派给某 agent，如 assignee 绑定；广播 task.available 仍是竞争入口）
EVENT_TASK_ASSIGNED = "task.assigned"

# ---- 事件常量命名空间统一（Step 4 P1-1，2026-08-10 review）----
# 旧 review.* / comment.* 跨 story+task 模糊（同一字符串既用于 story 评审
# 也用于 task 评审），消费端无法区分。统一为 entity.action 严格两段式：
# - story.review_requested / story.review_rejected / story.review_vote_cast
# - story.comment_replied
# - task.review_requested  / task.review_rejected  / task.review_vote_cast
# - task.comment_replied
#
# 旧名 EVENT_REVIEW_REQUESTED 等保留为 alias（指向 story.* 默认），1 release
# 后下架——届时新代码全部用 entity.action 形式。
EVENT_STORY_REVIEW_REQUESTED = "story.review_requested"
EVENT_STORY_REVIEW_REJECTED = "story.review_rejected"
EVENT_STORY_REVIEW_VOTE_CAST = "story.review_vote_cast"
EVENT_STORY_COMMENT_REPLIED = "story.comment_replied"
EVENT_TASK_REVIEW_REQUESTED = "task.review_requested"
EVENT_TASK_REVIEW_REJECTED = "task.review_rejected"
EVENT_TASK_REVIEW_VOTE_CAST = "task.review_vote_cast"
EVENT_TASK_COMMENT_REPLIED = "task.comment_replied"
# PR-4：内部编排事件（Python workflow_worker 专属，不进 .NET 消费）。
# 任务进入 in_review 时 FastAPI 发布此事件，Python 选 reviewer
# 然后 publish_event(EVENT_TASK_REVIEW_REQUESTED, route='agent')
# 定向通知 .NET 真正执行 review。把"分配 reviewer"和"执行 review"
# 两个不同 step 拆到不同事件 / 不同 queue 上，避免与 .NET 抢
# task.ready_for_review 广播导致双执行。
EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED = "task.review_assignment_needed"

# 兼容旧 import（pre-existing 客户端可能仍引用 review.* 模糊名）
# 1 release 后下架——届时新代码全部用 entity.action 形式。
EVENT_REVIEW_REQUESTED = EVENT_STORY_REVIEW_REQUESTED  # DEPRECATED
EVENT_REVIEW_REJECTED = EVENT_STORY_REVIEW_REJECTED  # DEPRECATED
EVENT_REVIEW_VOTE_CAST = EVENT_STORY_REVIEW_VOTE_CAST  # DEPRECATED
EVENT_COMMENT_REPLIED = EVENT_STORY_COMMENT_REPLIED  # DEPRECATED

WORKFLOW_EVENTS: frozenset[str] = frozenset({
    EVENT_STORY_CREATED,
    EVENT_STORY_CONFIRMED,
    EVENT_STORY_REVIEW_REQUESTED,
    EVENT_STORY_REVIEW_REJECTED,
    EVENT_STORY_REVIEW_VOTE_CAST,
    EVENT_STORY_COMMENT_REPLIED,
    EVENT_REVIEW_REQUESTED,           # 旧 alias 仍在白名单，向后兼容
    EVENT_REVIEW_REJECTED,
    EVENT_REVIEW_VOTE_CAST,
    EVENT_COMMENT_REPLIED,
    EVENT_STORY_READY,
    EVENT_TASK_AVAILABLE,
    EVENT_TASK_ASSIGNED,
    EVENT_TASK_READY_FOR_REVIEW,
    EVENT_TASK_REVIEWED,
    EVENT_TASK_REJECTED,
    EVENT_TASK_REVIEW_REQUESTED,
    EVENT_TASK_REVIEW_REJECTED,
    EVENT_TASK_REVIEW_VOTE_CAST,
    EVENT_TASK_COMMENT_REPLIED,
    EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED,
    EVENT_TICKET_REQUESTED,
    EVENT_TICKET_CREATED,
})

WORKFLOW_ENTITY_TYPES: frozenset[str] = frozenset({"story", "task", "proposal"})


class WorkflowTopology:
    """Workflow 事件总线拓扑：topic 交换机 + 广播队列 + 每 Agent 定向队列 + DLX。"""

    def __init__(self, namespace: str = WORKFLOW_DEFAULT_NAMESPACE):
        self.namespace = namespace

    @property
    def exchange(self) -> str:
        return self.namespace

    @property
    def broadcast_queue(self) -> str:
        return f"{self.namespace}.broadcast"

    def agent_queue(self, agent_id: str) -> str:
        """某 Agent 的定向队列名（随注册幂等声明，注销时保留）。"""
        return f"{self.namespace}.agent.{agent_id}"

    @property
    def dlx_exchange(self) -> str:
        return f"{self.namespace}.dlx"

    @property
    def dead_queue(self) -> str:
        return f"{self.namespace}.dead"

    @property
    def queue_arguments(self) -> dict:
        """主队列参数：nack(requeue=False) 的消息自动路由到死信队列。"""
        return {
            "x-dead-letter-exchange": self.dlx_exchange,
            "x-dead-letter-routing-key": ROUTING_DEAD,
        }

    # ---- routing key / 绑定模式 ----

    def broadcast_routing(self, event: str) -> str:
        """广播事件 routing key：workflow.broadcast.{event}"""
        return f"{ROUTING_WORKFLOW_BROADCAST}.{event}"

    def agent_routing(self, agent_id: str) -> str:
        """定向事件 routing key：workflow.agent.{agent_id}（事件类型在消息体）"""
        return f"{ROUTING_WORKFLOW_AGENT}.{agent_id}"

    def internal_routing(self, event: str) -> str:
        """PR-4：内部编排事件 routing key（Python workflow_worker 专用）。

        区别于 broadcast：broadcast 是 "N 个 event 谁拿到谁干"，internal
        是 "Python 专属的编排事件"，.NET 不订阅 internal_queue。
        """
        return f"{ROUTING_WORKFLOW_INTERNAL}.{event}"

    def broadcast_pattern(self) -> str:
        return f"{ROUTING_WORKFLOW_BROADCAST}.#"

    def agent_pattern(self, agent_id: str) -> str:
        return f"{ROUTING_WORKFLOW_AGENT}.{agent_id}"

    def internal_pattern(self) -> str:
        return f"{ROUTING_WORKFLOW_INTERNAL}.#"

    @property
    def internal_queue(self) -> str:
        """PR-4：内部编排队列（Python workflow_worker 订阅）。"""
        return f"{self.namespace}.internal"


@dataclass(frozen=True)
class WorkflowMessage:
    """通用工作流事件消息。刻意只带定位信息，不带业务状态——状态一律回查数据库。

    PR-2 加 3 字段（dispatch 决策 + 审计最小依赖）：

    - ``agent_type``：希望执行该事件的 CLI 类型（``workbuddy`` / ``codex`` /
      ``minimax``）。缺省时 consumer 按 task_type_routing 查表回填（PR-3）。
    - ``workload_type``：工作类别（``task`` / ``review`` / ``rework`` /
      ``ticket``），与 .NET 端 ``WorkloadTypes`` 对齐。consumer 用来选
      正确的 adapter（PR-3）。
    - ``correlation_id``：trace 链 id。publish 时缺省自动生成 UUID4；
      caller 可以在 proposal/story 创建时显式传一个，整条链复用。
      状态机和日志用它来串事件，定位"哪条链在哪里断"。
    """

    event: str
    entity_type: str
    entity_id: int
    ref_id: int | None = None
    ts: str = ""
    agent_type: str | None = None
    workload_type: str | None = None
    correlation_id: str = ""

    def to_dict(self) -> dict:
        return {
            "event": self.event,
            "entity_type": self.entity_type,
            "entity_id": int(self.entity_id),
            "ref_id": None if self.ref_id is None else int(self.ref_id),
            "ts": self.ts or datetime.now(timezone.utc).isoformat(),
            "agent_type": self.agent_type,
            "workload_type": self.workload_type,
            "correlation_id": self.correlation_id,
        }

    def to_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Any) -> "WorkflowMessage":
        if not isinstance(data, dict):
            raise MQMessageError(
                f"消息体必须是 JSON 对象，实际为 {type(data).__name__}")
        event = data.get("event")
        if not isinstance(event, str) or event not in WORKFLOW_EVENTS:
            raise MQMessageError(f"未知事件类型：{event!r}（白名单 {len(WORKFLOW_EVENTS)} 个）")
        et = data.get("entity_type")
        if not isinstance(et, str) or et not in WORKFLOW_ENTITY_TYPES:
            raise MQMessageError(f"非法 entity_type：{et!r}")
        raw_id = data.get("entity_id")
        # 布尔是 int 的子类，显式挡掉，避免 True 被当成 entity_id=1
        if isinstance(raw_id, bool) or raw_id is None:
            raise MQMessageError(f"消息缺少合法 entity_id：{data!r}")
        try:
            eid = int(raw_id)
        except (TypeError, ValueError):
            raise MQMessageError(f"entity_id={raw_id!r} 不是整数") from None
        if eid <= 0:
            raise MQMessageError(f"entity_id={eid} 必须为正整数")
        ref = data.get("ref_id")
        if ref is not None and not isinstance(ref, bool):
            try:
                ref = int(ref)
            except (TypeError, ValueError):
                ref = None
        # 新字段都是 optional 字符串；非 str 视为 None（容忍老 publisher 漏字段）
        def _opt_str(v: Any) -> str | None:
            if v is None:
                return None
            if not isinstance(v, str):
                return None
            v = v.strip()
            return v or None
        # correlation_id：缺省/非 str 一律空串；publisher 那边 publish 时会
        # 兜底生成 UUID4，consumer 端可以信任"非空即来自 publisher 自动生成"
        cid_raw = data.get("correlation_id")
        if not isinstance(cid_raw, str):
            cid_raw = ""
        return cls(
            event=event,
            entity_type=et,
            entity_id=eid,
            ref_id=None if ref is None else max(0, ref),
            ts=str(data.get("ts") or ""),
            agent_type=_opt_str(data.get("agent_type")),
            workload_type=_opt_str(data.get("workload_type")),
            correlation_id=cid_raw,
        )

    @classmethod
    def from_bytes(cls, body: bytes | str) -> "WorkflowMessage":
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
WorkflowMessageHandler = Callable[[WorkflowMessage], bool]


def _topic_match(pattern: str, routing_key: str) -> bool:
    """RabbitMQ topic 绑定模式匹配：`*` 匹配恰好一个段，`#` 匹配零或多个段。

    段按 ``.`` 分隔。无通配符时为精确匹配整串。
    """
    pp = pattern.split(".")
    rp = routing_key.split(".")

    def rec(i: int, j: int) -> bool:
        if i == len(pp):
            return j == len(rp)
        if pp[i] == "#":
            for k in range(j, len(rp) + 1):
                if rec(i + 1, k):
                    return True
            return False
        if j >= len(rp):
            return False
        if pp[i] != "*" and pp[i] != rp[j]:
            return False
        return rec(i + 1, j + 1)

    return rec(0, 0)


class InMemoryWorkflowBroker:
    """进程内 Workflow broker：支持 topic 匹配与多队列（广播 + 每 Agent 定向 + 死信）。

    语义与 PikaWorkflowBroker 对齐，供单测与离线降级使用。
    """

    def __init__(self, namespace: str = WORKFLOW_DEFAULT_NAMESPACE):
        self.topology = WorkflowTopology(namespace)
        self._lock = threading.Lock()
        # queue_name -> [routing pattern, ...]
        self._bindings: dict[str, list[str]] = {}
        self._queues: dict[str, list[bytes]] = {}
        self._dead: list[bytes] = []
        self.published = 0
        self._default_topology_declared = False

    def declare_topology(self) -> None:
        if not self._default_topology_declared:
            self._declare_queue(self.topology.broadcast_queue,
                                self.topology.broadcast_pattern())
            # PR-4：internal 队列（Python workflow_worker 专属）。.NET
            # 不订阅这个 queue；声明仅保证 topology 完整。
            self._declare_queue(self.topology.internal_queue,
                                self.topology.internal_pattern())
            self._declare_queue(self.topology.dead_queue, None)
            self._default_topology_declared = True

    def declare_agent_queue(self, agent_id: str) -> None:
        self._declare_queue(self.topology.agent_queue(agent_id),
                            self.topology.agent_pattern(agent_id))

    def _declare_queue(self, queue_name: str, pattern: str | None) -> None:
        with self._lock:
            if queue_name not in self._queues:
                self._queues[queue_name] = []
            if pattern is not None:
                binds = self._bindings.setdefault(queue_name, [])
                if pattern not in binds:
                    binds.append(pattern)

    def publish_raw(self, routing_key: str, body: bytes) -> bool:
        """按 routing key 投递到所有匹配绑定的队列。"""
        with self._lock:
            self.published += 1
            for queue_name, patterns in self._bindings.items():
                if any(_topic_match(p, routing_key) for p in patterns):
                    self._queues.setdefault(queue_name, []).append(body)
        return True

    def publish(self, routing_key: str, message: WorkflowMessage) -> bool:
        return self.publish_raw(routing_key, message.to_bytes())

    def queue_depth(self, queue: str, dead: bool = False) -> int:
        with self._lock:
            if dead:
                return len(self._dead)
            return len(self._queues.get(queue, []))

    def dead_letters(self) -> list[bytes]:
        with self._lock:
            return list(self._dead)

    def purge(self) -> None:
        with self._lock:
            for q in self._queues:
                self._queues[q].clear()
            self._dead.clear()

    def consume(self, queue_name: str, handler: WorkflowMessageHandler, *,
                max_messages: int | None = None,
                idle_timeout: float | None = None,
                stop: threading.Event | None = None) -> dict:
        stats = {"consumed": 0, "acked": 0, "dead": 0, "retried": 0}
        deadline = time.monotonic() + (idle_timeout or 0) if idle_timeout else None
        while True:
            if stop is not None and stop.is_set():
                break
            if max_messages is not None and stats["consumed"] >= max_messages:
                break
            with self._lock:
                q = self._queues.get(queue_name)
                body = q.pop(0) if q else None
            if body is None:
                if deadline is None or time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
                continue
            stats["consumed"] += 1
            verdict = self._dispatch(handler, body)
            if verdict == "retry":
                # 瞬时失败：放回队头立即重投（handler 自带退避）
                with self._lock:
                    self._queues.setdefault(queue_name, []).insert(0, body)
                stats["retried"] += 1
            elif verdict == "ack":
                stats["acked"] += 1
            else:
                with self._lock:
                    self._dead.append(body)
                stats["dead"] += 1
        return stats

    def _dispatch(self, handler: WorkflowMessageHandler, body: bytes) -> str:
        """三态判定："ack" 成功 / "dead" 永久失败 / "retry" 瞬时失败 requeue。"""
        try:
            msg = WorkflowMessage.from_bytes(body)
        except MQMessageError as e:
            log.warning("丢弃 Workflow 毒消息（载荷非法）：%s", e)
            return "dead"
        try:
            return "ack" if handler(msg) else "dead"
        except MessageRetry as e:
            log.warning("Workflow 消息处理瞬时失败，requeue 重投：event=%s "
                        "entity=%s#%s（%s）", msg.event, msg.entity_type,
                        msg.entity_id, e)
            return "retry"
        except Exception:
            log.exception("Workflow 消息处理抛出未预期异常，转入死信：event=%s "
                          "entity=%s#%s", msg.event, msg.entity_type, msg.entity_id)
            return "dead"

    def close(self) -> None:
        return None


class PikaWorkflowBroker:
    """基于 pika BlockingConnection 的 Workflow 事件总线实现（topic 语义）。

    非线程安全（BlockingConnection 固有限制）：经 ``WorkflowPublisher`` 加锁串行化，
    或每线程各持实例。
    """

    def __init__(self, config: MQConfig,
                 namespace: str = WORKFLOW_DEFAULT_NAMESPACE):
        if not config.enabled:
            raise MQUnavailable("未配置 AGENTBOARD_MQ_URL，消息总线未启用")
        self.config = config
        self.topology = WorkflowTopology(namespace)
        self._conn = None
        self._channel = None
        self._declared = False
        self._declared_agents: set[str] = set()

    # ---------- 连接（与 PikaBroker 同构） ----------

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
            params.heartbeat = 30  # 显式设置心跳，防NAT/防火墙静默切断长连接
            self._conn = pika.BlockingConnection(params)
            self._channel = self._conn.channel()
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
            self._declared_agents.clear()

    def __enter__(self) -> "PikaWorkflowBroker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- 拓扑 ----------

    def declare_topology(self, force: bool = False) -> None:
        """幂等声明 topic 交换机、广播队列、死信交换机与死信队列。"""
        if self._declared and not force:
            return
        ch = self.channel
        t = self.topology
        ch.exchange_declare(t.dlx_exchange, exchange_type="direct", durable=True)
        ch.queue_declare(t.dead_queue, durable=True)
        ch.queue_bind(t.dead_queue, t.dlx_exchange, routing_key=ROUTING_DEAD)
        ch.exchange_declare(t.exchange, exchange_type="topic", durable=True)
        ch.queue_declare(t.broadcast_queue, durable=True, arguments=t.queue_arguments)
        ch.queue_bind(t.broadcast_queue, t.exchange,
                      routing_key=t.broadcast_pattern())
        # PR-4：internal 队列（Python workflow_worker 专属）。
        ch.queue_declare(t.internal_queue, durable=True, arguments=t.queue_arguments)
        ch.queue_bind(t.internal_queue, t.exchange,
                      routing_key=t.internal_pattern())
        self._declared = True

    def declare_agent_queue(self, agent_id: str) -> None:
        """幂等声明某 Agent 的定向队列并绑定（随 Agent 注册调用）。"""
        if agent_id in self._declared_agents:
            return
        t = self.topology
        qname = t.agent_queue(agent_id)
        ch = self.channel
        ch.queue_declare(qname, durable=True, arguments=t.queue_arguments)
        ch.queue_bind(qname, t.exchange, routing_key=t.agent_pattern(agent_id))
        self._declared_agents.add(agent_id)

    def queue_depth(self, queue_name: str, dead: bool = False) -> int:
        self.declare_topology()
        name = self.topology.dead_queue if dead else queue_name
        res = self.channel.queue_declare(name, durable=True, passive=True)
        return int(res.method.message_count)

    def purge(self) -> None:
        self.declare_topology()
        t = self.topology
        self.channel.queue_purge(t.broadcast_queue)
        self.channel.queue_purge(t.dead_queue)
        for agent_id in list(self._declared_agents):
            self.channel.queue_purge(t.agent_queue(agent_id))

    def teardown(self) -> None:
        """删除本命名空间的队列与交换机——测试收尾用，避免污染共享 broker。"""
        try:
            ch = self.channel
            t = self.topology
            ch.queue_delete(t.broadcast_queue)
            ch.queue_delete(t.dead_queue)
            for agent_id in list(self._declared_agents):
                ch.queue_delete(t.agent_queue(agent_id))
            ch.exchange_delete(t.exchange)
            ch.exchange_delete(t.dlx_exchange)
        except Exception:  # pragma: no cover
            log.debug("清理 Workflow MQ 拓扑失败", exc_info=True)

    # ---------- 发布 ----------

    def publish(self, routing_key: str, message: WorkflowMessage) -> bool:
        return self.publish_raw(routing_key, message.to_bytes())

    def publish_raw(self, routing_key: str, body: bytes) -> bool:
        pika = self._pika()
        self.declare_topology()
        props = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,  # 持久化：broker 重启不丢消息
        )
        try:
            self.channel.basic_publish(
                exchange=self.topology.exchange,
                routing_key=routing_key,
                body=body,
                properties=props,
                mandatory=True,
            )
        except Exception as e:
            raise MQError(f"发布 Workflow 消息失败：{e}") from None
        return True

    # ---------- 消费 ----------

    def consume(self, queue_name: str, handler: WorkflowMessageHandler, *,
                max_messages: int | None = None,
                idle_timeout: float | None = None,
                stop: threading.Event | None = None) -> dict:
        """从指定队列竞争消费。

        ``prefetch=1`` 让 broker 按「谁空闲谁拿」分发；handler 返回 False 或抛异常
        即 nack(requeue=False) 落入死信队列。

        断线自愈（2026-08-13，配套 PikaBroker.consume 修复 #546ca77）：与 PikaBroker
        对称——broker 重启 / 网络抖动时 pika ``consume`` 迭代器抛
        ``AMQPConnectionError``（含 ``StreamLostError``），未捕获会冒泡导致 worker
        进程崩溃。现在按 1s→30s 指数退避重建连接继续消费；``stop`` 置位时立即退出，
        ``max_messages`` / ``idle_timeout`` 语义保持不变。注意 ``PikaWorkflowBroker``
        实际承载 worker 的两个后台消费线程（``_wf_broadcast_loop`` / ``_agent_direct_loop``），
        一旦崩溃会同时拉死 worker 进程，所以同样要覆盖。
        """
        pika = self._pika()
        stats = {"consumed": 0, "acked": 0, "dead": 0, "retried": 0}
        tick = 0.5 if idle_timeout is None else min(0.5, max(0.05, idle_timeout))
        retry_delay = 1.0  # 首次重连等待（秒），此后指数退避，封顶 30s
        max_retry_delay = 30.0
        idle_started = time.monotonic()
        _ch = None  # 当前迭代通道，返回前优雅取消消费

        def _should_stop() -> bool:
            return stop is not None and stop.is_set()

        def _cancel_and_return() -> dict:
            if _ch is not None:
                try:
                    _ch.cancel()
                except Exception:  # pragma: no cover - 连接已断时 cancel 失败无害
                    log.debug("取消 Workflow 消费者失败", exc_info=True)
            return stats

        while True:
            if _should_stop():
                break
            try:
                self.declare_topology()
                ch = self.channel
                _ch = ch
                ch.basic_qos(prefetch_count=max(1, self.config.prefetch))
                for method, _props, body in ch.consume(
                    queue_name, inactivity_timeout=tick,
                ):
                    if method is None:  # 空闲心跳
                        if _should_stop():
                            return _cancel_and_return()
                        if (idle_timeout is not None
                                and time.monotonic() - idle_started >= idle_timeout):
                            return _cancel_and_return()
                        continue
                    idle_started = time.monotonic()
                    stats["consumed"] += 1
                    verdict = self._dispatch(handler, body)
                    if verdict == "retry":
                        # 瞬时失败（网络抖动 / server 5xx）：requeue 立即重投，
                        # handler 自带退避与次数上限，不进死信
                        ch.basic_nack(method.delivery_tag, requeue=True)
                        stats["retried"] += 1
                    elif verdict == "ack":
                        ch.basic_ack(method.delivery_tag)
                        stats["acked"] += 1
                    else:
                        ch.basic_nack(method.delivery_tag, requeue=False)
                        stats["dead"] += 1
                    if _should_stop():
                        return _cancel_and_return()
                    if (max_messages is not None
                            and stats["consumed"] >= max_messages):
                        return _cancel_and_return()
                # 迭代器正常结束（broker 主动关闭消费）→ 视同断线，重连继续
                raise pika.exceptions.AMQPConnectionError(
                    "consume 迭代器提前结束（broker 关闭消费）",
                )
            except pika.exceptions.AMQPConnectionError as e:
                if _should_stop():
                    break
                log.warning("Workflow 消费连接中断（%s），%.1fs 后重连…", e, retry_delay)
                self.close()
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            except pika.exceptions.AMQPChannelError as e:
                # 通道级错误（队列被删等）重建连接通常无法解决，但重连一次更稳妥
                if _should_stop():
                    break
                log.warning("Workflow 消费通道异常（%s），%.1fs 后重连…", e, retry_delay)
                self.close()
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
        return _cancel_and_return()

    def _dispatch(self, handler: WorkflowMessageHandler, body: bytes) -> str:
        """三态判定："ack" 成功 / "dead" 永久失败 / "retry" 瞬时失败 requeue。"""
        try:
            msg = WorkflowMessage.from_bytes(body)
        except MQMessageError as e:
            log.warning("丢弃 Workflow 毒消息（载荷非法），转入死信队列：%s", e)
            return "dead"
        try:
            return "ack" if handler(msg) else "dead"
        except MessageRetry as e:
            log.warning("Workflow 消息处理瞬时失败，requeue 重投：event=%s（%s）",
                        msg.event, e)
            return "retry"
        except Exception:
            log.exception("Workflow 消息处理抛出未预期异常，转入死信：event=%s",
                          msg.event)
            return "dead"


# ===================== Workflow 发布器（API 侧） =====================

class WorkflowPublisher:
    """Workflow 事件发布器：**best-effort**，永不让 MQ 故障影响 REST 返回。

    - 加锁串行化：BlockingConnection 非线程安全，而 FastAPI 同步端点跑在线程池里；
    - 断线自愈：发布失败先重连再试一次，仍失败则记告警返回 False；
    - 未启用（未配置 URL / 未注入 broker）时所有调用都是静默 no-op 返回 False，
      调用方回退轮询，正确性不变。
    """

    def __init__(self, config: MQConfig | None = None,
                 broker: Any | None = None,
                 namespace: str = WORKFLOW_DEFAULT_NAMESPACE):
        self.config = config or MQConfig.from_env()
        self._namespace = namespace
        self._lock = threading.Lock()
        self._broker = broker
        self._injected = broker is not None

    @property
    def enabled(self) -> bool:
        return self._injected or self.config.enabled

    @property
    def topology(self) -> WorkflowTopology:
        return WorkflowTopology(self._namespace)

    def _get_broker(self):
        if self._broker is None:
            self._broker = PikaWorkflowBroker(self.config, self._namespace)
        return self._broker

    def publish(self, event: str, entity_type: str, entity_id: int,
                ref_id: int | None = None, *, agent_id: str | None = None,
                agent_type: str | None = None,
                workload_type: str | None = None,
                correlation_id: str | None = None,
                route: str = "auto",
                worker_id: str | None = None) -> bool:
        """发布一条工作流事件。

        - ``route="auto"``（默认）→
            - ``worker_id`` 非空 → 定向投递到 ``workflow.agent.{worker_id}``（PR-5
              物理身份，.NET worker 按 ``_identity.WorkerId`` 订阅）；
            - 否则 ``agent_id`` 非空 → 定向投递到 ``workflow.agent.{agent_id}``
              （PR-5 之前行为，向后兼容；建议 caller 改成传 worker_id）；
            - 都没有 → 广播（story.created / story.ready / task.* 认领型）；
        - ``route="internal"`` → 强制走 internal 路由（PR-4，Python
          workflow_worker 专用；.NET 不订阅）；agent_id/worker_id 仍生效
          但通常不同时设（internal 不定向）。
        - ``agent_type`` / ``workload_type`` / ``correlation_id`` 全部 optional，
          缺省 ``correlation_id`` 时自动生成 UUID4 让日志能串链。其它两个
          缺省时由 consumer 端按 task_type_routing 查表回填（PR-3）。
        - 返回是否投递成功；失败仅告警，不抛异常。
        """
        if not self.enabled:
            return False
        if event not in WORKFLOW_EVENTS:
            log.warning("拒绝发布未知事件类型：%r", event)
            return False
        msg = WorkflowMessage(
            event=event, entity_type=entity_type, entity_id=int(entity_id),
            ref_id=None if ref_id is None else int(ref_id),
            ts=datetime.now(timezone.utc).isoformat(),
            agent_type=(agent_type or None),
            workload_type=(workload_type or None),
            correlation_id=(correlation_id or str(uuid.uuid4())),
        )
        if route == "internal":
            routing_key = self.topology.internal_routing(event)
        elif route == "broadcast":
            routing_key = self.topology.broadcast_routing(event)
        else:  # "auto" 或未识别
            # PR-5：worker_id 优先（物理身份 = 实际 .NET 订阅的 queue），
            # 没 worker_id 才回退 agent_id（逻辑身份 = 老路由 = 通常没人收）。
            if worker_id:
                routing_key = self.topology.agent_routing(worker_id)
            elif agent_id:
                routing_key = self.topology.agent_routing(agent_id)
            else:
                routing_key = self.topology.broadcast_routing(event)
        with self._lock:
            for attempt in (1, 2):
                try:
                    return bool(self._get_broker().publish(routing_key, msg))
                except Exception as e:
                    log.warning("发布工作流事件 %s 失败（第 %s 次）：%s",
                                event, attempt, e)
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


_workflow_publisher: WorkflowPublisher | None = None
_workflow_publisher_lock = threading.Lock()


def get_workflow_publisher() -> WorkflowPublisher:
    """进程级单例 Workflow 发布器（按首次调用时的环境变量初始化）。"""
    global _workflow_publisher
    if _workflow_publisher is None:
        with _workflow_publisher_lock:
            if _workflow_publisher is None:
                _workflow_publisher = WorkflowPublisher()
    return _workflow_publisher


def set_workflow_publisher(publisher: WorkflowPublisher | None) -> None:
    """注入/重置发布器——测试用。"""
    global _workflow_publisher
    with _workflow_publisher_lock:
        if _workflow_publisher is not None and publisher is not _workflow_publisher:
            try:
                _workflow_publisher.close()
            except Exception:  # pragma: no cover
                pass
        _workflow_publisher = publisher


def publish_workflow_event(event: str, entity_type: str, entity_id: int,
                           ref_id: int | None = None, *,
                           agent_id: str | None = None,
                           agent_type: str | None = None,
                           workload_type: str | None = None,
                           correlation_id: str | None = None,
                           route: str = "auto",
                           worker_id: str | None = None) -> bool:
    """给 API 层用的一行式发布入口：**任何情况下都不抛异常**。

    PR-2 增 3 kwargs（``agent_type`` / ``workload_type`` / ``correlation_id``），
    旧 caller 不传也能用——它们都是 optional，``correlation_id`` 缺省
    自动生成 UUID4。

    PR-4 增 ``route`` kwarg：``"internal"`` 走 internal 路由（Python
    workflow_worker 专用），其它值按 auto 行为。

    PR-5 增 ``worker_id`` kwarg：物理身份，优先于 ``agent_id`` 决定
    routing key（``.NET worker`` 按 ``_identity.WorkerId`` 订阅
    ``workflow.agent.{workerId}``，不按 agent_id）。
    """
    try:
        return get_workflow_publisher().publish(
            event, entity_type, entity_id, ref_id, agent_id=agent_id,
            agent_type=agent_type, workload_type=workload_type,
            correlation_id=correlation_id, route=route, worker_id=worker_id)
    except Exception:  # pragma: no cover - 兜底，MQ 绝不影响主流程
        log.warning("发布工作流事件 %s 时出现未预期异常", event, exc_info=True)
        return False
