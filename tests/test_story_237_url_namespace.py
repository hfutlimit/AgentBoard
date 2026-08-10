"""Epic 123 Step 1 补全 — URL 命名空间统一（2026-08-10）测试。

覆盖（Ticket flow 端点重构 A 部分）：
1. 新 RPC 端点 ``POST /api/ticket-requests:execute``（body 带 proposal_id）
2. 统一动作端点 ``POST /api/ticket-requests/{rid}/{action}``（execute/fail/claim）
3. admin 命名空间 ``GET /api/admin/ticket-requests/pending`` /
   ``POST /api/admin/ticket-requests/reclaim-stale``
4. 兼容层：旧 URL 全部保留可用
   - ``GET /api/ticket-requests/pending`` → 301 → admin 新 URL
   - ``POST /api/ticket-requests/reclaim-stale`` → 内部转发
   - ``POST /api/proposals/{pid}/ticket-requests/execute-by-type`` → 转发 RPC
   - ``POST /api/proposals/{pid}/ticket-requests/{rid}/{action}`` → 转发统一动作
5. worker.py / mcp_server.py 调用点已切新 URL（源码静态断言）

运行：PYTHONPATH=. python -m pytest tests/test_story_237_url_namespace.py -q
"""
import itertools
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

import pytest
from fastapi.testclient import TestClient

from agentboard import api, service
from agentboard.database import SessionLocal, init_db

init_db()

client = TestClient(api.app)

# 每测试独立用户，避免「下线/共享 seed」顺序污染
_UID = itertools.count(1)
_ADMIN_TOK = {}


def _mk_converged(s, project_id, title="URL NS P"):
    """建一个 converged + converged_spec 的提案。"""
    pr = service.create_proposal(s, project_id=project_id, title=title,
                                 content="need clarity")
    service.set_proposal_status(s, pr.id, "queued")
    service.set_proposal_status(s, pr.id, "analyzing")
    service.set_proposal_status(s, pr.id, "converged")
    service.update_proposal(s, pr.id, converged_spec="# 需求\n- [ ] 子任务一")
    return pr.id


@pytest.fixture()
def ctx():
    """每测试独立建 admin 用户 + 项目 + epic + story + 2 个提案。"""
    n = next(_UID)
    uname = f"ns-admin-{n}"
    with SessionLocal() as s:
        u = service.register_user(s, username=uname, password="password123")
        u.is_admin = True
        s.commit()
        p1 = service.create_project(s, name=f"NS-P{n}", key=f"NSP{n}")
        service.add_project_member(s, project_id=p1.id, user_id=u.id, role="owner")
        e1 = service.create_epic(s, project_id=p1.id, title="NS-E")
        st1 = service.create_story(s, epic_id=e1.id, title="NS-S")
        # pr1: task 类型请求（用 epic_id + story_id）
        pr1 = _mk_converged(s, p1.id, "NS-RP-A")
        rid = service.create_ticket_request(
            s, pr1, type="task", epic_id=e1.id, story_id=st1.id,
        ).id
        # pr2: epic 类型请求（独立）
        pr2 = _mk_converged(s, p1.id, "NS-RP-B")
        rid2 = service.create_ticket_request(s, pr2, type="epic").id
        s.commit()
        ctx_data = {
            "project_id": p1.id, "epic_id": e1.id, "story_id": st1.id,
            "pr1": pr1, "pr2": pr2, "rid": rid, "rid2": rid2,
        }
    r = client.post("/api/auth/login",
                    json={"username": uname, "password": "password123"})
    assert r.status_code == 200, r.text
    ctx_data["h"] = {"Authorization": f"Bearer {r.json()['token']}"}
    return ctx_data


# ---------- 1. 新 RPC 端点 POST /api/ticket-requests:execute ----------

def test_rpc_execute_by_proposal_body(ctx):
    r = client.post("/api/ticket-requests:execute",
                    json={"proposal_id": ctx["pr2"], "type": "epic"},
                    headers=ctx["h"])
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["request"]["type"] == "epic"
    assert data["request"]["status"] == "done"
    assert data["ticket"]["title"] == "NS-RP-B"
    # proposal 已回填 ticket_id + ticket_created
    with SessionLocal() as s:
        p = service.get_proposal(s, ctx["pr2"])
        assert p.status == "ticket_created"
        assert p.ticket_id == data["ticket"]["id"]


def test_rpc_execute_404_unknown_proposal(ctx):
    r = client.post("/api/ticket-requests:execute",
                    json={"proposal_id": 999999, "type": "epic"},
                    headers=ctx["h"])
    assert r.status_code == 404, r.text


