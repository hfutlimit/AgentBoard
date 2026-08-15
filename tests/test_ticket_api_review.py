"""Proposal → Ticket 端点 review 修复验证（2026-08-09 GPT review 专项）。

覆盖：
- 高2：execute-by-id 不再传 type=""（从 request 取类型），正常执行 200；
- 高3：execute / fail / claim 按 id 操作时校验 rid 属于 URL proposal
  （跨 Proposal 404，防数据破坏）；
- 中1：全局 /api/ticket-requests/pending 与 /reclaim-stale 在 REQUIRE_AUTH=1
  下仅 admin 可访问（非 admin 403）；
- 中2：ticket_preparing 期间编辑提案正文 → 回退 pending 且未完成请求 failed。

运行：PYTHONPATH=. python -m pytest tests/test_ticket_api_review.py -q
"""
import os
import sys
import tempfile

# 独立临时数据库 + 强制重载 agentboard（engine 绑定临时库）
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"
os.environ["AGENTBOARD_REQUIRE_AUTH"] = "0"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from fastapi.testclient import TestClient

from agentboard import api, service
from agentboard.database import SessionLocal, init_db

init_db()

client = TestClient(api.app)


def _mk_converged(s, project_id, title="Review P"):
    """建一个 converged + converged_spec 的提案。"""
    pr = service.create_proposal(s, project_id=project_id, title=title,
                                 content="need clarity")
    service.set_proposal_status(s, pr.id, "queued")
    service.set_proposal_status(s, pr.id, "analyzing")
    service.set_proposal_status(s, pr.id, "converged")
    service.update_proposal(s, pr.id, converged_spec="# 需求\n- [ ] 子任务一")
    return pr.id


def _seed():
    with SessionLocal() as s:
        u = service.register_user(s, username="review-admin", password="password123")
        u.is_admin = True
        s.commit()
        p1 = service.create_project(s, name="RP1", key="RP1")
        service.add_project_member(s, project_id=p1.id, user_id=u.id, role="owner")
        p2 = service.create_project(s, name="RP2", key="RP2")
        service.add_project_member(s, project_id=p2.id, user_id=u.id, role="owner")
        e1 = service.create_epic(s, project_id=p1.id, title="RE1")
        e2 = service.create_epic(s, project_id=p2.id, title="RE2")
        st1 = service.create_story(s, epic_id=e1.id, title="RS1")
        pr1 = _mk_converged(s, p1.id, "RP-A")
        pr2 = _mk_converged(s, p2.id, "RP-B")
        # pr1 的 task 类型请求（execute-by-id 正常测试用）
        rid1 = service.create_ticket_request(
            s, pr1, type="task", epic_id=e1.id, story_id=st1.id,
        ).id
        # pr2 的 epic 请求（跨 proposal 测试用：URL=pr1 + rid2 → 404）
        rid2 = service.create_ticket_request(s, pr2, type="epic").id
        return p1.id, p2.id, pr1, pr2, rid1, rid2


_SEED = None
_ADMIN_TOK = None


def _seed_once():
    global _SEED, _ADMIN_TOK
    if _SEED is None:
        _SEED = _seed()
        r = client.post("/api/auth/login",
                        json={"username": "review-admin", "password": "password123"})
        assert r.status_code == 200, r.text
        _ADMIN_TOK = r.json()["token"]
    return _SEED


def _h():
    return {"Authorization": f"Bearer {_ADMIN_TOK}"}


# ---------- 高2：execute-by-id 正常执行（类型从 request 取） ----------

