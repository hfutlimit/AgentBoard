"""项目写操作 e2e（双栈重构 P2b）。

针对运行中的 .NET BFF（端口 18099，真实 agentboard.db）验证项目创建/更新/删除
与 FastAPI projects router 1:1 对齐：路由布局、状态码、校验语义、is_private 强制。

运行模式（同 test_comments.py）：
- 默认：连 AGENTBOARD_BFF_URL（运行中实例，真实库含 FastAPI 共享表）；不可达 skip。
- E2E_SPINUP=1：自拉起临时空 SQLite，无 FastAPI 拥有的共享表，取不到种子 id 而 skip。
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def _unique_key() -> str:
    # 唯一 key，避免跨用例 / 重跑时的 DuplicateException。
    return "E2E" + uuid.uuid4().hex[:10].upper()


def _create_project(client, *, key=None, name="e2e-project", description="e2e desc"):
    body = {"name": name, "description": description}
    if key is not None:
        body["key"] = key
    return client.post("/api/projects", json=body)


def test_post_project_returns_201_and_forces_private(bff_client):
    r = _create_project(bff_client, key=_unique_key())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] > 0
    assert body["name"] == "e2e-project"
    assert body["is_private"] is True  # FastAPI 强制邀请制
    assert body["is_archived"] is False
    assert "created_at" in body
    bff_client.delete(f"/api/projects/{body['id']}")


def test_post_project_missing_name_422(bff_client):
    r = bff_client.post("/api/projects", json={"key": _unique_key()})
    assert r.status_code == 422, r.text


def test_post_project_name_too_long_422(bff_client):
    r = bff_client.post("/api/projects", json={"name": "x" * 201})
    assert r.status_code == 422, r.text


def test_post_project_key_too_long_422(bff_client):
    r = bff_client.post("/api/projects", json={"name": "ok", "key": "y" * 21})
    assert r.status_code == 422, r.text


def test_post_project_duplicate_key_409(bff_client):
    key = _unique_key()
    first = _create_project(bff_client, key=key)
    assert first.status_code == 201, first.text
    pid = first.json()["id"]
    try:
        second = _create_project(bff_client, key=key)
        assert second.status_code == 409, second.text
    finally:
        bff_client.delete(f"/api/projects/{pid}")


def test_patch_project_updates_fields(bff_client):
    key = _unique_key()
    create = _create_project(bff_client, key=key)
    assert create.status_code == 201, create.text
    pid = create.json()["id"]
    try:
        r = bff_client.patch(
            f"/api/projects/{pid}",
            json={
                "name": "e2e-renamed",
                "description": "updated desc",
                "is_private": False,
                "is_archived": True,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "e2e-renamed"
        assert body["description"] == "updated desc"
        assert body["is_private"] is False
        assert body["is_archived"] is True
        assert body["key"] == key
        # is_archived 写入后 archived_at 应被填充（_ser 不返回该字段，仅校验可还原）
        restored = bff_client.patch(f"/api/projects/{pid}", json={"is_archived": False})
        assert restored.status_code == 200
        assert restored.json()["is_archived"] is False
    finally:
        bff_client.delete(f"/api/projects/{pid}")


def test_patch_project_not_found_404(bff_client):
    r = bff_client.patch("/api/projects/999999999", json={"name": "ghost"})
    assert r.status_code == 404, r.text


def test_delete_project_ok_then_404(bff_client):
    key = _unique_key()
    create = _create_project(bff_client, key=key)
    assert create.status_code == 201, create.text
    pid = create.json()["id"]
    d1 = bff_client.delete(f"/api/projects/{pid}")
    assert d1.status_code == 200, d1.text
    assert d1.json().get("ok") is True
    d2 = bff_client.delete(f"/api/projects/{pid}")
    assert d2.status_code == 404, d2.text
