#!/usr/bin/env python3
"""Smoke test for Task #33 (FastAPI CRUD) & Task #34 (MCP shares service layer).

Tests against the running Docker API at http://localhost:8000.
Verifies all core CRUD operations: Projects, Epics, Stories, Tasks, Comments, Search, Sprint.
"""
import json
import sys
import time
import httpx

BASE = "http://localhost:8000"
PASS = 0
FAIL = 0
ERRORS = []
_auth_token = None
_test_id = str(int(time.time()))[-6:]  # unique suffix to avoid key conflicts


def get_headers():
    if _auth_token:
        return {"Authorization": f"Bearer {_auth_token}"}
    return {}


def setup_auth():
    global _auth_token
    r = httpx.post(f"{BASE}/api/auth/register", json={"username": "test_reviewer", "password": "test12345678"})
    if r.status_code == 201:
        _auth_token = r.json()["token"]
        return
    r = httpx.post(f"{BASE}/api/auth/login", json={"username": "test_reviewer", "password": "test12345678"})
    if r.status_code == 200:
        _auth_token = r.json()["token"]
        return
    raise RuntimeError(f"Auth failed: register={r.status_code}, body={r.text}")


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        ERRORS.append(f"{name}: {detail}")
        print(f"  \u2717 {name} \u2014 {detail}")


def test_meta():
    print("\n[1] Meta endpoint")
    r = httpx.get(f"{BASE}/api/meta")
    check("GET /api/meta 200", r.status_code == 200, f"status={r.status_code}")
    data = r.json()
    check("meta has types", "types" in data, str(data.keys()))
    check("meta has statuses", "statuses" in data, str(data.keys()))
    check("meta has priorities", "priorities" in data, str(data.keys()))
    check("meta has sprint_statuses", "sprint_statuses" in data, str(data.keys()))
    check("statuses includes in_review", "in_review" in data["statuses"], str(data["statuses"]))


def test_project_crud():
    print("\n[2] Project CRUD")
    h = get_headers()
    r = httpx.post(f"{BASE}/api/projects", json={"name": f"TestProj_CRUD_{_test_id}", "key": f"TC{_test_id}", "description": "test"}, headers=h)
    check("POST /api/projects 201", r.status_code == 201, f"status={r.status_code}, body={r.text}")
    pid = r.json()["id"]
    check("project has id", pid > 0, str(r.json()))

    r = httpx.get(f"{BASE}/api/projects/{pid}", headers=h)
    check("GET /api/projects/{id} 200", r.status_code == 200, f"status={r.status_code}")
    check("project name matches", r.json()["name"] == f"TestProj_CRUD_{_test_id}", r.json().get("name", ""))

    r = httpx.get(f"{BASE}/api/projects", headers=h)
    check("GET /api/projects 200", r.status_code == 200, f"status={r.status_code}")
    check("project in list", any(p["id"] == pid for p in r.json()), "not found in list")

    r = httpx.patch(f"{BASE}/api/projects/{pid}", json={"name": "TestProj_UPDATED"}, headers=h)
    check("PATCH /api/projects/{id} 200", r.status_code == 200, f"status={r.status_code}")
    check("project name updated", r.json()["name"] == "TestProj_UPDATED", r.json().get("name", ""))

    r = httpx.delete(f"{BASE}/api/projects/{pid}", headers=h)
    check("DELETE /api/projects/{id} 200", r.status_code == 200, f"status={r.status_code}")

    r = httpx.get(f"{BASE}/api/projects/{pid}", headers=h)
    check("GET deleted project 404", r.status_code == 404, f"status={r.status_code}")


