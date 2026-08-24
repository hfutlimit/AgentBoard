"""Local quickstart: proposal + create ticket + task workflow 端到端 smoke.

AgentBoard Proposal 状态机：
    PENDING → QUEUED → ANALYZING → AWAITING → ANSWERED → ANALYZING (next round)
             → CONVERGED → TICKET_PREPARING → TICKET_CREATED

Task 状态机 (5 态)：
    TODO → IN_PROGRESS → IN_REVIEW → DONE
                   ↑          ↓ (reject 5 轮 → BLOCKED 护栏)
                   └──────────┘

前置：
- AgentBoard FastAPI 跑在 AGENTBOARD_API_URL（默认 18001）
- AGENTBOARD_REQUIRE_AUTH=0 + AGENTBOARD_ALLOW_REGISTRATION=1（dev 模式）
- DB 自动建表 + alembic upgrade head

执行：
1. 注册 admin（首用户自动 is_admin=True）
2. 创建 project
3. Proposal 全流程：create → status=queued → claim(→analyzing) →
   questions(→awaiting) → answer(→answered) → claim(→analyzing) ... →
   finalize(→converged)
4. Proposal → Ticket：ticket-requests:execute（epic → story → task）
5. Task workflow：claim → in_progress → submit-review → assign-reviewer
   → review approve → done
6. 打印终态摘要

跑法：
    AGENTBOARD_API_URL=http://127.0.0.1:18001 python scripts/local_e2e_proposal_workflow.py
"""
from __future__ import annotations

import os
import sys
import uuid

import httpx

BASE = os.environ.get("AGENTBOARD_API_URL", "http://127.0.0.1:18001")


def step(name: str) -> None:
    print(f"\n--- {name} ---")


def assert_ok(r: httpx.Response, expect: int | tuple[int, ...] = 200) -> dict:
    if isinstance(expect, int):
        expect = (expect,)
    if r.status_code not in expect:
        raise RuntimeError(f"HTTP {r.status_code} (expected {expect}): {r.text[:500]}")
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return {}


def post(c: httpx.Client, path: str, expect: int | tuple[int, ...] = 200, **kw) -> dict:
    r = c.post(path, **kw)
    return assert_ok(r, expect)


def put(c: httpx.Client, path: str, expect: int | tuple[int, ...] = 200, **kw) -> dict:
    r = c.put(path, **kw)
    return assert_ok(r, expect)


def patch(c: httpx.Client, path: str, expect: int | tuple[int, ...] = 200, **kw) -> dict:
    r = c.patch(path, **kw)
    return assert_ok(r, expect)


