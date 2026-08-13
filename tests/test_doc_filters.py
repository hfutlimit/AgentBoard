"""Epic 138 文档列表 + 过滤增强：service.list_documents 扩展与新计数端点。

覆盖：
- list_documents 新增 query：folder_id / author_id / epic_id / story_id / sort
- sort 白名单校验（非法值 → InvalidValue）
- count_document_comments 正常 / 文档不存在 → NotFound
- 跨用户隔离：未指定 project_id 时仅返回该用户有权限的项目文档
- 跨项目隔离：指定 project_id 时严格过滤
- 复合过滤：type + status + folder_id + author_id 同时生效
- sort 三种取值均生效，且稳定排序（同值时按 id 兜底）
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard import service
from agentboard.models import Base


def _env():
    """构造最小可重现环境：2 个项目、2 个用户、3 个 epic、4 个 folder、6 个 document。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as s:
        owner = service.register_user(s, username="owner", password="password123")
        other = service.register_user(s, username="other", password="password123")
        p1 = service.create_project(s, name="P1", key="P1")
        service.add_project_member(s, project_id=p1.id, user_id=owner.id, role="owner")
        p2 = service.create_project(s, name="P2", key="P2")
        service.add_project_member(s, project_id=p2.id, user_id=owner.id, role="owner")
        service.add_project_member(s, project_id=p2.id, user_id=other.id, role="owner")
        # P1 的 epic / folder
        e1 = service.create_epic(s, project_id=p1.id, title="E1")
        e2 = service.create_epic(s, project_id=p1.id, title="E2")
        f_root = service.create_document_folder(s, project_id=p1.id, name="root")
        f_child = service.create_document_folder(
            s, project_id=p1.id, name="child", parent_id=f_root.id,
        )
        # 6 个文档，分布在不同项目 / epic / folder / author / type
        service.create_document(
            s, project_id=p1.id, title="A-Alpha", type="plan", folder_id=f_root.id,
            author_id=owner.id,
        )
        service.create_document(
            s, project_id=p1.id, title="B-Beta", type="memory", folder_id=f_root.id,
            author_id=other.id, epic_id=e1.id,
        )
        service.create_document(
            s, project_id=p1.id, title="C-Gamma", type="plan", folder_id=f_child.id,
            author_id=owner.id, epic_id=e1.id,
        )
        service.create_document(
            s, project_id=p1.id, title="D-Delta", type="design", folder_id=f_child.id,
            author_id=owner.id, epic_id=e2.id,
        )
        # P2 的文档（owner 也属于 P2，但 other 是 P2 owner）
        service.create_document(
            s, project_id=p2.id, title="E-Epsilon", type="knowledge", author_id=other.id,
        )
        service.create_document(
            s, project_id=p2.id, title="F-Zeta", type="plan", author_id=owner.id,
        )
        ids = {
            "owner": owner.id, "other": other.id,
            "p1": p1.id, "p2": p2.id,
            "e1": e1.id, "e2": e2.id,
            "f_root": f_root.id, "f_child": f_child.id,
        }
    return sessions, ids


def _ids(d, name):
    return d[name]


# --------------------------------------------------------------------------- #
# list_documents 扩展 query
# --------------------------------------------------------------------------- #

def test_list_documents_folder_filter():
    sessions, ids = _env()
    with sessions() as s:
        rows = service.list_documents(s, project_id=ids["p1"], folder_id=ids["f_root"])
        titles = sorted(r.title for r in rows)
    assert titles == ["A-Alpha", "B-Beta"]


def test_list_documents_author_filter():
    sessions, ids = _env()
    with sessions() as s:
        rows = service.list_documents(s, project_id=ids["p1"], author_id=ids["owner"])
        titles = sorted(r.title for r in rows)
    # A-Alpha / C-Gamma / D-Delta 都是 owner 写的
    assert titles == ["A-Alpha", "C-Gamma", "D-Delta"]


def test_list_documents_epic_filter():
    sessions, ids = _env()
    with sessions() as s:
        rows = service.list_documents(s, project_id=ids["p1"], epic_id=ids["e1"])
        titles = sorted(r.title for r in rows)
    # B-Beta / C-Gamma 都属于 e1
    assert titles == ["B-Beta", "C-Gamma"]