def test_rpc_execute_idempotent(ctx):
    body = {"proposal_id": ctx["pr1"], "type": "task",
            "epic_id": ctx["epic_id"], "story_id": ctx["story_id"]}
    r1 = client.post("/api/ticket-requests:execute", json=body, headers=ctx["h"])
    assert r1.status_code == 200, r1.text
    tid1 = r1.json()["ticket"]["id"]
    r2 = client.post("/api/ticket-requests:execute", json=body, headers=ctx["h"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["ticket"]["id"] == tid1  # 幂等复用


# ---------- 2. 统一动作端点 POST /api/ticket-requests/{rid}/{action} ----------

def test_unified_action_execute(ctx):
    r = client.post(f"/api/ticket-requests/{ctx['rid']}/execute", json={},
                    headers=ctx["h"])
    assert r.status_code == 200, r.text
    assert r.json()["request"]["status"] == "done"
    with SessionLocal() as s:
        assert service.get_ticket_request(s, ctx["rid"]).status == "done"


def test_unified_action_fail(ctx):
    r = client.post(f"/api/ticket-requests/{ctx['rid2']}/fail",
                    json={"error": "agent 放弃"}, headers=ctx["h"])
    assert r.status_code == 200, r.text
    with SessionLocal() as s:
        req = service.get_ticket_request(s, ctx["rid2"])
        assert req.status == "failed"
        p = service.get_proposal(s, ctx["pr2"])
        assert p.status == "converged", "失败应回退 converged"


def test_unified_action_claim_then_409(ctx):
    r = client.post(f"/api/ticket-requests/{ctx['rid2']}/claim", json={},
                    headers=ctx["h"])
    assert r.status_code == 200, r.text
    with SessionLocal() as s:
        assert service.get_ticket_request(s, ctx["rid2"]).status == "processing"
    # 二次认领 → 409
    r2 = client.post(f"/api/ticket-requests/{ctx['rid2']}/claim", json={},
                     headers=ctx["h"])
    assert r2.status_code == 409, r2.text


def test_unified_action_unknown_404(ctx):
    r = client.post(f"/api/ticket-requests/{ctx['rid']}/nonsense", json={},
                    headers=ctx["h"])
    assert r.status_code == 404, r.text


def test_unified_action_rid_404(ctx):
    r = client.post("/api/ticket-requests/999999/claim", json={}, headers=ctx["h"])
    assert r.status_code == 404, r.text


# ---------- 3. admin 命名空间 ----------

def test_admin_pending_and_reclaim(ctx):
    r = client.get("/api/admin/ticket-requests/pending", headers=ctx["h"])
    assert r.status_code == 200, r.text
    # rid（task 类型）尚未被认领 → 在 pending 池
    ids = [x["id"] for x in r.json()]
    assert ctx["rid"] in ids
    r2 = client.post("/api/admin/ticket-requests/reclaim-stale", json={},
                     headers=ctx["h"])
    assert r2.status_code == 200, r2.text
    assert "reclaimed" in r2.json()


# ---------- 4. 兼容层（旧 URL 保留） ----------

def test_old_pending_301_to_admin(ctx):
    r = client.get("/api/ticket-requests/pending", headers=ctx["h"],
                   follow_redirects=False)
    assert r.status_code == 301, r.text
    assert "/api/admin/ticket-requests/pending" in r.headers.get("location", "")
    # 跟随重定向后仍可用
    r2 = client.get("/api/ticket-requests/pending", headers=ctx["h"])
    assert r2.status_code == 200, r2.text


def test_old_reclaim_stale_forward(ctx):
    r = client.post("/api/ticket-requests/reclaim-stale", json={}, headers=ctx["h"])
    assert r.status_code == 200, r.text
    assert "reclaimed" in r.json()


def test_old_execute_by_type_forward(ctx):
    # 旧 execute-by-type 应转发 RPC 逻辑（proposal 从 URL 取，type 从 body 取）
    r = client.post(f"/api/proposals/{ctx['pr2']}/ticket-requests/execute-by-type",
                    json={"type": "epic"}, headers=ctx["h"])
    assert r.status_code == 200, r.text
    assert r.json()["request"]["type"] == "epic"
    assert r.json()["ticket"]["title"] == "NS-RP-B"


def test_old_action_urls_forward(ctx):
    # 旧 execute（带 pid 前缀）→ 转发统一动作
    r = client.post(f"/api/proposals/{ctx['pr1']}/ticket-requests/{ctx['rid']}/execute",
                    json={}, headers=ctx["h"])
    assert r.status_code == 200, r.text
    # 旧 fail：rid2 不属于 pr1 → 旧端点保留归属校验 → 404
    r2 = client.post(f"/api/proposals/{ctx['pr1']}/ticket-requests/{ctx['rid2']}/fail",
                     json={"error": "x"}, headers=ctx["h"])
    assert r2.status_code == 404, r2.text
    # rid2 属于 pr2 → 200
    r3 = client.post(f"/api/proposals/{ctx['pr2']}/ticket-requests/{ctx['rid2']}/fail",
                     json={"error": "x"}, headers=ctx["h"])
    assert r3.status_code == 200, r3.text


def test_old_cross_proposal_404_kept(ctx):
    # 旧 URL 的归属校验仍然生效（跨 proposal 操作 404）
    r = client.post(f"/api/proposals/{ctx['pr1']}/ticket-requests/{ctx['rid2']}/claim",
                    json={}, headers=ctx["h"])
    assert r.status_code == 404, r.text


# ---------- 5. 调用点静态断言（worker / mcp_server 已切新 URL） ----------

def test_worker_call_sites_use_new_urls():
    import agentboard.worker as w
    src = open(w.__file__, encoding="utf-8").read()
    assert "/api/admin/ticket-requests/pending" in src
    assert "/api/admin/ticket-requests/reclaim-stale" in src
    assert 'f"/api/ticket-requests/{rid}/claim"' in src
    assert 'f"/api/ticket-requests/{rid}/fail"' in src
    # 旧 URL 不应再出现在 worker 调用点
    assert "/api/proposals/{pid}/ticket-requests/{rid}/claim" not in src
    assert "/api/proposals/{pid}/ticket-requests/{rid}/fail" not in src


def test_mcp_call_site_uses_new_rpc():
    import agentboard.mcp_server as ms
    src = open(ms.__file__, encoding="utf-8").read()
    assert '"/api/ticket-requests:execute"' in src
    assert "execute-by-type" not in src


# ---------- 6. Pydantic alias 兼容 ----------

def test_pydantic_alias_still_importable():
    from agentboard.api import (  # noqa: F401
        TicketRequestSpec, ProposalTicketIn, TicketRequestExecuteIn,
        TicketRequestExecuteSpec,
    )
    assert ProposalTicketIn is TicketRequestSpec
    assert TicketRequestExecuteIn is TicketRequestSpec
