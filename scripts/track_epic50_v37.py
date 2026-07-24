"""Track v3.7 epic (group-by due date) via REST. Shares DB with authoritative API on :18000."""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:18000"
USER, PW = "admin", "admin123"

def req(method, path, token=None, data=None):
    url = BASE + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

# 1. auth
st, auth = req("POST", "/api/auth/login", data={"username": USER, "password": PW})
if st != 200:
    raise SystemExit(f"login failed {st}: {auth}")
token = auth["token"]
print("token ok, user id =", auth.get("id"))

# 2. create project
st, proj = req("POST", "/api/projects", token, {"name": "AUTODEV50", "key": "AD50",
      "description": "auto-dev v3.7 tracking"})
print("project", st, proj.get("id"), proj.get("name"))

# 3. create epic
st, epic = req("POST", f"/api/projects/{proj['id']}/epics", token,
      {"title": "Epic 50 v3.7 任务列表分组新增按截止日期", "description": "纯前端，零后端契约变更"})
print("epic", st, epic.get("id"), epic.get("title"))

# 4. create story
st, story = req("POST", f"/api/epics/{epic['id']}/stories", token,
      {"title": "Story 188 分组按截止日期", "description": "overdue/today/week/later/none 五桶"})
print("story", st, story.get("id"))

# 5. create task
st, task = req("POST", f"/api/stories/{story['id']}/tasks", token,
      {"project_id": proj["id"], "title": "Task 970(high) 分组新增按截止日期", "type": "task", "priority": "high"})
print("task", st, task.get("id"), "status=", task.get("status"))

tid = task["id"]
# 6. walk status machine backlog -> todo -> in_progress -> in_review
for s in ("todo", "in_progress", "in_review"):
    st, r = req("PUT", f"/api/tasks/{tid}/status", token, {"status": s})
    print("task set_status", s, st, r.get("status") if isinstance(r, dict) else r)

# 7. sync story + epic to in_review (no FSM validation on story/epic patch)
st, r = req("PATCH", f"/api/stories/{story['id']}", token, {"status": "in_review"})
print("story ->", st, r.get("status") if isinstance(r, dict) else r)
st, r = req("PATCH", f"/api/epics/{epic['id']}", token, {"status": "in_review"})
print("epic ->", st, r.get("status") if isinstance(r, dict) else r)

print(f"\nDONE: project={proj['id']} epic={epic['id']} story={story['id']} task={tid}")