def test_execute_by_id_uses_request_type():
    p1, p2, pr1, pr2, rid1, rid2 = _seed_once()
    rid = rid1
    r = client.post(f"/api/proposals/{pr1}/ticket-requests/{rid}/execute",
                    headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticket"]["type"] == "dev"  # Story 265 后任务类型 task→dev（ticket 记录 type 仍为 task）
    assert body["proposal"]["status"] == "ticket_created"
    assert body["request"]["status"] == "done"
    # 幂等重放（已 done）
    r2 = client.post(f"/api/proposals/{pr1}/ticket-requests/{rid}/execute",
                     headers=_h())
    assert r2.status_code == 200, r2.text
    assert r2.json()["ticket"]["id"] == body["ticket"]["id"]


# ---------- 高3：跨 Proposal 归属校验 ----------

def test_execute_by_id_cross_proposal_404():
    p1, p2, pr1, pr2, rid1, rid2 = _seed_once()
    rid = rid2  # 属于 pr2，用 pr1 的 URL 操作 → 404
    r = client.post(f"/api/proposals/{pr1}/ticket-requests/{rid}/execute",
                    headers=_h())
    assert r.status_code == 404, r.text
    with SessionLocal() as s:
        req = service.get_ticket_request(s, rid)
        assert req.proposal_id == pr2, "请求应仍属于其原始 proposal"
        assert req.status == "pending", "请求状态不应被改动"


def test_fail_by_id_cross_proposal_404():
    p1, p2, pr1, pr2, rid1, rid2 = _seed_once()
    rid = rid2  # 属于 pr2，用 pr1 的 URL 操作 → 404
    r = client.post(f"/api/proposals/{pr1}/ticket-requests/{rid}/fail",
                    json={"error": "x"}, headers=_h())
    assert r.status_code == 404, r.text
    with SessionLocal() as s:
        assert service.get_ticket_request(s, rid).status == "pending"


def test_claim_by_id_cross_proposal_404():
    p1, p2, pr1, pr2, rid1, rid2 = _seed_once()
    rid = rid2  # 属于 pr2，用 pr1 的 URL 操作 → 404
    r = client.post(f"/api/proposals/{pr1}/ticket-requests/{rid}/claim",
                    headers=_h())
    assert r.status_code == 404, r.text
    with SessionLocal() as s:
        assert service.get_ticket_request(s, rid).status == "pending"


# ---------- 中1：全局端点 admin-only（REQUIRE_AUTH=1）+ URL 命名统一（2026-08-10） ----------

def _login(username, password):
    r = client.post("/api/auth/login",
                    json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_pending_and_reclaim_require_admin(monkeypatch):
    monkeypatch.setenv("AGENTBOARD_REQUIRE_AUTH", "1")
    # 注册非 admin 用户
    r = client.post("/api/auth/register",
                    json={"username": "review-user", "password": "password123"})
    assert r.status_code in (200, 201), r.text
    user_tok = _login("review-user", "password123")
    admin_tok = _login("review-admin", "password123")

    h_user = {"Authorization": f"Bearer {user_tok}"}
    h_admin = {"Authorization": f"Bearer {admin_tok}"}

    # 新命名空间（admin-only）：非 admin → 403，admin → 200
    assert client.get("/api/admin/ticket-requests/pending",
                      headers=h_user).status_code == 403
    assert client.post("/api/admin/ticket-requests/reclaim-stale", json={},
                       headers=h_user).status_code == 403
    assert client.get("/api/admin/ticket-requests/pending",
                      headers=h_admin).status_code == 200
    assert client.post("/api/admin/ticket-requests/reclaim-stale", json={},
                       headers=h_admin).status_code == 200

    # 兼容层（deprecated）：非 admin → 403；admin → pending 301 到新 URL
    assert client.get("/api/ticket-requests/pending", headers=h_user).status_code == 403
    assert client.post("/api/ticket-requests/reclaim-stale", json={},
                       headers=h_user).status_code == 403
    r301 = client.get("/api/ticket-requests/pending", headers=h_admin,
                      follow_redirects=False)
    assert r301.status_code == 301, r301.text
    assert "/api/admin/ticket-requests/pending" in r301.headers.get("location", "")
    assert client.post("/api/ticket-requests/reclaim-stale", json={},
                       headers=h_admin).status_code == 200


# ---------- 中2：ticket_preparing 编辑回退 + 取消未完成请求 ----------

def test_edit_during_ticket_preparing_cancels_requests():
    p1, p2, pr1, pr2, rid1, rid2 = _seed_once()
    # 再造一个请求（当前 pr1 的 rid 已被 execute 为 done——用新提案）
    with SessionLocal() as s:
        pr3 = _mk_converged(s, p1, "RP-C")
        rid3 = service.create_ticket_request(
            s, pr3, type="epic",
        ).id
        # 编辑正文（ticket_preparing 期间）
        service.update_proposal(s, pr3, content="被并发修改")
        p = service.get_proposal(s, pr3)
        assert p.status == "pending", "生成中编辑应回退 pending"
        req = service.get_ticket_request(s, rid3)
        assert req.status == "failed", "未完成请求应被取消"
        assert "取消" in req.error