def main() -> int:
    suffix = uuid.uuid4().hex[:8]
    admin = f"admin_{suffix}"
    admin_pw = f"AdminPass_{suffix}"
    project_name = f"Demo-{suffix}"
    project_key = f"D{suffix[:4].upper()}"
    proposal_title = f"试用 proposal 流程 {suffix}"
    proposal_content = (
        "# 背景\n测试 proposal + create ticket + task workflow 闭环。\n"
        "# 目标\n让本地 dev 用户 5 分钟跑通。"
    )
    ticket_spec = (
        "## 验收标准\n"
        "1. POST /api/tasks/{id}/status 走通 todo→in_progress→in_review→done\n"
        "2. SPEC markdown 渲染正常"
    )

    print(f"== AgentBoard base: {BASE} ==")

    with httpx.Client(base_url=BASE, timeout=30) as c:
        # ===== 1. 注册 admin（首用户自动 is_admin）=====
        step("1. 注册 admin")
        body = post(c, "/api/auth/register", (200, 201),
                    json={"username": admin, "password": admin_pw})
        admin_user_id = body.get("id")
        token = body.get("token")
        if not token:
            body = post(c, "/api/auth/login",
                        json={"username": admin, "password": admin_pw})
            token = body["token"]
        H = {"Authorization": f"Bearer {token}"}
        print(f"   user_id={admin_user_id} username={body.get('username')}")

        # ===== 2. project =====
        step("2. 创建 project")
        proj = post(c, "/api/projects", 201,
                    json={"name": project_name, "key": project_key}, headers=H)
        pid = proj["id"]
        owner_id = proj.get("owner_id") or admin_user_id
        print(f"   project_id={pid} key={proj.get('key')} owner_id={owner_id}")

        # ===== 3. proposal =====
        step("3. 创建 proposal (PENDING)")
        prop = post(c, "/api/proposals", 201,
                    json={"project_id": pid, "title": proposal_title,
                          "content": proposal_content}, headers=H)
        prop_id = prop["id"]
        print(f"   proposal_id={prop_id} status={prop.get('status')}")

        # ===== 4. PENDING → QUEUED =====
        step("4. PUT /api/proposals/{id}/status: queued")
        st = put(c, f"/api/proposals/{prop_id}/status",
                 json={"status": "queued"}, headers=H)
        print(f"   status={st.get('status')}")

        # ===== 5. claim → ANALYZING =====
        step("5. claim → ANALYZING")
        post(c, f"/api/proposals/{prop_id}/claim",
             json={"agent": "local-smoke"}, headers=H)
        st = c.get(f"/api/proposals/{prop_id}", headers=H).json()
        print(f"   status={st.get('status')} claimed_by={st.get('claimed_by')}")

        # ===== 6. ask 1 轮 2 问 → AWAITING =====
        step("6. ask 1 轮 2 问 → AWAITING")
        ask_questions = [
            "目标用户是个人还是团队？",
            "是否依赖 .NET 8+ 环境？",
        ]
        post(c, f"/api/proposals/{prop_id}/questions", 201,
             json={"questions": ask_questions}, headers=H)
        st = c.get(f"/api/proposals/{prop_id}", headers=H).json()
        print(f"   status={st.get('status')}")

        # 拉 question id（GET /api/proposals/{pid}/rounds）
        rounds_data = c.get(f"/api/proposals/{prop_id}/rounds", headers=H).json()
        questions = []
        for r in rounds_data or []:
            for q in (r.get("questions") or []):
                if not q.get("answered_at"):
                    questions.append(q)
        print(f"   open_questions={len(questions)} (across {len(rounds_data or [])} rounds)")

        # ===== 7. 答 → ANSWERED =====
        step(f"7. answer {len(questions)} 题 → ANSWERED")
        for q in questions:
            qid = q["id"]
            seq = q.get("seq")
            ans = "个人 + 团队（混合）" if seq == 1 else "否，纯 Python 即可"
            put(c, f"/api/proposals/{qid}/answer", json={"answer": ans}, headers=H)
        st = c.get(f"/api/proposals/{prop_id}", headers=H).json()
        print(f"   status={st.get('status')}")

        # ===== 8. PATCH converged_spec + PUT /status → CONVERGED =====
        step("8a. PATCH converged_spec")
        patch(c, f"/api/proposals/{prop_id}",
              json={"converged_spec": ticket_spec}, headers=H)
        step("8b. PUT /api/proposals/{id}/status: converged")
        # state machine: ANSWERED → CONVERGED 允许
        put(c, f"/api/proposals/{prop_id}/status",
            json={"status": "converged", "error": None}, headers=H)
        st = c.get(f"/api/proposals/{prop_id}", headers=H).json()
        print(f"   status={st.get('status')}")

        # ===== 9. proposal → ticket（一次只产一个 type；proposal 自动 ticket_created 终态）=====
        step("9a. ticket-requests:execute (epic) — proposal 进入 ticket_created 终态")
        body = post(c, "/api/ticket-requests:execute",
                    json={"proposal_id": prop_id, "type": "epic",
                          "title": f"[Epic] {proposal_title}"}, headers=H)
        epic_id = (body.get("ticket") or {}).get("id") or (body.get("request") or {}).get("ticket_id")
        print(f"   epic_id={epic_id} request_status={body.get('request', {}).get('status')}")

        # ticket_created 是终态不能再 execute；story + task 走 REST 直接创建
        step("9b. POST /api/stories 手工挂到 epic")
        story_body = post(c, f"/api/epics/{epic_id}/stories", 201,
                          json={"title": f"[Story] {proposal_title}",
                                "description": ticket_spec}, headers=H)
        story_id = story_body.get("id") or story_body.get("story", {}).get("id")
        print(f"   story_id={story_id}")

        step("9c. POST /api/tasks 手工挂到 story")
        task_body = post(c, f"/api/stories/{story_id}/tasks", 201,
                         json={"project_id": pid, "title": f"[Task] {proposal_title}",
                               "type": "dev", "priority": "high",
                               "description": ticket_spec}, headers=H)
        task_id = task_body.get("id") or task_body.get("task", {}).get("id")
        print(f"   task_id={task_id}")
        assert task_id is not None, f"task create response did not contain an id: {task_body}"
        created_task = c.get(f"/api/tasks/{task_id}", headers=H)
        assert_ok(created_task, 200)
        created_task_body = created_task.json()
        assert created_task_body.get("id") == task_id, created_task_body
        assert created_task_body.get("story_id") == story_id, created_task_body
        assert created_task_body.get("project_id") == pid, created_task_body

        # ===== 10. task workflow =====
        step(f"10. claim task_id={task_id} (TODO → IN_PROGRESS)")
        r = c.post(f"/api/tasks/{task_id}/claim", json={"user_id": owner_id}, headers=H)
        if r.status_code == 422:
            # claim 不带 body 也行
            r = c.post(f"/api/tasks/{task_id}/claim", headers=H)
        assert_ok(r, (200, 204))
        st = c.get(f"/api/tasks/{task_id}", headers=H).json()
        print(f"   status={st.get('status')} assignee_id={st.get('assignee_id')}")

        step("11. submit-review (IN_PROGRESS → IN_REVIEW)")
        post(c, f"/api/tasks/{task_id}/submit-review", headers=H)
        st = c.get(f"/api/tasks/{task_id}", headers=H).json()
        print(f"   status={st.get('status')}")

        step("12. assign-reviewer (admin 自评)")
        r = c.post(f"/api/tasks/{task_id}/assign-reviewer",
                   headers={**H, "X-User-Id": str(admin_user_id)})
        assert_ok(r)
        print(f"   reviewer 已指派")

        step("13. review approve (IN_REVIEW → DONE)")
        r = c.post(f"/api/tasks/{task_id}/review",
                   json={"verdict": "approve", "comment": "looks good"}, headers=H)
        assert_ok(r)
        st = c.get(f"/api/tasks/{task_id}", headers=H).json()
        assert st.get("status") == "done", st
        print(f"   status={st.get('status')}")

        # ===== 11. 终态摘要 =====
        step("DONE · 终态摘要")
        for kind, tid in [("epic", epic_id), ("story", story_id), ("task", task_id)]:
            t = c.get(f"/api/{kind}s/{tid}", headers=H).json()
            status = t.get("status") or "unknown"
            title = (t.get("title") or "")[:60]
            print(f"   {kind:5s} #{tid:>4} status={status:16s} title={title!r}")
        final = c.get(f"/api/proposals/{prop_id}", headers=H).json()
        print(f"   proposal #{prop_id} status={final.get('status')} "
              f"ticket_type={final.get('ticket_type')} ticket_id={final.get('ticket_id')}")

    print("\n== [PASS] proposal + create ticket + task workflow 全链路通过 ==")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n== ❌ FAIL: {e} ==", file=sys.stderr)
        raise
