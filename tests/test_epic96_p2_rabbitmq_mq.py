"""Epic 96 P2-1 — RabbitMQ 消息总线接入回归护栏。

覆盖 Task 934（RabbitMQ 消息总线：发布钩子 + 竞争消费 + DLQ + 超时回退重投）：

1. **纯函数层**：``ProposalMessage`` 载荷校验（毒消息一律拒收）、内存 broker 的
   死信语义。不需要 broker，始终可跑。
2. **真 broker 层**：在专用 ``agentboard-rabbitmq`` 容器上跑拓扑声明→发布→竞争消费
   →ack，以及毒消息自动进死信队列。每个测试用唯一命名空间，绝不影响其它项目队列。
3. **降级层**：未配 ``AGENTBOARD_MQ_URL`` 时整体回退轮询（broker=None，发布静默 no-op）；
   broker 不可达时发布失败仅返回 False，绝不抛异常影响 REST。
4. **端到端闭环层**：拉起真实 uvicorn 子进程 + 把 ``AGENTBOARD_MQ_URL``/命名空间注入
   其环境，经 REST 把提案置为 ``queued``，再从同一 broker 命名空间消费到派发消息——
   验证「API 发布钩子 → broker → 消费者」整条链路真的打通。

运行::

    PYTHONPATH=. python -m pytest tests/test_epic96_p2_rabbitmq_mq.py -q

完全自包含：不依赖、不触碰 18001 上的 MCP 容器，broker 走独立的 28672 端口。
若测试机没有可达的 RabbitMQ，真 broker 用例自动 skip，纯函数/降级用例仍照常跑。
"""
import os
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentboard import mq  # noqa: E402

# 测试 broker：优先读环境变量，默认指向本机专用 agentboard-rabbitmq 容器（28672/5672）。
BROKER_URL = os.getenv(
    "AGENTBOARD_MQ_URL_TEST",
    "amqp://agentboard:agentboard_dev_pass@127.0.0.1:28672/%2F",
)


def _broker_reachable() -> bool:
    try:
        import pika  # noqa: PLC0415

        params = pika.URLParameters(BROKER_URL)
        params.socket_timeout = 2.0
        conn = pika.BlockingConnection(params)
        conn.close()
        return True
    except Exception:
        return False


HAVE_BROKER = _broker_reachable()


@pytest.fixture
def real_broker():
    """真 broker 上的唯一命名空间 broker，自动清理。"""
    if not HAVE_BROKER:
        pytest.skip("RabbitMQ 测试 broker 不可达（设置 AGENTBOARD_MQ_URL_TEST 可启用）")
    cfg = mq.MQConfig(
        url=BROKER_URL, namespace=mq.unique_namespace(), connect_timeout=3.0
    )
    b = mq.PikaBroker(cfg)
    b.declare_topology()
    b.purge()
    try:
        yield b
    finally:
        try:
            b.purge()
        except Exception:  # pragma: no cover
            pass
        try:
            b.teardown()
        except Exception:  # pragma: no cover
            pass
        b.close()


# ===================== 第 1 层：纯函数 / 内存 broker =====================


def test_message_validation_rejects_poison_payloads():
    """非法载荷必须被严格拒绝（bool/None/负数/非整数/非 JSON/非对象）。"""
    bad = [
        b"",
        b"not-json",
        b"123",
        b'"a-string"',
        b"{}",
        '{"proposal_id": true}',
        '{"proposal_id": null}',
        '{"proposal_id": -1}',
        '{"proposal_id": 0}',
        '{"proposal_id": "abc"}',
    ]
    for raw in bad:
        with pytest.raises(mq.MQMessageError):
            mq.ProposalMessage.from_bytes(raw)


def test_message_roundtrip_and_strict_int():
    """合法消息可往返，且 proposal_id 必须是正整数（bool 不算）。"""
    msg = mq.ProposalMessage.from_bytes(
        b'{"proposal_id": 7, "round": 2, "reason": "queued", "ts": "t"}'
    )
    assert msg.proposal_id == 7
    assert msg.round == 2
    assert msg.reason == "queued"
    # 往返：to_bytes -> from_bytes 一致
    again = mq.ProposalMessage.from_bytes(msg.to_bytes())
    assert again.proposal_id == 7 and again.round == 2


def test_in_memory_broker_dlq_semantics():
    """内存 broker 也要复刻死信语义：毒消息被拒收后落入死信。"""
    b = mq.InMemoryBroker()
    b.declare_topology()
    b.publish(mq.ProposalMessage(proposal_id=1))
    b.publish_raw(b"not-json")  # 毒消息
    stats = b.consume(lambda m: True, max_messages=2, idle_timeout=1)
    assert stats["consumed"] == 2
    assert stats["acked"] == 1
    assert stats["dead"] == 1
    # 死信队列里正是那条毒消息字节
    assert b.dead_letters() == [b"not-json"]


