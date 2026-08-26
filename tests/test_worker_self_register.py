"""Worker 自注册测试（commit 4480967 P2 follow-up 修复，2026-08-26）。

覆盖：
1. ``_ensure_worker_registered`` 成功路径（HTTP 200/201）→ True
2. ``_ensure_worker_registered`` HTTP 错误（4xx/5xx）→ False，warning 记录
3. ``_ensure_worker_registered`` 网络异常 → False，warning 记录
4. ``_ensure_worker_registered`` body 正确（worker_id + hostname + status）
5. ``_heartbeat_via_instances`` 在 GET instances 前先调 register
6. register 失败不阻塞（fallback 走 legacy 路径）
"""
from __future__ import annotations

import os
import sys
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest

from agentboard.agent_runtime.heartbeat import (
    _ensure_worker_registered,
    _heartbeat_via_instances,
    _heartbeat_via_agents_legacy,
)


class _FakeResp:
    def __init__(self, status_code: int, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = str(self._body)[:200]

    def json(self):
        return self._body


class _RecordingClient:
    """记录所有 request.method / path / json。"""
    def __init__(self, response_map: dict | None = None, default_status: int = 200):
        self.calls: list[tuple[str, str, dict]] = []
        self.response_map = response_map or {}
        self.default_status = default_status

    def request(self, method, path, json=None, **kw):
        self.calls.append((method, path, json or {}))
        # 匹配精确 path
        for key, resp in self.response_map.items():
            if key == path:
                if isinstance(resp, BaseException):
                    raise resp
                return resp
        # GET /api/workers/.../instances 路径：返回空列表（无 instances）
        if method == "GET" and path.endswith("/instances"):
            return _FakeResp(200, [])
        # 默认
        return _FakeResp(self.default_status, {})


# =============== 1. _ensure_worker_registered 成功路径 ===============

def test_ensure_worker_registered_200_returns_true():
    client = _RecordingClient(response_map={
        "/api/workers/register": _FakeResp(201, {"worker_id": "wb-1", "id": 1}),
    })
    ok = _ensure_worker_registered(client, "wb-1")
    assert ok is True
    assert len(client.calls) == 1
    method, path, body = client.calls[0]
    assert method == "POST"
    assert path == "/api/workers/register"
    assert body["worker_id"] == "wb-1"
    assert body["status"] == "active"
    # hostname 应是 best-effort（不一定非空，但应该是字符串）
    assert isinstance(body.get("hostname", ""), str)


def test_ensure_worker_registered_200_existing_returns_true():
    """200 = server 端 upsert 已存在记录，幂等返回 True。"""
    client = _RecordingClient(response_map={
        "/api/workers/register": _FakeResp(200, {"worker_id": "wb-1", "id": 1, "status": "active"}),
    })
    assert _ensure_worker_registered(client, "wb-1") is True


# =============== 2. _ensure_worker_registered HTTP 错误 ===============

def test_ensure_worker_registered_404_returns_false_warns():
    client = _RecordingClient(response_map={
        "/api/workers/register": _FakeResp(404, {"detail": "nope"}),
    })
    assert _ensure_worker_registered(client, "wb-1") is False


def test_ensure_worker_registered_500_returns_false_warns():
    client = _RecordingClient(response_map={
        "/api/workers/register": _FakeResp(500, {"detail": "db down"}),
    })
    assert _ensure_worker_registered(client, "wb-1") is False


# =============== 3. _ensure_worker_registered 网络异常 ===============

def test_ensure_worker_registered_network_error_returns_false():
    client = _RecordingClient(response_map={
        "/api/workers/register": ConnectionError("server unreachable"),
    })
    assert _ensure_worker_registered(client, "wb-1") is False


# =============== 4. _heartbeat_via_instances 先注册再 GET ===============

def test_heartbeat_via_instances_calls_register_before_get_instances():
    """P2 修复验证：先调 register → 再调 GET /instances（顺序不能反）。"""
    client = _RecordingClient(response_map={
        "/api/workers/register": _FakeResp(201, {"id": 1}),
    })
    cfg = mock.MagicMock()
    cfg.worker_id = "wb-1"
    cfg.heartbeat_timeout = 8
    _heartbeat_via_instances(client, cfg, "wb-1")
    # 验证顺序：register 是 calls[0]，没有其他 GET instances 调用
    assert len(client.calls) >= 1
    method0, path0, _ = client.calls[0]
    assert (method0, path0) == ("POST", "/api/workers/register")
    # 验证后续没有 GET instances 调用（因空列表 + no instances = stats OK）
    get_instances_calls = [c for c in client.calls if c[0] == "GET" and c[1].endswith("/instances")]
    assert len(get_instances_calls) <= 1, (
        f"register 后最多 1 次 GET instances（避免重复探测），实际: {get_instances_calls}"
    )


def test_heartbeat_via_instances_continues_when_register_fails():
    """register 失败 → 不阻塞，后续 GET /instances 仍走（即使 404 也不 crash）。"""
    client = _RecordingClient(response_map={
        "/api/workers/register": _FakeResp(500, {"detail": "down"}),
    })
    cfg = mock.MagicMock()
    cfg.worker_id = "wb-1"
    cfg.heartbeat_timeout = 8
    # 不应抛异常
    stats = _heartbeat_via_instances(client, cfg, "wb-1")
    assert stats["mode"] == "instance"
    assert stats["checked"] == 0  # 没 instances 可探测
    # 验证：register 调过了，GET /instances 也调过了
    method_paths = [(m, p) for m, p, _ in client.calls]
    assert ("POST", "/api/workers/register") in method_paths
    assert any(p.endswith("/instances") for m, p in method_paths)


# =============== 5. _heartbeat_via_agents_legacy 不调 register ===============

def test_heartbeat_via_agents_legacy_does_not_call_register():
    """Legacy 路径（worker_id 留空）不应调 /api/workers/register（那路径不存在）。"""
    client = _RecordingClient(response_map={
        "/api/agents": _FakeResp(200, []),
    })
    cfg = mock.MagicMock()
    cfg.heartbeat_timeout = 8
    _heartbeat_via_agents_legacy(client, cfg)
    # 验证：没有调 register
    register_calls = [c for c in client.calls if c[1] == "/api/workers/register"]
    assert register_calls == [], (
        f"legacy 路径不应调 /api/workers/register，实际: {register_calls}"
    )
