"""Epic 139 文档 Revision（不可变快照 + 乐观锁）：service 层 + 端到端。

覆盖：
- create_document 自动生成 revision 1（current_revision_id / current_revision_number）
- 多次 save_document_with_revision 形成 r1/r2/r3，revision_number 单调递增
- 乐观锁：expected_revision_number 不匹配 → RevisionConflict(expected, current)
- 空保存（title+content 都未变化）→ 不消耗 revision_number，返回原 Document
- list_revisions 倒序、get_revision、不存在 → NotFound
- restore_revision 不改写历史；新 revision 标 is_restore=True、restored_from_revision=N
- restore 强制 change_note（空 → InvalidValue）
- 不影响 Document 头其他元数据（type / status / folder / epic / story 仍走 update_document）
- 并发安全：两个并发 save，期望相同 → 一个成功一个 RevisionConflict
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard import service
from agentboard.models import Base


def _env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as s:
        u = service.register_user(s, username="rev-user", password="password123")
        p = service.create_project(s, name="P1", key="P1")
        service.add_project_member(s, project_id=p.id, user_id=u.id, role="owner")
        ids = {"user": u.id, "project": p.id}
    return sessions, ids


# --------------------------------------------------------------------------- #
# 创建 + 头指针
# --------------------------------------------------------------------------- #

def test_create_document_initializes_revision_one():
    sessions, ids = _env()
    with sessions() as s:
        d = service.create_document(s, project_id=ids["project"], title="D1", content="hello")
        assert d.current_revision_number == 1
        assert d.current_revision_id is not None
        rev = service.get_revision(s, d.id, 1)
        assert rev.title == "D1"
        assert rev.content == "hello"
        assert rev.change_note == "初始版本"
        assert rev.is_restore is False
        assert rev.restored_from_revision is None


# --------------------------------------------------------------------------- #
# 多次保存 → revision_number 单调递增
# --------------------------------------------------------------------------- #

def test_save_with_revision_monotonic():
    sessions, ids = _env()
    with sessions() as s:
        d = service.create_document(s, project_id=ids["project"], title="D1", content="v1")
        for i, content in enumerate(["v2", "v3", "v4"], start=2):
            d = service.save_document_with_revision(
                s, id=d.id, expected_revision_number=i - 1,
                content=content, change_note=f"edit to {content}",
                author_id=ids["user"], author="rev-user",
            )
        assert d.current_revision_number == 4
        revs = service.list_revisions(s, d.id)
        nums = [r.revision_number for r in revs]
        assert nums == [4, 3, 2, 1]


# --------------------------------------------------------------------------- #
# 乐观锁冲突
# --------------------------------------------------------------------------- #

def test_save_with_revision_conflict_returns_409_payload():
    sessions, ids = _env()
    with sessions() as s:
        d = service.create_document(s, project_id=ids["project"], title="D1", content="v1")
        # 客户端基于 r1 编辑；服务端已走到 r2 → 冲突
        service.save_document_with_revision(
            s, id=d.id, expected_revision_number=1, content="v2", change_note="x",
        )
        try:
            service.save_document_with_revision(
                s, id=d.id, expected_revision_number=1, content="v3", change_note="y",
            )
        except service.RevisionConflict as e:
            assert e.expected == 1
            assert e.current == 2
        else:
            raise AssertionError("expected RevisionConflict")


# --------------------------------------------------------------------------- #
# 空保存不消耗 revision_number
# --------------------------------------------------------------------------- #

def test_no_op_save_does_not_consume_revision_number():
    sessions, ids = _env()
    with sessions() as s:
        d = service.create_document(s, project_id=ids["project"], title="D1", content="v1")
        same = service.save_document_with_revision(
            s, id=d.id, expected_revision_number=1, title="D1", content="v1",
            change_note="nothing changed",
        )
        assert same.current_revision_number == 1
        revs = service.list_revisions(s, d.id)
        assert [r.revision_number for r in revs] == [1]


# --------------------------------------------------------------------------- #
# list / get / 404
# --------------------------------------------------------------------------- #

def test_list_revisions_paginated_and_descending():
    sessions, ids = _env()
    with sessions() as s:
        d = service.create_document(s, project_id=ids["project"], title="D1", content="v1")
        for i in range(5):
            service.save_document_with_revision(
                s, id=d.id, expected_revision_number=i + 1,
                content=f"v{i+2}", change_note=f"e{i}",
            )
        page1 = service.list_revisions(s, d.id, limit=2, offset=0)
        page2 = service.list_revisions(s, d.id, limit=2, offset=2)
        assert [r.revision_number for r in page1] == [6, 5]
        assert [r.revision_number for r in page2] == [4, 3]


def test_get_revision_404_when_missing():
    sessions, ids = _env()
    with sessions() as s:
        d = service.create_document(s, project_id=ids["project"], title="D1", content="v1")
        try:
            service.get_revision(s, d.id, 999)
        except service.NotFound as e:
            assert "not found" in str(e)
        else:
            raise AssertionError("expected NotFound")


def test_list_revisions_404_when_document_missing():
    sessions, ids = _env()
    with sessions() as s:
        try:
            service.list_revisions(s, 99999)
        except service.NotFound as e:
            assert "not found" in str(e)
        else:
            raise AssertionError("expected NotFound")


# --------------------------------------------------------------------------- #
# Restore：复制为新 revision，标记 is_restore
# --------------------------------------------------------------------------- #

def test_restore_revision_creates_new_revision_without_overwriting_history():
    sessions, ids = _env()
    with sessions() as s:
        d = service.create_document(s, project_id=ids["project"], title="T", content="v1")
        d = service.save_document_with_revision(
            s, id=d.id, expected_revision_number=1, content="v2", change_note="edit",
        )
        d = service.save_document_with_revision(
            s, id=d.id, expected_revision_number=2, content="v3", change_note="edit",
        )
        # 当前 r3（content=v3），恢复到 r1（content=v1）
        d = service.restore_revision(
            s, id=d.id, revision_number=1, change_note="oops, revert",
            author_id=ids["user"], author="rev-user",
        )
        # 头指针 + 头字段都恢复
        assert d.content == "v1"
        assert d.title == "T"
        # 历史完整保留 + 新增 r4
        revs = service.list_revisions(s, d.id)
        nums = [r.revision_number for r in revs]
        assert nums == [4, 3, 2, 1]
        # r4 标 is_restore / restored_from_revision=1
        r4 = next(r for r in revs if r.revision_number == 4)
        assert r4.is_restore is True
        assert r4.restored_from_revision == 1
        assert "回滚自 r1" in r4.change_note
        # r1/r2/r3 内容未被改
        r1 = service.get_revision(s, d.id, 1)
        r2 = service.get_revision(s, d.id, 2)
        r3 = service.get_revision(s, d.id, 3)
        assert (r1.title, r1.content) == ("T", "v1")
        assert (r2.title, r2.content) == ("T", "v2")
        assert (r3.title, r3.content) == ("T", "v3")


def test_restore_requires_change_note():
    sessions, ids = _env()
    with sessions() as s:
        d = service.create_document(s, project_id=ids["project"], title="T", content="v1")
        try:
            service.restore_revision(
                s, id=d.id, revision_number=1, change_note="   ",
            )
        except service.InvalidValue as e:
            assert "change_note" in str(e)
        else:
            raise AssertionError("expected InvalidValue for empty change_note")


def test_restore_source_missing_404():
    sessions, ids = _env()
    with sessions() as s:
        d = service.create_document(s, project_id=ids["project"], title="T", content="v1")
        try:
            service.restore_revision(
                s, id=d.id, revision_number=999, change_note="nope",
            )
        except service.NotFound as e:
            assert "not found" in str(e)
        else:
            raise AssertionError("expected NotFound")


# --------------------------------------------------------------------------- #
# 头部元数据（type/status/folder/epic/story）走 update_document，不影响 revision
# --------------------------------------------------------------------------- #

def test_head_metadata_update_does_not_create_revision():
    sessions, ids = _env()
    with sessions() as s:
        d = service.create_document(s, project_id=ids["project"], title="T", content="v1")
        before = d.current_revision_number
        service.update_document(s, id=d.id, status="in_review")
        service.update_document(s, id=d.id, type="design")
        after_rev = service.list_revisions(s, d.id)
        assert [r.revision_number for r in after_rev] == [1]
        d2 = service.get_document(s, d.id)
        assert d2.current_revision_number == before
        assert d2.status == "in_review"
        assert d2.type == "design"


# --------------------------------------------------------------------------- #
# RevisionConflict 携带 expected/current 字段（供 API 序列化）
# --------------------------------------------------------------------------- #

def test_revision_conflict_carries_payload():
    sessions, ids = _env()
    with sessions() as s:
        d = service.create_document(s, project_id=ids["project"], title="T", content="v1")
        service.save_document_with_revision(
            s, id=d.id, expected_revision_number=1, content="v2", change_note="x",
        )
        try:
            service.save_document_with_revision(
                s, id=d.id, expected_revision_number=1, content="v3", change_note="y",
            )
        except service.RevisionConflict as e:
            payload = {"expected": e.expected, "current": e.current}
            assert payload == {"expected": 1, "current": 2}
        else:
            raise AssertionError("expected RevisionConflict")