def test_epic_crud():
    print("\n[3] Epic CRUD")
    h = get_headers()
    r = httpx.post(f"{BASE}/api/projects", json={"name": f"TestEpicProj_{_test_id}", "key": f"TE{_test_id}"}, headers=h)
    pid = r.json()["id"]

    r = httpx.post(f"{BASE}/api/projects/{pid}/epics", json={"title": "Test Epic"}, headers=h)
    check("POST /api/projects/{pid}/epics 201", r.status_code == 201, f"status={r.status_code}, body={r.text}")
    eid = r.json()["id"]

    r = httpx.get(f"{BASE}/api/epics/{eid}", headers=h)
    check("GET /api/epics/{id} 200", r.status_code == 200, f"status={r.status_code}")

    r = httpx.get(f"{BASE}/api/projects/{pid}/epics", headers=h)
    check("GET /api/projects/{pid}/epics 200", r.status_code == 200, f"status={r.status_code}")
    check("epic in list", any(e["id"] == eid for e in r.json()), "not found")

    r = httpx.patch(f"{BASE}/api/epics/{eid}", json={"title": "Updated Epic", "status": "in_progress"}, headers=h)
    check("PATCH /api/epics/{id} 200", r.status_code == 200, f"status={r.status_code}")
    check("epic title updated", r.json()["title"] == "Updated Epic", r.json().get("title", ""))

    r = httpx.delete(f"{BASE}/api/epics/{eid}", headers=h)
    check("DELETE /api/epics/{id} 200", r.status_code == 200, f"status={r.status_code}")

    httpx.delete(f"{BASE}/api/projects/{pid}", headers=h)


def test_story_crud():
    print("\n[4] Story CRUD")
    h = get_headers()
    r = httpx.post(f"{BASE}/api/projects", json={"name": f"TestStoryProj_{_test_id}", "key": f"TS{_test_id}"}, headers=h)
    pid = r.json()["id"]
    r = httpx.post(f"{BASE}/api/projects/{pid}/epics", json={"title": "Story Epic"}, headers=h)
    eid = r.json()["id"]

    r = httpx.post(f"{BASE}/api/epics/{eid}/stories", json={"title": "Test Story"}, headers=h)
    check("POST /api/epics/{eid}/stories 201", r.status_code == 201, f"status={r.status_code}, body={r.text}")
    sid = r.json()["id"]

    r = httpx.get(f"{BASE}/api/stories/{sid}", headers=h)
    check("GET /api/stories/{id} 200", r.status_code == 200, f"status={r.status_code}")

    r = httpx.get(f"{BASE}/api/epics/{eid}/stories", headers=h)
    check("GET /api/epics/{eid}/stories 200", r.status_code == 200, f"status={r.status_code}")

    r = httpx.patch(f"{BASE}/api/stories/{sid}", json={"title": "Updated Story", "status": "in_progress"}, headers=h)
    check("PATCH /api/stories/{id} 200", r.status_code == 200, f"status={r.status_code}")
    check("story title updated", r.json()["title"] == "Updated Story", r.json().get("title", ""))

    r = httpx.delete(f"{BASE}/api/stories/{sid}", headers=h)
    check("DELETE /api/stories/{id} 200", r.status_code == 200, f"status={r.status_code}")

    httpx.delete(f"{BASE}/api/epics/{eid}", headers=h)
    httpx.delete(f"{BASE}/api/projects/{pid}", headers=h)