def test_build_broker_returns_none_when_disabled():
    """未配置 URL → build_broker 返回 None（调用方降级轮询）。"""
    assert mq.MQConfig(url="").enabled is False
    assert mq.build_broker(mq.MQConfig(url="")) is None
    with pytest.MonkeyPatch().context() as mp:
        mp.delenv("AGENTBOARD_MQ_URL", raising=False)
        assert mq.build_broker() is None


def test_publish_proposal_event_is_noop_when_disabled():
    """未启用时 publish_proposal_event 是静默 no-op，不改变既有行为。"""
    pub = mq.ProposalPublisher(config=mq.MQConfig(url=""))
    assert pub.enabled is False
    assert pub.publish(1) is False
    mq.set_publisher(mq.ProposalPublisher(config=mq.MQConfig(url="")))
    try:
        assert mq.publish_proposal_event(1) is False
    finally:
        mq.set_publisher(None)


def test_publisher_resilient_to_broker_down():
    """broker 不可达：发布失败仅返回 False，绝不抛异常。"""
    cfg = mq.MQConfig(
        url="amqp://nope:nope@127.0.0.1:1/%2F", connect_timeout=0.5
    )
    pub = mq.ProposalPublisher(config=cfg)
    # 连接 port 1 会被立即 RST，应快速失败且返回 False，不抛
    assert pub.publish(1) is False


def test_publisher_injected_broker_receives():
    """注入 broker 时发布路径真的把消息写进去（不依赖真 broker）。"""
    mem = mq.InMemoryBroker()
    pub = mq.ProposalPublisher(config=mq.MQConfig(url="memory://"), broker=mem)
    assert pub.enabled is True
    assert pub.publish(42, 3, mq.REASON_QUEUED) is True
    assert mem.published == 1
    assert mq.ProposalMessage.from_bytes(mem._queue[0]).proposal_id == 42


# ===================== 第 2 层：真 broker =====================


def test_topology_publish_consume_ack(real_broker):
    """真 broker：声明→发布→消费→ack 全链路。"""
    real_broker.publish(mq.ProposalMessage(proposal_id=11, round=1, reason="queued"))
    assert real_broker.queue_depth() == 1
    got = []
    stats = real_broker.consume(
        lambda m: (got.append(m.proposal_id) or True),
        max_messages=1, idle_timeout=3,
    )
    assert stats == {"consumed": 1, "acked": 1, "dead": 0}, stats
    assert got == [11]
    assert real_broker.queue_depth() == 0


def test_dlq_routes_poison_message(real_broker):
    """毒消息（非 JSON）被拒收后自动经 DLX 落入死信队列。"""
    real_broker.publish_raw(b"not-json")
    stats = real_broker.consume(lambda m: True, max_messages=1, idle_timeout=3)
    assert stats["consumed"] == 1
    assert stats["acked"] == 0
    assert stats["dead"] == 1
    assert real_broker.queue_depth(dead=True) == 1
    assert real_broker.queue_depth() == 0


def test_competing_consumers_no_duplicate(real_broker):
    """两个消费者竞争同一队列：每条消息恰好被消费一次，无重复无丢失。"""
    n = 12
    for i in range(n):
        real_broker.publish(
            mq.ProposalMessage(proposal_id=1000 + i, round=0, reason="queued")
        )
    collected = []
    lock = threading.Lock()

    def consumer():
        cfg = mq.MQConfig(
            url=BROKER_URL, namespace=real_broker.config.namespace, connect_timeout=3.0
        )
        b = mq.PikaBroker(cfg)
        b.consume(
            lambda m: (collected.append(m.proposal_id) or True),
            max_messages=None, idle_timeout=2.0,
        )
        b.close()

    t1 = threading.Thread(target=consumer)
    t2 = threading.Thread(target=consumer)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(collected) == n, collected
    assert len(set(collected)) == n, "出现重复消费"


def test_end_to_end_dispatch_via_publisher_and_consumer(real_broker):
    """注入真 broker 的发布器 → 发布 → 同命名空间消费者收到，闭环自证。"""
    pub = mq.ProposalPublisher(config=real_broker.config, broker=real_broker)
    assert pub.publish(77, 2, mq.REASON_QUEUED) is True
    got = []
    stats = real_broker.consume(
        lambda m: (got.append(m) or True), max_messages=1, idle_timeout=4
    )
    assert stats["acked"] == 1, stats
    assert got and got[0].proposal_id == 77 and got[0].round == 2


