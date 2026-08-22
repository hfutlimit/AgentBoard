"""BFF OpenAPI 契约 e2e —— Story #310 / S0-4 的 .NET 侧基线。

- Development 环境下 /openapi/v1.json 必须可服务（注意：非 /openapi.json，后者 404）。
- 暴露的 paths 至少包含 /api/health、/api/meta、/api/auth/login，
  与 FastAPI 的 /api/* 路径 1:1 对应（契约冻结要求的路由对齐）。
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

EXPECTED_PATHS = {"/api/health", "/api/meta", "/api/auth/login"}


@pytest.mark.e2e
def test_openapi_v1_served(bff_client: httpx.Client):
    """Development 下 OpenAPI 文档在 /openapi/v1.json 提供。"""
    # .NET 9+ MapOpenApi 默认文档名 v1 → /openapi/v1.json
    r = bff_client.get("/openapi/v1.json")
    assert r.status_code == 200, (
        f"/openapi/v1.json 应 200，实际 {r.status_code}。"
        f"若返回 404，说明 BFF 未以 Development 环境启动（MapOpenApi 仅在 Development 生效）"
    )
    doc = r.json()
    assert "paths" in doc, "openapi 文档缺 paths"
    # 反向确认 /openapi.json（无文档名）不提供服务
    alt = bff_client.get("/openapi.json")
    assert alt.status_code == 404, "/openapi.json（无文档名）应 404，避免调用方误用路径"


@pytest.mark.e2e
def test_openapi_paths_aligned(bff_client: httpx.Client):
    """BFF 路由必须与 FastAPI 的 /api/* 路径对齐（契约冻结前提）。"""
    r = bff_client.get("/openapi/v1.json")
    assert r.status_code == 200
    paths = set(r.json().get("paths", {}).keys())
    missing = EXPECTED_PATHS - paths
    assert not missing, f"OpenAPI 缺失与 FastAPI 对齐的路由: {missing}"
