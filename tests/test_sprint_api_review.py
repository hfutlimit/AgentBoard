#!/usr/bin/env python3
"""Sprint API smoke test for Task #82 review."""
import requests
import json
import sys
import time

BASE = "http://localhost:8000/api"
TOKEN = None
PROJECT_ID = None
SPRINT1_ID = None
SPRINT2_ID = None
TASK1_ID = None
TASK2_ID = None
STORY_ID = None
EPIC_ID = None

passed = 0
failed = 0
errors = []

def header():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        errors.append(f"{name}: {detail}")
        print(f"  FAIL: {name} - {detail}")

print("=" * 60)
print("Sprint API Smoke Test - Task #82")
print("=" * 60)

# Step 1: Login
print("\n[1] Authentication")
resp = requests.post(f"{BASE}/auth/login", json={"username": "reviewer", "password": "Review123!"})
test("Login", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")
if resp.status_code == 200:
    TOKEN = resp.json().get("token")
    test("Token received", TOKEN is not None, "No token in response")
else:
    print("FATAL: Cannot proceed without auth")
    sys.exit(1)

# Step 2: Verify /api/meta has sprint_statuses
print("\n[2] /api/meta contains sprint_statuses")
resp = requests.get(f"{BASE}/meta")
meta = resp.json()
test("Meta returns sprint_statuses", "sprint_statuses" in meta, f"Keys: {list(meta.keys())}")
test("Sprint statuses correct", meta.get("sprint_statuses") == ["planning", "active", "completed"],
     f"Got: {meta.get('sprint_statuses')}")

# Step 3: Create a project for testing
print("\n[3] Setup: Create Project + Epic + Story")
ts = int(time.time())
resp = requests.post(f"{BASE}/projects", headers=header(), json={
    "name": f"SprintTest-{ts}",
    "description": "Test project for Sprint API review"
})
test("Create project", resp.status_code in (200, 201), f"Status {resp.status_code}: {resp.text}")
PROJECT_ID = resp.json().get("id")
test("Project ID received", PROJECT_ID is not None, f"Response: {resp.json()}")

# Create epic
resp = requests.post(f"{BASE}/projects/{PROJECT_ID}/epics", headers=header(), json={
    "title": "Sprint Test Epic",
    "description": "Test epic"
})
test("Create epic", resp.status_code in (200, 201), f"Status {resp.status_code}: {resp.text}")
EPIC_ID = resp.json().get("id")

# Create story
resp = requests.post(f"{BASE}/epics/{EPIC_ID}/stories", headers=header(), json={
    "title": "Sprint Test Story",
    "description": "Test story"
})
test("Create story", resp.status_code in (200, 201), f"Status {resp.status_code}: {resp.text}")
STORY_ID = resp.json().get("id")

# Step 4: Sprint CRUD - Create (uses 'title' not 'name', no 'status' field)
print("\n[4] Sprint CRUD - Create")
resp = requests.post(f"{BASE}/projects/{PROJECT_ID}/sprints", headers=header(), json={
    "title": f"Sprint Alpha {ts}",
    "goal": "Test sprint goal"
})
test("Create sprint", resp.status_code in (200, 201), f"Status {resp.status_code}: {resp.text}")
SPRINT1_ID = resp.json().get("id")
test("Sprint ID received", SPRINT1_ID is not None, f"Response: {resp.json()}")
sprint1 = resp.json()
test("Sprint status is planning (default)", sprint1.get("status") == "planning", f"Got: {sprint1.get('status')}")
test("Sprint title matches", sprint1.get("title", "").startswith("Sprint Alpha"), f"Got: {sprint1.get('title')}")

# Step 5: List Sprints
print("\n[5] Sprint CRUD - List")
resp = requests.get(f"{BASE}/projects/{PROJECT_ID}/sprints", headers=header())
test("List sprints", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")
sprints = resp.json()
test("List contains created sprint", any(s["id"] == SPRINT1_ID for s in sprints), f"Sprints: {[s['id'] for s in sprints]}")

# Step 6: Get single Sprint
print("\n[6] Sprint CRUD - Get by ID")
resp = requests.get(f"{BASE}/sprints/{SPRINT1_ID}", headers=header())
test("Get sprint by ID", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")
test("Sprint title matches", resp.json().get("title", "").startswith("Sprint Alpha"), f"Got: {resp.json().get('title')}")

# Step 7: Update Sprint (PATCH not PUT)
print("\n[7] Sprint CRUD - Update (PATCH)")
resp = requests.patch(f"{BASE}/sprints/{SPRINT1_ID}", headers=header(), json={
    "title": "Sprint Alpha Updated",
    "goal": "Updated goal"
})
test("Update sprint", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")
test("Title updated", resp.json().get("title") == "Sprint Alpha Updated", f"Got: {resp.json().get('title')}")
test("Goal updated", resp.json().get("goal") == "Updated goal", f"Got: {resp.json().get('goal')}")

# Step 8: Activate Sprint
print("\n[8] Sprint Activate")
resp = requests.post(f"{BASE}/sprints/{SPRINT1_ID}/activate", headers=header())
test("Activate sprint", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")
test("Sprint status is active", resp.json().get("status") == "active", f"Got: {resp.json().get('status')}")

# Step 9: Create second sprint and try to activate (single active constraint)
print("\n[9] Single Active Sprint Constraint")
resp = requests.post(f"{BASE}/projects/{PROJECT_ID}/sprints", headers=header(), json={
    "title": f"Sprint Beta {ts}",
    "goal": "Second sprint"
})
test("Create second sprint", resp.status_code in (200, 201), f"Status {resp.status_code}: {resp.text}")
SPRINT2_ID = resp.json().get("id")

resp = requests.post(f"{BASE}/sprints/{SPRINT2_ID}/activate", headers=header())
# Should either fail or auto-deactivate the first sprint
if resp.status_code == 200:
    test("Second sprint activated", True, "")
    # Check if first sprint is no longer active
    resp_check = requests.get(f"{BASE}/sprints/{SPRINT1_ID}", headers=header())
    s1_status = resp_check.json().get("status")
    s2_status = resp.json().get("status")
    test("Only one active sprint (single active constraint)", not (s1_status == "active" and s2_status == "active"),
         f"S1={s1_status}, S2={s2_status}")
elif resp.status_code in (400, 409, 422):
    test("Second sprint activation rejected (single active constraint)", True, f"Status {resp.status_code}: {resp.text}")
else:
    test("Second sprint activation", False, f"Unexpected status {resp.status_code}: {resp.text}")

# Determine which sprint is active
resp = requests.get(f"{BASE}/sprints/{SPRINT1_ID}", headers=header())
active_sprint_id = SPRINT1_ID if resp.json().get("status") == "active" else SPRINT2_ID

# Step 10: Create tasks and assign to Sprint
# TaskIn requires: project_id, title, type, description, spec, priority (no status, no sprint_id)
print("\n[10] Task-Sprint Assignment")
resp = requests.post(f"{BASE}/stories/{STORY_ID}/tasks", headers=header(), json={
    "project_id": PROJECT_ID,
    "title": f"Sprint Task 1 {ts}",
    "type": "task",
    "priority": "medium"
})
test("Create task 1", resp.status_code in (200, 201), f"Status {resp.status_code}: {resp.text}")
TASK1_ID = resp.json().get("id")
test("Task 1 default status is backlog", resp.json().get("status") == "backlog", f"Got: {resp.json().get('status')}")
test("Task 1 sprint_id is null", resp.json().get("sprint_id") is None, f"Got: {resp.json().get('sprint_id')}")

# Create second task
resp = requests.post(f"{BASE}/stories/{STORY_ID}/tasks", headers=header(), json={
    "project_id": PROJECT_ID,
    "title": f"Sprint Task 2 {ts}",
    "type": "task",
    "priority": "low"
})
test("Create task 2", resp.status_code in (200, 201), f"Status {resp.status_code}: {resp.text}")
TASK2_ID = resp.json().get("id")

# Assign task1 to sprint via PATCH
resp = requests.patch(f"{BASE}/tasks/{TASK1_ID}", headers=header(), json={"sprint_id": active_sprint_id})
test("Assign task 1 to sprint via PATCH", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")
test("Task 1 sprint_id set", resp.json().get("sprint_id") == active_sprint_id, f"Got: {resp.json().get('sprint_id')}")

# Assign task2 to sprint
resp = requests.patch(f"{BASE}/tasks/{TASK2_ID}", headers=header(), json={"sprint_id": active_sprint_id})
test("Assign task 2 to sprint via PATCH", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")
test("Task 2 sprint_id set", resp.json().get("sprint_id") == active_sprint_id, f"Got: {resp.json().get('sprint_id')}")

# Step 11: List tasks in Sprint
print("\n[11] List Tasks in Sprint")
resp = requests.get(f"{BASE}/sprints/{active_sprint_id}/tasks", headers=header())
test("List sprint tasks", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")
sprint_tasks = resp.json()
test("Sprint has 2 tasks", len(sprint_tasks) == 2, f"Got {len(sprint_tasks)} tasks: {[t.get('id') for t in sprint_tasks]}")

# Step 12: Complete Sprint - tasks should move back to backlog
print("\n[12] Complete Sprint - Task Backfill")
# Set tasks to in_progress using correct state path: backlog -> todo -> in_progress
resp = requests.put(f"{BASE}/tasks/{TASK1_ID}/status", headers=header(), json={"status": "todo"})
test("Set task 1 to todo", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")
resp = requests.put(f"{BASE}/tasks/{TASK1_ID}/status", headers=header(), json={"status": "in_progress"})
test("Set task 1 to in_progress", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")

resp = requests.put(f"{BASE}/tasks/{TASK2_ID}/status", headers=header(), json={"status": "todo"})
test("Set task 2 to todo", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")
resp = requests.put(f"{BASE}/tasks/{TASK2_ID}/status", headers=header(), json={"status": "in_progress"})
test("Set task 2 to in_progress", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")

# Complete the sprint
resp = requests.post(f"{BASE}/sprints/{active_sprint_id}/complete", headers=header())
test("Complete sprint", resp.status_code == 200, f"Status {resp.status_code}: {resp.text}")
test("Sprint status is completed", resp.json().get("status") == "completed", f"Got: {resp.json().get('status')}")

# Verify tasks moved back to backlog
resp = requests.get(f"{BASE}/tasks/{TASK1_ID}", headers=header())
t1_status = resp.json().get("status")
test("Task 1 moved to backlog", t1_status == "backlog", f"Got: {t1_status}")

resp = requests.get(f"{BASE}/tasks/{TASK2_ID}", headers=header())
t2_status = resp.json().get("status")
test("Task 2 moved to backlog", t2_status == "backlog", f"Got: {t2_status}")

# Verify tasks have sprint_id cleared
t2_sprint = resp.json().get("sprint_id")
test("Task sprint_id cleared after complete", t2_sprint is None, f"Got: {t2_sprint}")

# Step 13: Delete Sprint
print("\n[13] Sprint CRUD - Delete")
resp = requests.delete(f"{BASE}/sprints/{SPRINT1_ID}", headers=header())
test("Delete sprint 1", resp.status_code in (200, 204), f"Status {resp.status_code}: {resp.text}")

if SPRINT2_ID and SPRINT2_ID != SPRINT1_ID:
    resp = requests.delete(f"{BASE}/sprints/{SPRINT2_ID}", headers=header())
    test("Delete sprint 2", resp.status_code in (200, 204), f"Status {resp.status_code}: {resp.text}")

# Cleanup: delete project
print("\n[14] Cleanup")
resp = requests.delete(f"{BASE}/projects/{PROJECT_ID}", headers=header())
test("Delete test project", resp.status_code in (200, 204), f"Status {resp.status_code}: {resp.text}")

# Summary
print("\n" + "=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed, total {passed + failed}")
if errors:
    print("\nFAILED TESTS:")
    for e in errors:
        print(f"  - {e}")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)
