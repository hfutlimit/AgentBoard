"""Epic 15 文档 epic/story 关联修复：存在性 + 同项目归属校验。

覆盖：
- 创建文档：跨项目 epic → 422；跨项目 story → 422；同项目但 story 与 epic 不匹配 → 422；
  合法关联（同项目 epic + 属其 story）→ 成功；
- 更新文档：PATCH epic_id 同项目生效；跨项目/不存在 → 422；epic_id=null 清空；
  story 与 epic 不匹配 → 422；普通 title 更新不受历史脏关联影响。
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
        u = service.register_user(s, username="doc-user", password="password123")
        p1 = service.create_project(s, name="P1", key="P1")
        service.add_project_member(s, project_id=p1.id, user_id=u.id, role="owner")
        p2 = service.create_project(s, name="P2", key="P2")
        service.add_project_member(s, project_id=p2.id, user_id=u.id, role="owner")
        e1 = service.create_epic(s, project_id=p1.id, title="E1")
        e2 = service.create_epic(s, project_id=p2.id, title="E2")
        st1 = service.create_story(s, epic_id=e1.id, title="S1")
        st2 = service.create_story(s, epic_id=e2.id, title="S2")
        e3 = service.create_epic(s, project_id=p1.id, title="E3")
        st3 = service.create_story(s, epic_id=e3.id, title="S3")  # 同项目、不同 epic
        doc = service.create_document(s, project_id=p1.id, title="D1", epic_id=e1.id)
        # expire_on_commit=True：session 关闭后 ORM 属性失效，提前提取纯 int
        ids = (p1.id, p2.id, e1.id, e2.id, st1.id, st2.id, e3.id, st3.id, doc.id)
    return sessions, ids


def _ids(ids, name):
    idx = {"p1": 0, "p2": 1, "e1": 2, "e2": 3, "st1": 4, "st2": 5, "e3": 6, "st3": 7, "doc": 8}[name]
    return ids[idx]


def test_create_document_rejects_cross_project_epic():
    sessions, ids = _env()
    p2, e1 = _ids(ids, "p2"), _ids(ids, "e1")
    with sessions() as s:
        try:
            service.create_document(s, project_id=p2, title="X", epic_id=e1)
            raise AssertionError("expected InvalidValue for cross-project epic")
        except service.InvalidValue as e:
            assert "不属于项目" in str(e)


def test_create_document_rejects_cross_project_story():
    sessions, ids = _env()
    p2, st1 = _ids(ids, "p2"), _ids(ids, "st1")
    with sessions() as s:
        try:
            service.create_document(s, project_id=p2, title="X", story_id=st1)
            raise AssertionError("expected InvalidValue for cross-project story")
        except service.InvalidValue as e:
            assert "不属于项目" in str(e)


def test_create_document_rejects_story_epic_mismatch():
    sessions, ids = _env()
    p1, e1, st3 = _ids(ids, "p1"), _ids(ids, "e1"), _ids(ids, "st3")
    with sessions() as s:
        try:
            service.create_document(s, project_id=p1, title="X",
                                    epic_id=e1, story_id=st3)  # st3 属 E3，非 E1
            raise AssertionError("expected InvalidValue for story/epic mismatch")
        except service.InvalidValue as e:
            assert "不属于 epic" in str(e)


def test_create_document_valid_links():
    sessions, ids = _env()
    p1, p2, e1, e2, st1 = _ids(ids, "p1"), _ids(ids, "p2"), _ids(ids, "e1"), _ids(ids, "e2"), _ids(ids, "st1")
    with sessions() as s:
        d = service.create_document(s, project_id=p1, title="OK",
                                    epic_id=e1, story_id=st1)
        assert d.epic_id == e1 and d.story_id == st1
        d2 = service.create_document(s, project_id=p2, title="OK2", epic_id=e2)
        assert d2.epic_id == e2


def test_update_document_link_change_and_clear():
    sessions, ids = _env()
    p1, p2, e1, e2, st1, st3, doc = (
        _ids(ids, "p1"), _ids(ids, "p2"), _ids(ids, "e1"), _ids(ids, "e2"),
        _ids(ids, "st1"), _ids(ids, "st3"), _ids(ids, "doc"),
    )
    with sessions() as s:
        d = service.update_document(s, doc, epic_id=None)   # 显式 null 清空
        assert d.epic_id is None
        d = service.update_document(s, doc, epic_id=e1)      # 同项目 epic 生效
        assert d.epic_id == e1
        d = service.update_document(s, doc, story_id=st1)    # 属该 epic 的 story 生效
        assert d.story_id == st1
        try:
            service.update_document(s, doc, epic_id=e2)      # 跨项目 → 422
            raise AssertionError("expected InvalidValue for cross-project epic")
        except service.InvalidValue as e:
            assert "不属于项目" in str(e)
        try:
            service.update_document(s, doc, epic_id=99999)   # 不存在 → 422
            raise AssertionError("expected InvalidValue for missing epic")
        except service.InvalidValue as e:
            assert "not found" in str(e)
        try:
            service.update_document(s, doc, story_id=st3)    # 与 e1 不匹配 → 422
            raise AssertionError("expected InvalidValue for story/epic mismatch")
        except service.InvalidValue as e:
            assert "不属于 epic" in str(e)


def test_update_document_title_unaffected_by_legacy_links():
    sessions, ids = _env()
    doc = _ids(ids, "doc")
    with sessions() as s:
        d = service.update_document(s, doc, title="renamed", content="c")
        assert d.title == "renamed"
        assert d.epic_id == _ids(ids, "e1")  # 关联保持不变
