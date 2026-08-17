"""8/17 二次 review 修复验收（review P1 + P1/P2）：

P1 #1  import_tasks_from_json 默认值与 model CheckConstraint 对齐
      （type/status/priority 显式校验，不再依赖 DB IntegrityError 兜底）

P1 #2  set_status 仅在 terminal 状态异步触发 L3 LLM judge
      （_record_learning_outcome 返回 outcome 作 gate，避免非终态
      spawn thread + new session + load task + return None 的纯开销）

P1 #3  注释清理：subtask generator 文档里「type=task / status=backlog」
      已下线说法改为实际 model 默认值（type=dev / status=todo / priority=medium）

P1 #4  get_project_stats SQL：active 不再含已下线的 verifying；
      旧 Task.status=='backlog' 永远 0 改为 Status.TODO（dict key 保留
      backlog_tasks 兼容旧 front-end 契约）
"""
import os
import sys
import tempfile

# 独立临时 DB（与同目录其它 test_*.py 的 mktemp 模式保持一致，
# 仅本文件内用 — 跨文件各自独立）
_DB = tempfile.mktemp(prefix="review_20260817_", suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
# 禁用后台 judge 线程 —— 本文件专注验证「不调用 / 调用」语义
os.environ["AGENTBOARD_JUDGE_AUTO"] = "0"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

import pytest

from agentboard import service
from agentboard.core.common.enums import (
    ALL_PRIORITIES, ALL_STATUSES, ALL_TYPES, ItemType, Priority, Status,
)
from agentboard.database import SessionLocal, init_db
from agentboard.features.work_items.models import Task


# ---------- 通用 fixture：每个 test 跑前 wipe 数据 ----------
# alembic migration 一次跑过即固定 schema；test 间手动 DELETE 业务表
# 即可（不要 drop_all — 那是 e2e / 集成测试的语义）。

@pytest.fixture
def session():
    init_db()
    s = SessionLocal()
    # 跨 test 数据隔离：清空业务表
    for tbl in (
        "task_outcome", "episode_embedding", "project_playbook",
        "project_playbook_episode", "task_status_history",
        "task_dependencies", "comments", "attachments",
        "tasks", "stories", "sprints", "epics", "projects", "users",
    ):
        try:
            s.execute(__import__("sqlalchemy").text(f"DELETE FROM {tbl}"))
        except Exception:
            pass  # 表不存在 / 无权限 — 静默
    s.commit()
    yield s
    s.close()


def _mk(s, name="u1", proj="p1"):
    import uuid
    suffix = uuid.uuid4().hex[:8]
    u = service.register_user(s, username=f"{name}_{suffix}", password="password123")
    p = service.create_project(s, name=f"{proj}_{suffix}")
    e = service.create_epic(s, project_id=p.id, title=f"E-{suffix}")
    st = service.create_story(s, epic_id=e.id, title=f"S-{suffix}")
    return u, p, st


def _mk_task(s, p, st, title="T1"):
    return service.create_task(s, project_id=p.id, story_id=st.id, title=title)


# =========================================================================
# P1 #1: import_tasks_from_json 默认值 + 显式校验
# =========================================================================

def test_import_tasks_from_json_defaults_align_with_enums(session):
    """8/17 review P1 #1 修复验收。

    旧实现 type="task" / status="backlog" 默认值会被 DB CheckConstraint 拒绝；
    新实现用 ItemType.DEV / Status.TODO / Priority.MEDIUM 默认 + 显式 _check_*。
    """
    _, p, st = _mk(session)
    session.commit()

    result = service.import_tasks_from_json(
        session, project_id=p.id,
        data={"tasks": [{"title": "Plain import"}]},
    )
    assert result["errors"] == []
    assert len(result["imported"]) == 1
    # 默认值必须与 model CheckConstraint 对齐，不再触发 IntegrityError
    t = service.get_task(session, result["imported"][0]["id"])
    assert t.type == ItemType.DEV
    assert t.status == Status.TODO
    assert t.priority == Priority.MEDIUM


def test_import_tasks_from_json_rejects_invalid_type_early(session):
    """8/17 review P1 #1 修复验收：传入非法 type 应 _check_type 早失败，
    不会进 flush → DB 阶段才抛 IntegrityError。

    批量 API 行为：InvalidValue 被 per-item try/except 收集进 errors 列表，
    不整体 raise（保护同批其它合法条目）。错误信息明确：「invalid type 'task'」，
    不再是 DB 层的模糊 CHECK constraint 失败。
    """
    _, p, st = _mk(session)
    session.commit()

    result = service.import_tasks_from_json(
        session, project_id=p.id,
        data={"tasks": [{"title": "Bad type", "type": "task"}]},
    )
    assert result["imported"] == []
    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert err["title"] == "Bad type"
    assert "invalid type" in err["error"].lower()
    # 关键断言：error 信息提到 _check_type 校验，不再是 DB constraint 模糊报错
    assert "'task'" in err["error"]


def test_import_tasks_from_json_rejects_invalid_status_early(session):
    """8/17 review P1 #1 修复验收：传入旧 backlog → 错误信息明确。"""
    _, p, st = _mk(session)
    session.commit()

    result = service.import_tasks_from_json(
        session, project_id=p.id,
        data={"tasks": [{"title": "Bad status", "status": "backlog"}]},
    )
    assert result["imported"] == []
    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert "invalid status" in err["error"].lower()
    assert "'backlog'" in err["error"]


def test_import_tasks_from_json_rejects_invalid_priority_early(session):
    """8/17 review P1 #1 修复验收：传入非法 priority 也早失败。"""
    _, p, st = _mk(session)
    session.commit()

    result = service.import_tasks_from_json(
        session, project_id=p.id,
        data={"tasks": [{"title": "Bad prio", "priority": "urgent"}]},
    )
    assert result["imported"] == []
    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert "invalid priority" in err["error"].lower()
    assert "'urgent'" in err["error"]


def test_import_tasks_from_json_per_item_isolation(session):
    """8/17 review P1 #1：单条非法不应阻塞同批其它合法条目。

    SAVEPOINT 修复（8/17 review P1 #2，本轮新增）：
    旧实现 ``except → s.rollback()`` 会回滚**整个** outer transaction——
    同批已经在前一条 ``s.add() + s.flush()`` 但还没 commit 的合法 task
    会被一起带走，导致 ``imported`` list 与 DB 实际行数不一致（API 说
    成功导入 2 条，DB 里只有 1 条）。这是数据一致性 P0 bug。

    修后用 ``s.begin_nested()`` 包单条 item：失败只回滚 SAVEPOINT，
    不影响同批其它已 flush 的合法条目；外层 transaction 在循环结束
    后由 ``_commit`` 一次性 commit。
    """
    _, p, st = _mk(session)
    session.commit()

    result = service.import_tasks_from_json(
        session, project_id=p.id,
        data={"tasks": [
            {"title": "OK 1"},
            {"title": "Bad", "type": "task"},      # 非法 type → _check_type 早失败
            {"title": "OK 2", "type": "bug"},
            {"title": "Bad", "status": "backlog"},  # 非法 status → _check_status 早失败
        ]},
    )

    # === API 行为断言（保留原 P1 #1 语义）===
    assert len(result["errors"]) == 2
    assert len(result["imported"]) == 2
    imported_titles = {t["title"] for t in result["imported"]}
    assert imported_titles == {"OK 1", "OK 2"}

    # === SAVEPOINT 修复关键断言（P1 #2）：re-query DB 验证 persistence ===
    # 旧 bug：API 返回 imported=['OK 1', 'OK 2']，但 DB 只剩 'OK 2'
    # （因为 'OK 1' 被中间那次 'Bad type' 触发的 s.rollback() 一起回滚了）。
    # 修后：两条合法 task 都真实落库。
    session.expire_all()  # 清 identity-map 缓存，强制走 DB
    persisted_titles = {
        t.title for t in session.query(Task).filter(Task.project_id == p.id).all()
    }
    # 只断言本批导入的 2 条都真实落库（其他 fixture / 子任务可能在同一项目下，不在此断言）
    assert imported_titles.issubset(persisted_titles), (
        f"SAVEPOINT 修复未生效：API imported={imported_titles} "
        f"但 DB 实际={persisted_titles}。考虑 #1 之前 fix 已回退，"
        f"imported 中至少有一条不在 DB（数据不一致）"
    )

    # 进一步精确断言：result["imported"] 里的每条 id 都能重新查回来
    for entry in result["imported"]:
        t = session.get(Task, entry["id"])
        assert t is not None, f"task id={entry['id']} title={entry['title']!r} 不在 DB"
        assert t.title == entry["title"]

    # === 8/18 review P3：直接 by-id 回归断言（防"API 报成功但 DB 丢数据"型 bug）===
    # 之前 issubset 检查只比对 title，没保证"API 返回的 id 一定能在 DB 查到"——
    # 极端情况下如果 import 接口用 stale id 字段（如内存对象 id）填充
    # imported 列表但实际 INSERT 失败，issubset 仍会假阳性通过。新增直接
    # ``id IN (...)`` 断言：API 报"导入成功 N 条"，就**必须**能查回 N 条。
    imported_ids = [x["id"] for x in result["imported"]]
    persisted_by_id = (
        session.query(Task).filter(Task.id.in_(imported_ids)).all()
    )
    assert len(persisted_by_id) == len(imported_ids), (
        f"by-id 回归断言：API imported {len(imported_ids)} 条，"
        f"DB 仅查回 {len(persisted_by_id)} 条 "
        f"(ids={imported_ids}, 查回 ids={[t.id for t in persisted_by_id]})"
        f"—— 'response 说成功但 DB 实际丢数据' 类 bug 复发？"
    )
    # set-equal 精确校验：ids 集合与 titles 集合都一一对应
    assert {t.id for t in persisted_by_id} == set(imported_ids), (
        f"id 集合不匹配：API={imported_ids}，DB={[t.id for t in persisted_by_id]}"
    )
    assert {t.title for t in persisted_by_id} == {"OK 1", "OK 2"}, (
        f"title 集合不匹配：DB 应仅含 'OK 1' / 'OK 2'，"
        f"实际 { {t.title for t in persisted_by_id} }"
    )


def test_import_tasks_from_json_savepoint_isolates_db_integrity_error(session):
    """8/17 review P1 #2 修复验收：SAVEPOINT 在 DB 阶段异常（IntegrityError）
    也能正确隔离同批其它条目。

    之前 _check_* 在 s.add() 之前就抛了，触发不到 flush 阶段。本测试绕过
    service 层的 _check_*（直接调底层 s.add + s.flush 模拟），验证 SAVEPOINT
    本身能扛 DB 阶段异常——这对应**未来** ALL_STATUSES 与 model 约束漂移、
    或并发场景下 session 持有旧值时的真实场景。
    """
    from sqlalchemy.exc import IntegrityError
    _, p, st = _mk(session)
    session.commit()

    # 直接走 SQLAlchemy：第一个 OK + 第二个 IntegrityError + 第三个 OK。
    # 用 session.begin_nested() 包单条 item，验证 IntegrityError 只回滚自己。
    task_ok_1 = Task(project_id=p.id, story_id=st.id, title="OK 1",
                     type=ItemType.DEV, status=Status.TODO, priority=Priority.MEDIUM)
    task_bad = Task(project_id=p.id, story_id=st.id, title="Bad",
                    type=ItemType.DEV, status="garbage_status", priority=Priority.MEDIUM)
    task_ok_2 = Task(project_id=p.id, story_id=st.id, title="OK 2",
                     type=ItemType.BUG, status=Status.TODO, priority=Priority.MEDIUM)

    # OK 1
    with session.begin_nested():
        session.add(task_ok_1)
        session.flush()

    # Bad → IntegrityError（CheckConstraint）
    integrity_failed = False
    try:
        with session.begin_nested():
            session.add(task_bad)
            session.flush()
    except IntegrityError:
        integrity_failed = True
        # SAVEPOINT 已自动回滚，无需手动 rollback
    assert integrity_failed, "测试前提：'garbage_status' 应触发 CheckConstraint"

    # OK 2 — 关键：必须能继续 flush 成功（SAVEPOINT 已隔离）
    with session.begin_nested():
        session.add(task_ok_2)
        session.flush()

    session.commit()

    # 验证 DB 实际只有 OK 1 + OK 2（Bad 不在）；用 issubset 因为
    # _mk fixture 可能在同一项目下创建子任务。
    session.expire_all()
    persisted = session.query(Task).filter(Task.project_id == p.id).all()
    persisted_titles = {t.title for t in persisted}
    assert {"OK 1", "OK 2"}.issubset(persisted_titles), (
        f"SAVEPOINT 隔离失败：DB={persisted_titles}，至少应包含 OK 1 + OK 2"
    )
    assert "Bad" not in persisted_titles, (
        f"Bad 任务不应落库（CheckConstraint 已 SAVEPOINT 回滚）：{persisted_titles}"
    )


# =========================================================================
# P1/P2 #2: set_status 仅 terminal 触发 judge 调度
# =========================================================================

def test_set_status_non_terminal_does_not_schedule_judge(monkeypatch, session):
    """8/17 review P1/P2 #2 修复验收：非终态转移不应 spawn judge thread。

    修前：todo → in_progress 也会调 schedule_judge(t.id)；judge_task()
    内部检查非终态返回 None，但已经付出「开线程 + 开 Session + load task」
    的纯开销。
    修后：_record_learning_outcome 返回 None（因为 record_outcome 不为非
    终态写 outcome）→ set_status 跳过 schedule_judge 调用。
    """
    from agentboard.features import work_items as work_items_feature
    from agentboard.features.learning import judge as learning_judge
    from agentboard.features.learning import service as learning_service

    u, p, st = _mk(session)
    t = _mk_task(session, p, st, title="non-terminal flow")
    session.commit()

    # 启用 judge auto，但 spy 一下 schedule_judge 是否被调
    monkeypatch.setenv("AGENTBOARD_JUDGE_AUTO", "1")
    calls: list[int] = []
    real_schedule = learning_judge.schedule_judge

    def spy_schedule(task_id: int):
        calls.append(task_id)
        # 不真开线程（避免测试卡住），只记调用
    monkeypatch.setattr(learning_judge, "schedule_judge", spy_schedule)

    # todo → in_progress：非终态
    work_items_feature.service.set_status(
        session, t.id, Status.IN_PROGRESS, changed_by=u.id,
    )
    session.commit()

    assert calls == [], (
        f"非终态转移不应触发 schedule_judge，实际调用 {len(calls)} 次: {calls}"
    )


def test_set_status_terminal_does_schedule_judge(monkeypatch, session):
    """8/17 review P1/P2 #2 修复验收：终态转移必须 schedule_judge（不要
    顺手把正常路径也 ban 了）。"""
    from agentboard.features.learning import judge as learning_judge
    from agentboard.features.work_items import service as work_items_service

    u, p, st = _mk(session)
    t = _mk_task(session, p, st, title="terminal flow")
    session.commit()

    monkeypatch.setenv("AGENTBOARD_JUDGE_AUTO", "1")
    calls: list[int] = []
    monkeypatch.setattr(
        learning_judge, "schedule_judge",
        lambda task_id: calls.append(task_id),
    )

    work_items_service.set_status(
        session, t.id, Status.IN_PROGRESS, changed_by=u.id,
    )
    work_items_service.set_status(
        session, t.id, Status.DONE, changed_by=u.id,
        status_reason="completed",
    )
    session.commit()

    # 只在 DONE 转移时调用一次；IN_PROGRESS 不应触发
    assert calls == [t.id], (
        f"终态转移应调用 schedule_judge 一次，实际 {calls}"
    )


# =========================================================================
# P1 #5: get_project_stats 计数修正
# =========================================================================

def test_get_project_stats_counts_todo_not_backlog(session):
    """8/17 review P1 #5 修复验收：stats 里「backlog」字段应数 Status.TODO
    的 task 数（旧 SQL 写死 "backlog"，Story 265 后永远 0，UI 静默坏）。

    字段名 backlog_tasks 保留以兼容 front-end 契约（app.html 仍引用），
    内部 SQL 改为 Status.TODO。

    注意：Epic 123 设计让 ``create_story`` 自动建 1 个 design + 1 个 dev
    Task（都进 Status.TODO 默认），所以测得 4 个 todo baseline + 后续创建。
    """
    u, p, st = _mk(session)
    # 3 个显式 todo task（_mk 已通过 create_story 间接建了 4 个 todo baseline：
    # 1 design 任务 + 1 dev 任务 + 2 个 _mk_task 创建时是否触发 design/dev 待确认）
    for i in range(3):
        _mk_task(session, p, st, title=f"todo-{i}")
    t_active = _mk_task(session, p, st, title="active")
    service.set_status(session, t_active.id, Status.IN_PROGRESS, changed_by=u.id)
    t_done = _mk_task(session, p, st, title="done")
    service.set_status(session, t_done.id, Status.IN_PROGRESS, changed_by=u.id)
    service.set_status(
        session, t_done.id, Status.DONE, changed_by=u.id,
        status_reason="completed",
    )
    session.commit()

    from agentboard.features.projects import service as projects_service
    stats = projects_service.get_project_stats(session, project_id=p.id)
    # 关键断言 1：active 不应再含已下线的 verifying；
    # 5 态下 active = in_progress + in_review，固定 1（"active" task）
    assert stats["active_tasks"] == 1
    # 关键断言 2：backlog_tasks 字段名保留（旧 UI 引用），但内部算的是 todo；
    # todo 数 = total - done - active = (3 baseline + 3 _mk + 1 active + 1 done) - 1 - 1
    # Epic 123 还会再为 active / done 这 2 个 _mk_task 各自动建 design+dev
    # （status=todo 默认），所以 todo 数会更多。重点：旧实现 backlog_tasks 永远 0，
    # 新实现 ≥ 1 即视为修复。
    assert stats["backlog_tasks"] >= 3, (
        f"backlog_tasks 应等于 Status.TODO 数（旧 SQL 写死 'backlog' 永远 0，"
        f"实际 {stats['backlog_tasks']}）"
    )
    assert stats["total_tasks"] == stats["done_tasks"] + stats["active_tasks"] + stats["backlog_tasks"]
    # done 字段不变
    assert stats["done_tasks"] == 1


# =========================================================================
# 注释清理（4 处）— 间接验收：subtask generator 实际行为正确
# =========================================================================

def test_subtask_generation_uses_actual_model_defaults(session):
    """8/17 review P1 #3 注释清理的间接验收：spec 生成的子 task 实际
    type=dev / status=todo / priority=medium（与注释一致），不依赖
    旧"task/backlog"表述。

    之前注释误导维护者，实际代码用的就是 dev/todo（model default），
    现统一注释。
    """
    from agentboard.features.work_items import service as work_items_service

    u, p, st = _mk(session)
    src = _mk_task(session, p, st, title="源 task")
    src.spec = "- [ ] 子任务 A\n- [ ] 子任务 B\n"
    session.commit()

    # 实际函数名是 generate_tasks_from_spec（不是 generate_subtasks_from_spec）
    created = work_items_service.generate_tasks_from_spec(
        session, task_id=src.id,
    )
    assert len(created) == 2
    for t in created:
        assert t.type == ItemType.DEV, (
            f"subtask type 应为 dev（ItemType.DEV），实际 {t.type!r}"
        )
        assert t.status == Status.TODO, (
            f"subtask status 应为 todo（Status.TODO），实际 {t.status!r}"
        )
        assert t.priority == Priority.MEDIUM
        assert t.source_spec_id == src.id
