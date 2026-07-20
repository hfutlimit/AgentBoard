import json, urllib.request, urllib.error

BASE = "http://127.0.0.1:58125"
PID = 3

def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

# 1) Epic
desc_epic = ("任务列表工具条新增第三组类型快速筛选 chips（全部/任务/Bug 单选带计数、localStorage 持久化），"
            "复用既有 filterTypes 信号与 visibleTasks 过滤逻辑，纯前端、无后端契约变更。")
st, epic = req("POST", f"/api/projects/{PID}/epics",
              {"title": "前端体验升级 v2.4 - 任务类型快速筛选 chips", "description": desc_epic})
print("EPIC", st, epic.get("id"))
eid = epic["id"]

# 2) Story
desc_story = "实现类型快速筛选 chips UI 与信号/方法（setQuickType / typeCounts / persistQuickType）。"
st, story = req("POST", f"/api/epics/{eid}/stories",
                {"title": "任务类型快速筛选 chips", "description": desc_story})
print("STORY", st, story.get("id"))
sid = story["id"]

# 3) Task (high)
desc_task = ("app.ts：filterTypes 初始化读 localStorage.agentboard_quick_type；新增 typeCounts computed；"
            "新增 setQuickType() 单选切换 + persistQuickType() 持久化；clearFilters 联动重置。"
            "app.html：状态 chips 后追加第三个 task-quickfilter-bar（全部+任务+Bug 带计数），复用 .qf-chip/.qf-count。")
st, task = req("POST", f"/api/stories/{sid}/tasks",
              {"project_id": PID, "title": "任务类型快速筛选 chips（任务/Bug 单选带计数，持久化）",
               "type": "task", "priority": "high", "description": desc_task})
print("TASK", st, task.get("id"))
tid = task["id"]

# 4) Drive task status: backlog -> todo -> in_progress -> in_review
for status in ["todo", "in_progress", "in_review"]:
    st, _ = req("PUT", f"/api/tasks/{tid}/status", {"status": status})
    print(f"TASK {tid} -> {status}: HTTP {st}")

# 5) Sync Story + Epic to in_review
st, _ = req("PATCH", f"/api/stories/{sid}", {"status": "in_review"})
print(f"STORY {sid} -> in_review: HTTP {st}")
st, _ = req("PATCH", f"/api/epics/{eid}", {"status": "in_review"})
print(f"EPIC {eid} -> in_review: HTTP {st}")

print(f"\nRESULT eid={eid} sid={sid} tid={tid}")
open("/tmp/v24_ids.txt", "w").write(f"{eid},{sid},{tid}")
