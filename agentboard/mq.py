"""RabbitMQ wake-up channel for Proposal workers.

The database is the source of truth. Messages only reduce polling latency, so a
temporarily unavailable broker never makes the Proposal workflow unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Callable


@dataclass(frozen=True)
class MQConfig:
    url: str
    namespace: str = "agentboard.proposals"
    prefetch: int = 1

    @classmethod
    def from_env(cls) -> "MQConfig | None":
        url = os.getenv("AGENTBOARD_MQ_URL", "").strip()
        if not url:
            return None
        return cls(
            url=url,
            namespace=os.getenv("AGENTBOARD_MQ_NAMESPACE", "agentboard.proposals").strip(),
            prefetch=max(1, int(os.getenv("AGENTBOARD_MQ_PREFETCH", "1"))),
        )

    @property
    def exchange(self) -> str:
        return f"{self.namespace}.events"

    @property
    def work_queue(self) -> str:
        return f"{self.namespace}.work"

    @property
    def dead_exchange(self) -> str:
        return f"{self.namespace}.dead"

    @property
    def dead_queue(self) -> str:
        return f"{self.namespace}.dead"


@dataclass(frozen=True)
class ProposalMessage:
    proposal_id: int
    event: str

    def encode(self) -> bytes:
        return json.dumps(
            {"version": 1, "proposal_id": self.proposal_id, "event": self.event},
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def decode(cls, body: bytes) -> "ProposalMessage":
        data = json.loads(body.decode("utf-8"))
        proposal_id = int(data["proposal_id"])
        event = str(data["event"])
        if data.get("version") != 1 or proposal_id < 1 or not event:
            raise ValueError("invalid proposal message")
        return cls(proposal_id=proposal_id, event=event)


class PikaBroker:
    def __init__(self, config: MQConfig):
        self.config = config

    def _connection(self):
        import pika
        return pika.BlockingConnection(pika.URLParameters(self.config.url))

    def _declare(self, channel) -> None:
        channel.exchange_declare(exchange=self.config.exchange, exchange_type="direct", durable=True)
        channel.exchange_declare(exchange=self.config.dead_exchange, exchange_type="direct", durable=True)
        channel.queue_declare(queue=self.config.dead_queue, durable=True)
        channel.queue_bind(queue=self.config.dead_queue, exchange=self.config.dead_exchange, routing_key="dead")
        channel.queue_declare(
            queue=self.config.work_queue,
            durable=True,
            arguments={
                "x-dead-letter-exchange": self.config.dead_exchange,
                "x-dead-letter-routing-key": "dead",
            },
        )
        channel.queue_bind(queue=self.config.work_queue, exchange=self.config.exchange, routing_key="work")

    def publish(self, message: ProposalMessage) -> None:
        import pika
        with self._connection() as connection:
            channel = connection.channel()
            self._declare(channel)
            channel.confirm_delivery()
            channel.basic_publish(
                exchange=self.config.exchange,
                routing_key="work",
                body=message.encode(),
                properties=pika.BasicProperties(
                    content_type="application/json", delivery_mode=2, type="proposal"
                ),
                mandatory=True,
            )

    def consume(self, handler: Callable[[ProposalMessage], None]) -> None:
        with self._connection() as connection:
            channel = connection.channel()
            self._declare(channel)
            channel.basic_qos(prefetch_count=self.config.prefetch)

            def callback(ch, method, _properties, body):
                try:
                    message = ProposalMessage.decode(body)
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                    return
                try:
                    handler(message)
                except Exception:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                    return
                ch.basic_ack(delivery_tag=method.delivery_tag)

            channel.basic_consume(queue=self.config.work_queue, on_message_callback=callback)
            channel.start_consuming()


class InMemoryBroker:
    """Small deterministic adapter for embedders and unit tests."""

    def __init__(self):
        self.messages: list[ProposalMessage] = []

    def publish(self, message: ProposalMessage) -> None:
        self.messages.append(message)


def publish_proposal_event(proposal_id: int, event: str) -> bool:
    config = MQConfig.from_env()
    if config is None:
        return False
    PikaBroker(config).publish(ProposalMessage(proposal_id=proposal_id, event=event))
    return True
