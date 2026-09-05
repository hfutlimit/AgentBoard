"""Publisher-confirmed relay. It does not generate work or choose Agents."""
import json
import os
from datetime import timedelta
from sqlalchemy import or_
from ...core.common.models import utc_now
from ...core.infrastructure import database
from .worker_work import EXCHANGE, enabled, queue_name
from .worker_work_models import WorkerWork


def drain_once():
    if not enabled():
        return 0
    import pika
    now = utc_now()
    with database.SessionLocal() as s:
        rows = s.query(WorkerWork).filter(or_(
            (WorkerWork.state == "available") & WorkerWork.published_at.is_(None),
            (WorkerWork.state == "leased") & (WorkerWork.lease_until < now)
            & (WorkerWork.published_at < now - timedelta(minutes=3))
        )).order_by(WorkerWork.id).limit(100).all()
        if not rows:
            return 0
        url = os.getenv("AGENTBOARD_MQ_URL", "")
        if not url:
            raise RuntimeError("Worker-owned relay requires AGENTBOARD_MQ_URL")
        parameters = pika.URLParameters(url)
        parameters.socket_timeout = 5
        parameters.blocked_connection_timeout = 5
        connection = pika.BlockingConnection(parameters)
        try:
            channel = connection.channel()
            channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)
            channel.exchange_declare(exchange=EXCHANGE + ".dlx", exchange_type="fanout", durable=True)
            channel.queue_declare(queue=EXCHANGE + ".dead", durable=True)
            channel.queue_bind(queue=EXCHANGE + ".dead", exchange=EXCHANGE + ".dlx")
            channel.confirm_delivery()
            for row in rows:
                queue = queue_name(row.project_id, row.kind, row.target_agent)
                channel.queue_declare(queue=queue, durable=True,
                    arguments={"x-dead-letter-exchange": EXCHANGE + ".dlx"})
                channel.queue_bind(queue=queue, exchange=EXCHANGE, routing_key=queue)
                channel.basic_publish(exchange=EXCHANGE, routing_key=queue, mandatory=True,
                    properties=pika.BasicProperties(delivery_mode=2, content_type="application/json", message_id=str(row.id)),
                    body=json.dumps({"schema": "worker-work.v2", "work_id": row.id,
                                     "project_id": row.project_id, "kind": row.kind,
                                     "target_agent": row.target_agent}).encode())
                row.published_at = now
            # Crash between confirm and commit redelivers; the claim/result
            # transaction, not a broker ACK, decides whether work can execute.
            s.commit()
            return len(rows)
        finally:
            connection.close()
