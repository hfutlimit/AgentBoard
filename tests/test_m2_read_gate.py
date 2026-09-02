"""Implementation Plan T2.1 · 读门（user_can_read_project / readable_project_ids）回归测试。

为什么专门测这些
----------------
HTTP 层的 project_access_middleware 在 ``AGENTBOARD_REQUIRE_AUTH=1`` 时已经
要求「项目成员才能读」，但它依赖 ``_resolve_project_id_from_request`` 能从
路由/参数里解析出 project id —— **解析不出来就放行**。于是不带
``project_id`` 的全局列表端点成了漏洞：

    GET /api/tasks   （无 project_id）→ 返回全库所有项目的 task
    GET /api/stories （无 project_id）→ 返回全库所有项目的 story

任何登录用户都能拉到别人项目的内容。本测试把这两个洞钉死，同时锁定
documents 原有的过滤行为（改为统一读门后语义不变）。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m2_read_gate.py -q
"""
import itertools
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "src", "backend-fastapi")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _BACKEND)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from fastapi.testclient import TestClient  # noqa: E402

from agentboard import api, auth, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402

init_db()

_SEQ = itertools.count(1)


@pytest.fixture(autouse=True)
def _require_auth_toggle():
    """每个测试强制 REQUIRE_AUTH=1，结束后还原。

    必须还原：REQUIRE_AUTH 是进程级环境变量，别的测试文件在模块导入时
    pop 掉它来获得本地开放模式 —— 本文件的值若泄漏，会改变它们的运行结果。
    """
    os.environ["AGENTBOARD_REQUIRE_AUTH"] = "1"
    yield
    os.environ["AGENTBOARD_REQUIRE_AUTH"] = "0"


def _client():
    return TestClient(api.app)


def _seed_users(*labels):
    """1 项目 2 成员（owner/insider）+ 1 个非成员 outsider + 1 个 admin。"""
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"rg P{n}")
        owner = service.register_user(s, username=f"rg-owner{n}",
                                      password="password123")
        insider = service.register_user(s, username=f"rg-insider{n}",
                                        password="password123")
        outsider = service.register_user(s, username=f"rg-outsider{n}",
                                         password="password123")
        service.add_project_member(s, project_id=p.id, user_id=owner.id,
                                   role="owner")
        service.add_project_member(s, project_id=p.id, user_id=insider.id,
                                   role="member")
        epic = service.create_epic(s, project_id=p.id, title=f"rg E{n}")
        st = service.create_story(s, epic_id=epic.id, title=f"rg S{n}",
                                  created_by_user_id=owner.id)
        t = service.create_task(s, project_id=p.id, story_id=st.id,
                                title=f"rg T{n}", type="dev",
                                created_by_user_id=owner.id)
        doc = service.create_document(
            s, project_id=p.id, title=f"rg D{n}", type="plan", content="x",
            author_id=owner.id)
        # admin 用户（is_admin=1），不是任何项目成员
        s.execute(
            text("INSERT INTO users (username, password_hash, display_name,"
                 " is_admin, created_at) VALUES (:u, 'x', 'Admin', 1,"
                 " CURRENT_TIMESTAMP)"),
            {"u": f"rg-admin{n}"})
        s.commit()
        admin_id = s.execute(text(
            "SELECT id FROM users WHERE username = :u"), {"u": f"rg-admin{n}"}
        ).scalar()
        return {
            "pid": p.id, "owner": owner.id, "insider": insider.id,
            "outsider": outsider.id, "admin": int(admin_id),
            "story": st.id, "task": t.id, "doc": doc.id,
        }


from sqlalchemy import text  # noqa: E402


def _hdr(uid):
    return {"Authorization": f"Bearer {auth.make_token(uid)}"}


# ---------- 1. service 层谓词 ----------

