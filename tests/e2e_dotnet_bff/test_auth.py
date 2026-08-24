"""认证端点 e2e（双栈 BFF register/login/me）。

针对运行中的 .NET BFF（端口 18099，dev SQLite）验证：
- POST /api/auth/register 不再触发 SQLite NOT NULL bug（regression guard for
  commit 88fc556 incomplete fix: `users.updated_at` / `users.row_version`
  NOT NULL constraint after EnsureCreated）。
- POST /api/auth/login 走 PBKDF2 验证 + 颁发 v1 HMAC token。
- GET /api/auth/me 用 bearer token 解析当前用户。
- /api/auth/login 错误密码 → 422；/api/auth/me 无 token → 401。

运行模式同 conftest：连 AGENTBOARD_BFF_URL（默认 18099），不可达 skip。
E2E_SPINUP=1 时自拉起临时空 SQLite。
"""
from __future__ import annotations

import re
import time
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.e2e]

V1_TOKEN_RE = re.compile(r"^v1\.\d+\.\d+\.[0-9a-f]+$")


def _new_user():
    suffix = uuid.uuid4().hex[:8]
    return f"e2e_{suffix}", f"E2ePass_{suffix}"


def test_register_returns_201_and_v1_token(bff_client: httpx.Client):
    username, password = _new_user()
    r = bff_client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 201, f"register 应 201，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert body["username"] == username
    assert body["id"] > 0
    assert V1_TOKEN_RE.match(body["token"]), f"token 格式不符合 v1.<uid>.<exp>.<sig>: {body['token']!r}"


def test_register_then_login_then_me_round_trip(bff_client: httpx.Client):
    username, password = _new_user()

    reg = bff_client.post("/api/auth/register", json={"username": username, "password": password})
    assert reg.status_code == 201, reg.text

    login = bff_client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["token"]
    assert V1_TOKEN_RE.match(token)

    me = bff_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["username"] == username
    # Regression guard: ensure updated_at was populated by the DB default
    # (HasDefaultValueSql("CURRENT_TIMESTAMP")) — would be default(DateTime)
    # if the column were NOT NULL but the INSERT forgot to set it.
    assert "updated_at" in me_body and me_body["updated_at"]


def test_register_duplicate_returns_409(bff_client: httpx.Client):
    username, password = _new_user()
    first = bff_client.post("/api/auth/register", json={"username": username, "password": password})
    assert first.status_code == 201, first.text
    second = bff_client.post("/api/auth/register", json={"username": username, "password": password})
    # FastAPI returns 400; .NET BFF returns 409 (per AuthController.ProducesResponseType
    # 409 for register). Either way the contract is "client error, not 500".
    assert second.status_code in (400, 409), (
        f"重复 register 应 4xx, 实际 {second.status_code}: {second.text}"
    )


def test_register_short_password_returns_422(bff_client: httpx.Client):
    r = bff_client.post(
        "/api/auth/register",
        json={"username": f"e2e_short_{uuid.uuid4().hex[:6]}", "password": "abc"},
    )
    assert r.status_code == 422, r.text


def test_login_wrong_password_returns_422(bff_client: httpx.Client):
    username, password = _new_user()
    reg = bff_client.post("/api/auth/register", json={"username": username, "password": password})
    assert reg.status_code == 201, reg.text

    bad = bff_client.post("/api/auth/login", json={"username": username, "password": "WRONG"})
    assert bad.status_code == 422, bad.text


def test_me_without_token_returns_401(bff_client: httpx.Client):
    r = bff_client.get("/api/auth/me")
    assert r.status_code == 401, r.text


def test_me_with_garbage_token_returns_401(bff_client: httpx.Client):
    r = bff_client.get("/api/auth/me", headers={"Authorization": "Bearer v1.0.0.deadbeef"})
    assert r.status_code == 401, r.text


def test_register_persists_user_with_default_updated_at(bff_client: httpx.Client):
    """Regression: UserConfiguration has HasDefaultValueSql('CURRENT_TIMESTAMP')
    on updated_at; without it, every register would 500 (SQLite NOT NULL).
    This test confirms the row landed and the column auto-populated."""
    username, _ = _new_user()
    reg = bff_client.post("/api/auth/register", json={"username": username, "password": "TestPass1234"})
    assert reg.status_code == 201, reg.text

    # /api/auth/users isn't exposed, but /api/auth/me (with the returned token)
    # shows the persisted row including the server-set updated_at.
    session = reg.json()
    me = bff_client.get("/api/auth/me", headers={"Authorization": f"Bearer {session['token']}"})
    assert me.status_code == 200
    body = me.json()
    assert body["id"] == session["id"]
    # updated_at should be a parseable timestamp not equal to default(DateTime)
    assert body["updated_at"].startswith("202") or body["updated_at"].startswith("197")  # current decade OR default
    # If it's default(DateTime) ('0001-01-01T00:00:00' / '1970-...'), the regression is back.
    if body["updated_at"].startswith("0001") or body["updated_at"].startswith("1970"):
        pytest.fail(
            f"updated_at 没被 DB 默认填上 → UserConfiguration.HasDefaultValueSql 失效: {body['updated_at']}"
        )
