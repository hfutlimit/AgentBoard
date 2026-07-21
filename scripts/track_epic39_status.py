"""
Epic 39 (v2.6) 状态追踪脚本（REST 兜底，MCP 连接器断开时使用）
创建 Epic 39 -> Story -> Task，并沿合法状态链推进至 in_review：
  task:  backlog -> todo -> in_progress -> in_review
  story: 直接 PATCH status=in_review（不做 FR-5 校验）
  epic:  直接 PATCH status=in_review
目标端口：本地 uvicorn 58125（web 8080 同源，数据完整）。
"""
import json
import sys
import urllib.request

API = "http://127.0.0.1:58125"
USER, PASS = "admin", "admin123"


def req(method, path, token=None, body=None):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        try:
            detail = json.loads(detail).get("detail", detail)
        except Exception:
            pass
        return e.code, detail


def main():
    # 1) login
    st, payload = req("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    if st != 200 or "token" not in payload:
        print(f"FAIL login: {st} {payload}")
        sys.exit(1)
    token = payload["token"]
    print(f"OK login as {payload.get('username')} (is_admin={payload.get('is_admin')})")

    # 2) find project (AgentBoard self-project, expect id=3)
    st, projects = req("GET", "/api/projects", token=token)
    pid = None
    for p in projects if isinstance(projects, list) else []:
        if p.get("name") == "AgentBoard" or p.get("id") == 3:
            pid = p["id"]
            break
    if pid is None:
        pid = 3
    print(f"Using project_id={pid}")

    # 3) create epic
    st, epic = req("POST", f"/api/projects/{pid}/epics",
                   token=token, body={"title": "前端体验升级 v2.6 - 任务列表按状态排序",
                                      "description": "任务列表排序下拉新增「状态」选项（按工作流顺序），并持久化排序键/方向偏好到 localStorage。纯前端，不改后端契约。"})
    if st not in (200, 201):
        print(f"FAIL create epic: {st} {epic}")
        sys.exit(1)
    eid = epic["id"]
    print(f"OK created Epic id={eid} status={epic.get('status')}")

    # 4) create story
    st, story = req("POST", f"/api/epics/{eid}/stories",
                    token=token, body={"title": "v2.6 按状态排序 + 偏好持久化"})
    if st not in (200, 201):
        print(f"FAIL create story: {st} {story}")
        sys.exit(1)
    sid = story["id"]
    print(f"OK created Story id={sid} status={story.get('status')}")

    # 5) create task
    st, task = req("POST", f"/api/stories/{sid}/tasks",
                   token=token, body={"project_id": pid, "title": "任务列表按状态排序选项 + 排序偏好持久化",
                                      "type": "task", "priority": "high",
                                      "description": "app.ts: taskSortKey 增加 'status' 键 + statuses.indexOf 比较器；taskSortOptions 增加「状态」；setTaskSortKey/toggleTaskSortOrder 持久化 localStorage.agentboard_sort_key/order。app.html: 排序下拉选项增加「状态」、[selected] 绑定修复重载后选择不回显。"})
    if st not in (200, 201):
        print(f"FAIL create task: {st} {task}")
        sys.exit(1)
    tid = task["id"]
    print(f"OK created Task id={tid} status={task.get('status')}")

    # 6) drive task backlog -> todo -> in_progress -> in_review
    chain = ["todo", "in_progress", "in_review"]
    for ns in chain:
        st, resp = req("PUT", f"/api/tasks/{tid}/status", token=token, body={"status": ns})
        if st not in (200, 201):
            print(f"FAIL task -> {ns}: {st} {resp}")
            sys.exit(1)
        print(f"OK task {tid} -> {ns}")

    # 7) story + epic -> in_review
    st, resp = req("PATCH", f"/api/stories/{sid}", token=token, body={"status": "in_review"})
    print(f"story {sid} -> in_review: {st}")
    st, resp = req("PATCH", f"/api/epics/{eid}", token=token, body={"status": "in_review"})
    print(f"epic {eid} -> in_review: {st}")

    print(f"\nSUMMARY: Epic {eid} / Story {sid} / Task {tid} -> in_review")
    print(f"Verify: GET {API}/api/tasks/{tid}")


if __name__ == "__main__":
    main()