def test_user_can_read_project_semantics():
    d = _seed_users()
    with SessionLocal() as s:
        assert service.user_can_read_project(s, d["insider"], d["pid"])
        assert not service.user_can_read_project(s, d["outsider"], d["pid"])
        assert not service.user_can_read_project(s, None, d["pid"])
        assert service.user_can_read_project(
            s, d["outsider"], d["pid"], is_admin=True)


def test_readable_project_ids_none_vs_empty():
    """None（不受限）和 []（一个都读不了）是两种语义，不能混。"""
    d = _seed_users()
    with SessionLocal() as s:
        assert service.readable_project_ids(s, d["insider"]) == [d["pid"]]
        assert service.readable_project_ids(s, d["admin"], is_admin=True) is None
        outsider_readable = service.readable_project_ids(s, d["outsider"])
        assert outsider_readable == [] or d["pid"] not in outsider_readable


# ---------- 2. HTTP 全局列表（实测泄漏点） ----------

def test_global_tasks_hidden_from_non_member():
    """GET /api/tasks 不带 project_id：非成员看不到别人项目的 task。"""
    d = _seed_users()
    c = _client()
    # 成员看得到
    r = _client().get("/api/tasks", headers=_hdr(d["insider"]))
    assert r.status_code == 200, r.text
    assert any(x["id"] == d["task"] for x in r.json())
    # 非成员看不到
    r2 = c.get("/api/tasks", headers=_hdr(d["outsider"]))
    assert r2.status_code == 200, r2.text
    assert not any(x["id"] == d["task"] for x in r2.json()), \
        "全局 task 列表泄漏了非成员项目的数据"


def test_global_tasks_visible_to_admin():
    d = _seed_users()
    r = _client().get("/api/tasks", headers=_hdr(d["admin"]))
    assert r.status_code == 200, r.text
    assert any(x["id"] == d["task"] for x in r.json())


def test_global_tasks_no_leak_for_anonymous():
    """未登录（REQUIRE_AUTH=1）→ 401 或空列表，总之不能泄漏全库。"""
    d = _seed_users()
    r = _client().get("/api/tasks")
    if r.status_code == 200:
        assert not any(x["id"] == d["task"] for x in r.json())
    else:
        assert r.status_code == 401, r.text


def test_scoped_task_still_blocked_for_non_member():
    """带 project_id 的路径由 project_access_middleware 管住：非成员 403。"""
    d = _seed_users()
    r = _client().get(f"/api/tasks?project_id={d['pid']}",
                      headers=_hdr(d["outsider"]))
    assert r.status_code == 403, r.text


def test_global_stories_hidden_from_non_member():
    """GET /api/stories 不带 project_id：非成员看不到别人项目的 story。"""
    d = _seed_users()
    r = _client().get("/api/stories", headers=_hdr(d["outsider"]))
    assert r.status_code == 200, r.text
    body = r.json()
    items = body["items"] if isinstance(body, dict) else body
    assert not any(x["id"] == d["story"] for x in items), \
        "全局 story 列表泄漏了非成员项目的数据"
    # 成员看得到
    r2 = _client().get("/api/stories", headers=_hdr(d["insider"]))
    items2 = r2.json()
    items2 = items2["items"] if isinstance(items2, dict) else items2
    assert any(x["id"] == d["story"] for x in items2)


def test_global_documents_hidden_from_non_member():
    """GET /api/documents 不带 project_id：documents 原有过滤，锁定行为。"""
    d = _seed_users()
    r = _client().get("/api/documents", headers=_hdr(d["outsider"]))
    assert r.status_code == 200, r.text
    assert not any(x["id"] == d["doc"] for x in r.json())
    r2 = _client().get("/api/documents", headers=_hdr(d["insider"]))
    assert any(x["id"] == d["doc"] for x in r2.json())


def test_document_detail_blocked_for_non_member():
    """GET /api/documents/{id}：中间件按 DB 反查 project → 非成员 403。"""
    d = _seed_users()
    r = _client().get(f"/api/documents/{d['doc']}", headers=_hdr(d["outsider"]))
    assert r.status_code == 403, r.text
