"""BFF 健康与枚举契约 e2e（新后端 .NET BFF / Story #313 / S0-7）。

- /api/health 形状与 FastAPI 一致（S0-5 契约）。
- /api/meta 枚举值与 FastAPI enums.py 对齐（#5 / #311 契约；已 R4 核验为 no-op）。
- 若 AGENTBOARD_FASTAPI_URL 指向运行中的 FastAPI，则额外断言双栈 meta 完全一致
  （双栈契约冻结的运行时校验）；否则仅校验 BFF 侧规范值。
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

# 规范枚举（与 FastAPI enums.py / MetaController.cs:25-30 一致，见 #311 评审闭环）
EXPECTED_META = {
    "types": ["dev", "bug", "qa", "design"],
    "statuses": ["todo", "in_progress", "in_review", "done", "blocked"],
    "priorities": ["highest", "high", "medium", "low", "lowest"],
    "sprint_statuses": ["planning", "active", "completed"],
    "schedule_types": ["once", "cron"],
    "run_statuses": ["pending", "running", "success", "failed", "cancelled"],
}


@pytest.mark.e2e
def test_health_shape(bff_client: httpx.Client):
    r = bff_client.get("/api/health")
    assert r.status_code == 200, f"/api/health 应 200，实际 {r.status_code}: {r.text}"
    body = r.json()
    for key in ("status", "database", "version", "timestamp"):
        assert key in body, f"health 响应缺字段 {key}"
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    # timestamp 必须是可解析的 ISO8601（UTC 'Z' 结尾）
    assert body["timestamp"].endswith("Z"), f"timestamp 应为 UTC ISO: {body['timestamp']}"


@pytest.mark.e2e
def test_meta_contract(bff_client: httpx.Client):
    r = bff_client.get("/api/meta")
    assert r.status_code == 200, f"/api/meta 应 200，实际 {r.status_code}: {r.text}"
    body = r.json()
    for key, expected in EXPECTED_META.items():
        assert key in body, f"meta 缺字段 {key}"
        assert body[key] == expected, f"meta.{key} 应为 {expected}，实际 {body[key]}"


@pytest.mark.e2e
def test_meta_parity_with_fastapi(bff_client: httpx.Client):
    """双栈契约：若 FastAPI 在跑，BFF meta 必须与其完全一致。"""
    import os

    fastapi_url = os.environ.get("AGENTBOARD_FASTAPI_URL")
    if not fastapi_url:
        pytest.skip("未设置 AGENTBOARD_FASTAPI_URL，跳过双栈 meta 一致性校验")

    try:
        fr = httpx.get(fastapi_url.rstrip("/") + "/api/meta", timeout=10)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"FastAPI 不可达（{fastapi_url}）：{e}")

    if fr.status_code != 200:
        pytest.skip(f"FastAPI /api/meta 返回 {fr.status_code}，跳过双栈校验")

    bff = bff_client.get("/api/meta").json()
    fast = fr.json()
    for key in EXPECTED_META:
        assert bff.get(key) == fast.get(key), (
            f"双栈 meta.{key} 不一致：BFF={bff.get(key)} FastAPI={fast.get(key)}"
        )