def test_task_crud():
    print("\n[5] Task CRUD")
    h = get_headers()
    r = httpx.post(f"{BASE}/api/projects", json={"name": f"TestTaskProj_{_test_id}", "key": f"TT{_test_id}"}, headers=h)
    pid = r.json()["id"]
    r = httpx.post(f"{BASE}/api/projects/{pid}/epics", json={"title": "Task Epic"}, headers=h)
    eid = r.json()["id"]
    r = httpx.post(f"{BASE}/api/epics/{eid}/stories", json={"title": "Task Story"}, headers=h)
    sid = r.json()["id"]

    r = httpx.post(f"{BASE}/api/stories/{sid}/tasks",
                   json={"project_id": pid, "title": "Test Task", "type": "task", "priority": "high"}, headers=h)
    check("POST /api/stories/{sid}/tasks 201", r.status_code == 201, f"status={r.status_code}, body={r.text}")
    tid = r.json()["id"]

    r = httpx.get(f"{BASE}/api/tasks/{tid}", headers=h)
    check("GET /api/tasks/{id} 200", r.status_code == 200, f"status={r.status_code}")
    check("task title matches", r.json()["title"] == "Test Task", r.json().get("title", ""))
    check("task priority matches", r.json()["priority"] == "high", r.json().get("priority", ""))

    r = httpx.get(f"{BASE}/api/stories/{sid}/tasks", headers=h)
    check("GET /api/stories/{sid}/tasks 200", r.status_code == 200, f"status={r.status_code}")
    check("task in list", any(t["id"] == tid for t in r.json()), "not found")

    r = httpx.patch(f"{BASE}/api/tasks/{tid}", json={"title": "Updated Task", "priority": "highest"}, headers=h)
    check("PATCH /api/tasks/{id} 200", r.status_code == 200, f"status={r.status_code}")
    check("task title updated", r.json()["title"] == "Updated Task", r.json().get("title", ""))

    r = httpx.put(f"{BASE}/api/tasks/{tid}/status", json={"status": "todo"}, headers=h)
    check("PUT status todo 200", r.status_code == 200, f"status={r.status_code}, body={r.text}")
    r = httpx.put(f"{BASE}/api/tasks/{tid}/status", json={"status": "in_progress"}, headers=h)
    check("PUT status in_progress 200", r.status_code == 200, f"status={r.status_code}, body={r.text}")
    r = httpx.put(f"{BASE}/api/tasks/{tid}/status", json={"status": "in_review"}, headers=h)
    check("PUT status in_review 200", r.status_code == 200, f"status={r.status_code}, body={r.text}")
    r = httpx.put(f"{BASE}/api/tasks/{tid}/status", json={"status": "done"}, headers=h)
    check("PUT status done 200", r.status_code == 200, f"status={r.status_code}, body={r.text}")

    r = httpx.post(f"{BASE}/api/tasks/{tid}/spec/append", json={"text": "Test spec content"}, headers=h)
    check("POST spec/append 200", r.status_code == 200, f"status={r.status_code}, body={r.text}")

    r = httpx.delete(f"{BASE}/api/tasks/{tid}", headers=h)
    check("DELETE /api/tasks/{id} 200", r.status_code == 200, f"status={r.status_code}")

    httpx.delete(f"{BASE}/api/stories/{sid}", headers=h)
    httpx.delete(f"{BASE}/api/epics/{eid}", headers=h)
    httpx.delete(f"{BASE}/api/projects/{pid}", headers=h)


def test_comments():
    print("\n[6] Comments")
    h = get_headers()
    r = httpx.post(f"{BASE}/api/projects", json={"name": f"TestCommentProj_{_test_id}", "key": f"TC{_test_id}"}, headers=h)
    pid = r.json()["id"]
    r = httpx.post(f"{BASE}/api/projects/{pid}/epics", json={"title": "C Epic"}, headers=h)
    eid = r.json()["id"]
    r = httpx.post(f"{BASE}/api/epics/{eid}/stories", json={"title": "C Story"}, headers=h)
    sid = r.json()["id"]
    r = httpx.post(f"{BASE}/api/stories/{sid}/tasks",
                   json={"project_id": pid, "title": "C Task"}, headers=h)
    tid = r.json()["id"]

    r = httpx.post(f"{BASE}/api/tasks/{tid}/comments", json={"author": "tester", "content": "Test comment"}, headers=h)
    check("POST comment 201", r.status_code == 201, f"status={r.status_code}, body={r.text}")
    cid = r.json()["id"]

    r = httpx.get(f"{BASE}/api/tasks/{tid}/comments", headers=h)
    check("GET comments 200", r.status_code == 200, f"status={r.status_code}")
    check("comment in list", any(c["id"] == cid for c in r.json()), "not found")

    r = httpx.delete(f"{BASE}/api/comments/{cid}", headers=h)
    check("DELETE comment 200", r.status_code == 200, f"status={r.status_code}")

    httpx.delete(f"{BASE}/api/tasks/{tid}", headers=h)
    httpx.delete(f"{BASE}/api/stories/{sid}", headers=h)
    httpx.delete(f"{BASE}/api/epics/{eid}", headers=h)
    httpx.delete(f"{BASE}/api/projects/{pid}", headers=h)


