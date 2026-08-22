"""BFF 跨栈关联（观测性）e2e —— Story #313 / S0-7 的可观测边界验证。

验证 RequestIdMiddleware 与 TraceContextMiddleware 在 HTTP 边界的真实行为
（2026-08-22 实测确认）：

1. X-Request-Id：入站携带则**原值回显**到响应头。这是 .NET → FastAPI 跨栈日志
   关联的关键键（TracePropagationDelegatingHandler 将其注入出站请求）。
2. traceparent（W3C）：入站携带则 BFF **续接同一 trace-id**（响应 trace-id 与入站
   一致、span-id 为新值），并回显到响应头——证明分布式追踪在边界被正确延续，
   而非另起一条新 trace。

注：出站「BFF→FastAPI 携带 traceparent/X-Request-Id」由 xUnit 单元测试
TracePropagationDelegatingHandlerTests 覆盖；本 e2e 验证其入站→响应这一可观测半环。
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.mark.e2e
def test_x_request_id_echoed(bff_client: httpx.Client):
    """入站 X-Request-Id 必须原值回显（跨栈关联键）。"""
    known = "e2e-known-reqid-0099"
    r = bff_client.get("/api/health", headers={"X-Request-Id": known})
    assert r.status_code == 200
    assert r.headers.get("X-Request-Id") == known, (
        f"X-Request-Id 未原值回显：入站 {known!r} 响应 {r.headers.get('X-Request-Id')!r}"
    )


@pytest.mark.e2e
def test_x_request_id_generated_when_absent(bff_client: httpx.Client):
    """未携带时 BFF 生成并返回 X-Request-Id（非空、可关联）。"""
    r = bff_client.get("/api/health")
    assert r.status_code == 200
    rid = r.headers.get("X-Request-Id")
    assert rid and len(rid) <= 128, f"应生成非空 X-Request-Id，实际 {rid!r}"


@pytest.mark.e2e
def test_traceparent_continued(bff_client: httpx.Client):
    """入站 W3C traceparent 必须被续接：响应 trace-id 与入站一致、span-id 为新值。"""
    inbound_tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    r = bff_client.get("/api/meta", headers={"traceparent": inbound_tp})
    assert r.status_code == 200

    resp_tp = r.headers.get("traceparent")
    assert resp_tp, f"响应应回显 traceparent，实际缺失（响应头: {dict(r.headers)}）"

    def _trace_id(tp: str) -> str:
        # 格式: version-traceid-spanid-flags
        parts = tp.split("-")
        assert len(parts) == 4, f"traceparent 格式非法: {tp}"
        return parts[1]

    assert _trace_id(resp_tp) == _trace_id(inbound_tp), (
        f"trace-id 未续接：入站 {inbound_tp} 响应 {resp_tp}"
    )
    # span-id 应变化（新子 span），证明是「续接」而非「原样透传」
    assert resp_tp != inbound_tp, "响应的 traceparent 不应与入站完全相同（应生成新 span-id）"


@pytest.mark.e2e
def test_traceparent_present_by_default(bff_client: httpx.Client):
    """Development 下 OpenTelemetry 活动存在，响应默认带 traceparent（可关联）。"""
    r = bff_client.get("/api/health")
    assert r.status_code == 200
    assert r.headers.get("traceparent"), "默认响应应携带 traceparent 以便链路关联"