# ===================== 第 4 层：REST → broker → 消费者 =====================


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int, extra_env: dict) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_ready(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(base + "/api/meta", timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"API 服务在 {base} 启动超时")


@pytest.fixture(scope="module")
def rest_mq_ctx():
    """拉起真实 API，并把 MQ 配置注入其环境，返回带鉴权头的上下文 + namespace。"""
    if not HAVE_BROKER:
        pytest.skip("RabbitMQ 测试 broker 不可达")
    import tempfile

    db = tempfile.mktemp(suffix=".db")
    ns = mq.unique_namespace()
    port = _free_port()
    proc = _start_server(
        port,
        {
            "AGENTBOARD_DB_URL": f"sqlite:///{db}",
            "AGENTBOARD_MCP_BACKEND": "db",
            "AGENTBOARD_MQ_URL": BROKER_URL,
            "AGENTBOARD_MQ_NAMESPACE": ns,
            "AGENTBOARD_MQ_CONNECT_TIMEOUT": "3",
        },
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        c = httpx.Client(base_url=base, timeout=30)
        r = c.post(
            "/api/auth/register",
            json={"username": "p96mq", "password": "p96mq123"},
        )
        assert r.status_code in (201, 409), r.text
        r = c.post(
            "/api/auth/login",
            json={"username": "p96mq", "password": "p96mq123"},
        )
        assert r.status_code == 200, r.text
        c.headers.update({"Authorization": f"Bearer {r.json()['token']}"})

        r = c.post("/api/projects", json={"name": "Epic96 P2 MQ 项目"})
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]
        r = c.post(f"/api/projects/{pid}/epics", json={"title": "MQ Epic"})
        assert r.status_code in (200, 201), r.text
        eid = r.json()["id"]
        r = c.post(f"/api/epics/{eid}/stories", json={"title": "MQ Story"})
        assert r.status_code in (200, 201), r.text
        sid = r.json()["id"]

        yield {
            "c": c, "project_id": pid, "epic_id": eid, "story_id": sid,
            "namespace": ns,
        }
        c.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # pragma: no cover
            proc.kill()
        # 清理 broker 命名空间，避免污染共享 broker
        try:
            b = mq.PikaBroker(mq.MQConfig(url=BROKER_URL, namespace=ns))
            b.purge()
            b.teardown()
            b.close()
        except Exception:  # pragma: no cover
            pass


def test_rest_dispatch_publishes_to_broker(rest_mq_ctx):
    """经 REST 把提案置为 queued，同命名空间消费者必须收到派发消息。"""
    c = rest_mq_ctx["c"]
    r = c.post(
        "/api/proposals",
        json={
            "project_id": rest_mq_ctx["project_id"],
            "title": "MQ 派发验证提案",
            "content": "置为 queued 后应经 MQ 推送",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    r = c.put(f"/api/proposals/{pid}/status", json={"status": "queued"})
    assert r.status_code == 200, r.text

    # 从注入的命名空间消费，应在超时内收到 proposal_id == pid 的派发消息
    b = mq.PikaBroker(
        mq.MQConfig(url=BROKER_URL, namespace=rest_mq_ctx["namespace"], connect_timeout=3.0)
    )
    got = []
    stats = b.consume(
        lambda m: (got.append(m) or True), max_messages=1, idle_timeout=8
    )
    b.close()
    assert stats["acked"] >= 1, stats
    assert any(m.proposal_id == pid for m in got), f"未收到提案 {pid} 的派发消息：{got}"


def test_rest_dispatch_silent_when_broker_down():
    """broker 不可达时，REST 置 queued 仍成功返回 200（MQ 故障绝不阻断主流程）。"""
    if not HAVE_BROKER:
        pytest.skip("需要 broker 探活，但此处专门验证『置 queued 不依赖 broker』")
    import tempfile

    ns = mq.unique_namespace()
    db = tempfile.mktemp(suffix=".db")
    port = _free_port()
    # 故意指向不可达 broker，但业务应照常
    proc = _start_server(
        port,
        {
            "AGENTBOARD_DB_URL": f"sqlite:///{db}",
            "AGENTBOARD_MCP_BACKEND": "db",
            "AGENTBOARD_MQ_URL": "amqp://nope:nope@127.0.0.1:1/%2F",
            "AGENTBOARD_MQ_NAMESPACE": ns,
            "AGENTBOARD_MQ_CONNECT_TIMEOUT": "1",
        },
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        c = httpx.Client(base_url=base, timeout=30)
        r = c.post(
            "/api/auth/register",
            json={"username": "p96mqx", "password": "p96mqx123"},
        )
        assert r.status_code in (201, 409), r.text
        r = c.post(
            "/api/auth/login",
            json={"username": "p96mqx", "password": "p96mqx123"},
        )
        assert r.status_code == 200, r.text
        c.headers.update({"Authorization": f"Bearer {r.json()['token']}"})

        r = c.post(
            "/api/projects", json={"name": "Epic96 P2 MQ-down 项目"}
        )
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]
        r = c.post(f"/api/projects/{pid}/epics", json={"title": "MQ-down Epic"})
        assert r.status_code in (200, 201), r.text
        eid = r.json()["id"]
        r = c.post(f"/api/epics/{eid}/stories", json={"title": "MQ-down Story"})
        assert r.status_code in (200, 201), r.text

        r = c.post(
            "/api/proposals",
            json={
                "project_id": pid, "title": "broker 宕机验证",
                "content": "MQ 挂了也不应影响提案流转",
            },
        )
        assert r.status_code == 201, r.text
        pid2 = r.json()["id"]
        r = c.put(f"/api/proposals/{pid2}/status", json={"status": "queued"})
        # 关键断言：broker 不可达，但 status 更新成功返回 200
        assert r.status_code == 200, r.text
        c.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # pragma: no cover
            proc.kill()
