"""Dashboard overview 聚合端点回归护栏（Task 995，Epic 117）。

覆盖：
1. service.get_overview 结构契约：counts / projects / status_distribution / activity_7d；
2. 可见性：admin 全量；普通用户仅成员项目；未登录为空；
3. 口径一致性：counts.tasks == 可见项目任务总数，projects 各项目 total/done/percent 正确；
4. API 端点直接调用：匿名 200（REQUIRE_AUTH=0 开放模式）返回空结构；
5. activity_7d 近 7 天完整性（含 0 计数日）。

运行：
    PYTHONPATH=. python -m pytest tests/test_overview.py -q
"""
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import api, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402

init_db()  # 跑完整 alembic 迁移链


def _seed():
    """创建 2 项目 × (1 epic + 1 story + 任务)，admin 全量可见；member 仅见其一。"""
    with SessionLocal() as s:
        admin = service.register_user(s, username="ov_admin", password="password123")
        member = service.register_user(s, username="ov_member", password="password123")
        admin.is_admin = True
        s.commit()

        p1 = service.create_project(s, name="Overview P1")
        p2 = service.create_project(s, name="Overview P2")
        service.add_project_member(s, project_id=p1.id, user_id=member.id, role="member")

        # Story 265：状态收敛为 5 值（backlog 已下线 → todo）
        for pid, title, statuses in (
            (p1.id, "Epic A", ["done", "in_progress", "todo", "todo", "done"]),
            (p2.id, "Epic B", ["done"]),
        ):
            epic = service.create_epic(s, project_id=pid, title=title)
            story = service.create_story(s, epic_id=epic.id, title=f"Story of {title}")
            for i, st in enumerate(statuses):
                t = service.create_task(
                    s, project_id=pid, story_id=story.id, title=f"T{i} of {title}",
                )
                t.status = st
        s.commit()
        return admin.id, member.id, p1.id, p2.id


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def test_overview_structure_and_counts(seeded):
    admin_id, member_id, p1_id, p2_id = seeded
    with SessionLocal() as s:
        ov = service.get_overview(s, admin_id)
    # 结构契约
    assert set(ov.keys()) == {"counts", "projects", "status_distribution", "activity_7d"}
    assert set(ov["counts"].keys()) == {"projects", "epics", "stories", "tasks", "done_tasks"}
    # admin 全量：2 项目 / 2 epic / 4 story（每 epic 自动 1 默认）/ 14 任务（自动模板 8 + 手动 6）/ 3 done
    assert ov["counts"]["projects"] == 2
    assert ov["counts"]["epics"] == 2
    assert ov["counts"]["stories"] == 4
    assert ov["counts"]["tasks"] == 14
    assert ov["counts"]["done_tasks"] == 3
    # projects 列表：2 条，按 total 降序
    assert len(ov["projects"]) == 2
    assert ov["projects"][0]["total"] >= ov["projects"][1]["total"]
    by_id = {row["id"]: row for row in ov["projects"]}
    assert by_id[p1_id]["total"] == 9
    assert by_id[p1_id]["done"] == 2
    assert by_id[p1_id]["percent"] == round(2 / 9 * 100)
    assert by_id[p2_id]["total"] == 5
    assert by_id[p2_id]["done"] == 1
    assert by_id[p2_id]["percent"] == 20
    # 状态分布：全部状态键，计数正确
    dist = {row["status"]: row["count"] for row in ov["status_distribution"]}
    assert set(dist.keys()) == set(service.ALL_STATUSES)
    assert dist["done"] == 3
    assert dist["in_progress"] == 1
    # Story 265：原 backlog 9 个 + 手动 1 个 → todo 共 10 个
    assert dist["todo"] == 10
    # activity_7d：恰好 7 天，按日升序
    assert len(ov["activity_7d"]) == 7
    days = [row["day"] for row in ov["activity_7d"]]
    assert days == sorted(days)
    assert all(row["count"] >= 0 for row in ov["activity_7d"])


def test_overview_visibility_member_and_anon(seeded):
    admin_id, member_id, p1_id, p2_id = seeded
    with SessionLocal() as s:
        ov_member = service.get_overview(s, member_id)
        ov_anon = service.get_overview(s, None)
    # 普通用户仅见成员项目 P1
    assert ov_member["counts"]["projects"] == 1
    assert ov_member["counts"]["tasks"] == 9  # 自动模板 4 + 手动 5
    assert [row["id"] for row in ov_member["projects"]] == [p1_id]
    # 未登录：空
    assert ov_anon["counts"]["projects"] == 0
    assert ov_anon["projects"] == []
    assert ov_anon["status_distribution"] == []
    assert ov_anon["activity_7d"] == []


def test_overview_api_endpoint_direct(seeded):
    # REQUIRE_AUTH=0（本地开放模式）：匿名调用返回 200 空统计
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    resp = client.get("/api/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "counts" in data and "projects" in data
    # 本地模式匿名不注入身份 → 空（与 /api/projects 一致）
    assert data["counts"]["projects"] == 0