def test_search():
    print("\n[7] Search")
    h = get_headers()
    r = httpx.get(f"{BASE}/api/tasks", params={"status": "in_review"}, headers=h)
    check("GET /api/tasks?status=in_review 200", r.status_code == 200, f"status={r.status_code}")
    check("search returns list", isinstance(r.json(), list), str(type(r.json())))

    r = httpx.get(f"{BASE}/api/tasks", params={"q": "FastAPI"}, headers=h)
    check("GET /api/tasks?q=FastAPI 200", r.status_code == 200, f"status={r.status_code}")

    r = httpx.get(f"{BASE}/api/tasks", params={"priority": "high"}, headers=h)
    check("GET /api/tasks?priority=high 200", r.status_code == 200, f"status={r.status_code}")


def test_sprint():
    print("\n[8] Sprint CRUD")
    h = get_headers()
    r = httpx.post(f"{BASE}/api/projects", json={"name": f"TestSprintProj_{_test_id}", "key": f"TR{_test_id}"}, headers=h)
    pid = r.json()["id"]

    r = httpx.post(f"{BASE}/api/projects/{pid}/sprints",
                   json={"title": "Sprint 1", "goal": "Test sprint"}, headers=h)
    check("POST sprint 201", r.status_code == 201, f"status={r.status_code}, body={r.text}")
    sid = r.json()["id"]

    r = httpx.get(f"{BASE}/api/sprints/{sid}", headers=h)
    check("GET sprint 200", r.status_code == 200, f"status={r.status_code}")

    r = httpx.get(f"{BASE}/api/projects/{pid}/sprints", headers=h)
    check("GET sprints list 200", r.status_code == 200, f"status={r.status_code}")

    r = httpx.post(f"{BASE}/api/sprints/{sid}/activate", headers=h)
    check("POST activate 200", r.status_code == 200, f"status={r.status_code}, body={r.text}")

    r = httpx.post(f"{BASE}/api/sprints/{sid}/complete", headers=h)
    check("POST complete 200", r.status_code == 200, f"status={r.status_code}, body={r.text}")

    r = httpx.delete(f"{BASE}/api/sprints/{sid}", headers=h)
    check("DELETE sprint 200", r.status_code == 200, f"status={r.status_code}")

    httpx.delete(f"{BASE}/api/projects/{pid}", headers=h)


def test_error_handling():
    print("\n[9] Error Handling")
    h = get_headers()
    r = httpx.get(f"{BASE}/api/projects/999999", headers=h)
    check("GET nonexistent project 404", r.status_code == 404, f"status={r.status_code}")

    r = httpx.delete(f"{BASE}/api/tasks/999999", headers=h)
    check("DELETE nonexistent task 404", r.status_code == 404, f"status={r.status_code}")

    r = httpx.post(f"{BASE}/api/projects", json={"name": ""}, headers=h)
    check("POST empty project name 422", r.status_code == 422, f"status={r.status_code}")


if __name__ == "__main__":
    print("=" * 60)
    print("AgentBoard CRUD Smoke Test")
    print(f"API: {BASE}")
    print("=" * 60)

    try:
        setup_auth()
        print(f"Auth: token acquired for test_reviewer")
        test_meta()
        test_project_crud()
        test_epic_crud()
        test_story_crud()
        test_task_crud()
        test_comments()
        test_search()
        test_sprint()
        test_error_handling()
    except Exception as e:
        FAIL += 1
        ERRORS.append(f"Exception: {e}")
        print(f"\n!! Exception: {e}")

    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    if ERRORS:
        print("\nFailures:")
        for e in ERRORS:
            print(f"  - {e}")
    print("=" * 60)

    sys.exit(0 if FAIL == 0 else 1)