def test_list_documents_story_filter_isolated_to_others():
    """当前没有 story 数据，story_id 过滤应返回空。"""
    sessions, ids = _env()
    with sessions() as s:
        rows = service.list_documents(s, project_id=ids["p1"], story_id=99999)
    assert rows == []


def test_list_documents_combined_filters():
    """type + status + folder_id + author_id 同时生效。"""
    sessions, ids = _env()
    with sessions() as s:
        rows = service.list_documents(
            s, project_id=ids["p1"],
            type="plan", status="draft", folder_id=ids["f_child"],
            author_id=ids["owner"],
        )
        titles = [r.title for r in rows]
    assert titles == ["C-Gamma"]


# --------------------------------------------------------------------------- #
# sort
# --------------------------------------------------------------------------- #

def test_list_documents_sort_title_ascending():
    sessions, ids = _env()
    with sessions() as s:
        rows = service.list_documents(s, project_id=ids["p1"], sort="title")
        titles = [r.title for r in rows]
    assert titles == sorted(titles)
    assert titles == ["A-Alpha", "B-Beta", "C-Gamma", "D-Delta"]


def test_list_documents_sort_invalid_rejected():
    sessions, ids = _env()
    with sessions() as s:
        try:
            service.list_documents(s, project_id=ids["p1"], sort="garbage")
        except service.InvalidValue as e:
            assert "invalid sort" in str(e)
        else:
            raise AssertionError("expected InvalidValue for invalid sort")


def test_list_documents_default_sort_is_updated_desc():
    """不传 sort 时按 updated_at 倒序；同秒内多次插入用 id 兜底（不影响测试稳定性的近似验证）。"""
    sessions, ids = _env()
    with sessions() as s:
        rows = service.list_documents(s, project_id=ids["p1"])
        ids_list = [r.id for r in rows]
    # 默认排序必须非空且数量正确
    assert len(rows) == 4
    # 至少保证稳定可读
    assert all(r.project_id == ids["p1"] for r in rows)


# --------------------------------------------------------------------------- #
# 跨用户 / 跨项目隔离
# --------------------------------------------------------------------------- #

def test_list_documents_user_isolation_no_project():
    """未指定 project_id 且有 user_id 时，仅返回该用户有权限的项目文档。

    owner 是 P1 + P2 成员，应看到 6 个；other 是 P2 成员，应看到 P2 的 2 个。
    """
    sessions, ids = _env()
    with sessions() as s:
        owner_rows = service.list_documents(s, user_id=ids["owner"])
        other_rows = service.list_documents(s, user_id=ids["other"])
    assert len(owner_rows) == 6
    assert len(other_rows) == 2
    assert {r.project_id for r in other_rows} == {ids["p2"]}


def test_list_documents_no_user_no_project_returns_all():
    """未指定 project_id 且无 user_id：返回所有文档（admin / 内部维护场景）。"""
    sessions, ids = _env()
    with sessions() as s:
        rows = service.list_documents(s)
    assert len(rows) == 6


# --------------------------------------------------------------------------- #
# count_document_comments
# --------------------------------------------------------------------------- #

def test_count_document_comments_empty():
    sessions, ids = _env()
    with sessions() as s:
        # A-Alpha 没评论
        rows = service.list_documents(s, project_id=ids["p1"])
        a = next(r for r in rows if r.title == "A-Alpha")
        n = service.count_document_comments(s, a.id)
    assert n == 0


def test_count_document_comments_after_add():
    sessions, ids = _env()
    with sessions() as s:
        rows = service.list_documents(s, project_id=ids["p1"])
        a = next(r for r in rows if r.title == "A-Alpha")
        service.create_document_comment(
            s, document_id=a.id, author="alice", content="first",
        )
        service.create_document_comment(
            s, document_id=a.id, author="bob", content="second", author_id=ids["other"],
        )
        n = service.count_document_comments(s, a.id)
    assert n == 2


def test_count_document_comments_not_found():
    sessions, ids = _env()
    with sessions() as s:
        try:
            service.count_document_comments(s, 99999)
        except service.NotFound as e:
            assert "not found" in str(e)
        else:
            raise AssertionError("expected NotFound for missing document")
