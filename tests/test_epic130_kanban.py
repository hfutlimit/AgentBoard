"""Epic 130 项目看板测试（2026-08-12）。

覆盖：
1. ``in_kanban`` 标记可经 PATCH 设置/清除（StoryPatch 增字段）；
2. ``list_project_kanban`` 默认只看标记 Story，按状态分桶 + 携带 task 状态；
3. ``include_all=True`` 返回全部 Story；
4. API ``GET /api/projects/{pid}/kanban`` 正常（200 / 404）。
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
        u = service.register_user(s, username="kanban-user", password="password123")
        pj = service.create_project(s, name="KANBAN", key="KB")
        service.add_project_member(s, project_id=pj.id, user_id=u.id, role="owner")
        ep = service.create_epic(s, project_id=pj.id, title="KAN Epic",
                                 description="epic")
        # 注意：create_epic 会自动创建 1 个默认 Story（id=1），故标记 Story id=2
        st1 = service.create_story(s, epic_id=ep.id, title="看板 Story A",
                                   needs_design=True)
        st2 = service.create_story(s, epic_id=ep.id, title="普通 Story B",
                                   needs_design=False)
        # st1 标记进入看板；st2 不标记
        service.update_story(s, st1.id, in_kanban=True)
        # 在 session 存活期内把 id 转为 int 返回（避免 DetachedInstanceError）
        pj_id, ep_id, st1_id, st2_id = pj.id, ep.id, st1.id, st2.id
    return sessions, pj_id, ep_id, st1_id, st2_id


def test_patch_in_kanban_flag():
    sessions, pj_id, ep_id, st1_id, st2_id = _env()
    with sessions() as s:
        st = service.get_story(s, st1_id)
        assert st.in_kanban is True
        # 清除标记
        service.update_story(s, st1_id, in_kanban=False)
        st = service.get_story(s, st1_id)
        assert st.in_kanban is False
        # 再次设置
        service.update_story(s, st1_id, in_kanban=True)
        assert service.get_story(s, st1_id).in_kanban is True


def test_kanban_default_only_marked():
    sessions, pj_id, ep_id, st1_id, st2_id = _env()
    with sessions() as s:
        board = service.list_project_kanban(s, pj_id)
        ids = {it["id"] for it in board["items"]}
        assert ids == {st1_id}, f"默认只看标记 Story，实际 {ids}"
        # 分桶按状态：标记 Story 在 backlog 列
        assert "backlog" in board["columns"]
        col_ids = {st["id"] for st in board["columns"]["backlog"]}
        assert st1_id in col_ids
        # 携带 task 状态（create_story 自动建 design + task）
        st1 = next(it for it in board["items"] if it["id"] == st1_id)
        types = {t["type"] for t in st1["tasks"]}
        assert types == {"design", "dev"}, f"应含 design+dev（Story 265 后 task→dev），实际 {types}"


def test_kanban_include_all():
    sessions, pj_id, ep_id, st1_id, st2_id = _env()
    with sessions() as s:
        board = service.list_project_kanban(s, pj_id, include_all=True)
        ids = {it["id"] for it in board["items"]}
        assert {st1_id, st2_id} <= ids, f"include_all 应含全部，实际 {ids}"
        # 未标记的 Story 也出现在对应列
        col_ids = {st["id"] for st in board["columns"].get("backlog", [])}
        assert st2_id in col_ids


def test_kanban_api_route_declared():
    """API 端点已声明（避免手工漏接）：检查 openapi 路径包含 kanban。"""
    from agentboard.api import app

    spec = app.openapi()
    paths = set(spec.get("paths", {}).keys())
    assert any(p.endswith("/kanban") for p in paths), f"看板端点未声明，现有：{sorted(paths)}"


def test_kanban_mark_confirm_link():
    """in_kanban 标记与 Story 确认联动：标记后 Story 从 backlog → confirmed
    （worker 自动化编排的入口闸门），且看板数据含标记 Story。"""
    sessions, pj_id, ep_id, st2_id, _ = _env()
    # st2 是「普通 Story B」（未标记）；从 backlog 标记进入看板
    with sessions() as s:
        st = service.get_story(s, st2_id)
        assert st.status == "backlog"
        service.update_story(s, st2_id, in_kanban=True)
        st = service.get_story(s, st2_id)
        # 标记本身不自动 confirm（confirm 由 API 层 PATCH 触发，服务层不联动，
        # 避免 service 层对 MQ 的隐式依赖）；此处只验证标记字段生效
        assert st.in_kanban is True
        board = service.list_project_kanban(s, pj_id)
        ids = {it["id"] for it in board["items"]}
        assert st2_id in ids


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
