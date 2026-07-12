"""
Code Review Test Script for Task #84 & #85
- Task #84: Sprint/Backlog Web View & MCP Tools
- Task #85: Attachment metadata model, local safe storage, size/MIME limits
"""
import httpx
import json
import time
import sys
import re

API = "http://localhost:8000"
WEB = "http://localhost:8080"
MCP = "http://localhost:8001"

passed = 0
failed = 0
errors = []

def ok(name):
    global passed
    passed += 1
    print(f"  PASS: {name}")

def fail(name, detail=""):
    global failed
    failed += 1
    errors.append(f"{name}: {detail}")
    print(f"  FAIL: {name} - {detail}")

def sep(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

client = httpx.Client(base_url=API, timeout=30)

ts = int(time.time())

# ============================================================
# Setup: Create project -> epic -> story
# ============================================================
sep("Setup: Create Project/Epic/Story")

try:
    r = client.post("/api/projects", json={"name": f"Review-{ts}", "description": "Code review test"})
    if r.status_code == 201:
        proj_id = r.json()["id"]
        ok(f"Create project (id={proj_id})")
    else:
        fail("Create project", f"status={r.status_code} body={r.text}")
        sys.exit(1)
except Exception as e:
    fail("Create project", str(e))
    sys.exit(1)

try:
    r = client.post(f"/api/projects/{proj_id}/epics", json={"title": f"Epic-{ts}"})
    if r.status_code == 201:
        epic_id = r.json()["id"]
        ok(f"Create epic (id={epic_id})")
    else:
        fail("Create epic", f"status={r.status_code} body={r.text}")
        sys.exit(1)
except Exception as e:
    fail("Create epic", str(e))
    sys.exit(1)

try:
    r = client.post(f"/api/epics/{epic_id}/stories", json={"title": f"Story-{ts}"})
    if r.status_code == 201:
        story_id = r.json()["id"]
        ok(f"Create story (id={story_id})")
    else:
        fail("Create story", f"status={r.status_code} body={r.text}")
        sys.exit(1)
except Exception as e:
    fail("Create story", str(e))
    sys.exit(1)

# ============================================================
# Task #84: Sprint API Tests
# ============================================================
sep("Task #84: Sprint API Tests")

# 1. Create sprint
try:
    r = client.post(f"/api/projects/{proj_id}/sprints", json={
        "title": f"Sprint-{ts}",
        "goal": "Code review sprint",
        "start_date": "2026-07-12",
        "end_date": "2026-07-26"
    })
    if r.status_code == 201:
        sprint = r.json()
        sprint_id = sprint["id"]
        ok(f"Create sprint (id={sprint_id}, status={sprint.get('status')})")
    else:
        fail("Create sprint", f"status={r.status_code} body={r.text}")
        sprint_id = None
except Exception as e:
    fail("Create sprint", str(e))
    sprint_id = None

# 2. Get sprint
if sprint_id:
    try:
        r = client.get(f"/api/sprints/{sprint_id}")
        if r.status_code == 200 and r.json()["id"] == sprint_id:
            ok("Get sprint by id")
        else:
            fail("Get sprint", f"status={r.status_code}")
    except Exception as e:
        fail("Get sprint", str(e))

# 3. List sprints
try:
    r = client.get(f"/api/projects/{proj_id}/sprints")
    if r.status_code == 200 and len(r.json()) >= 1:
        ok(f"List sprints (count={len(r.json())})")
    else:
        fail("List sprints", f"status={r.status_code} count={len(r.json()) if r.status_code==200 else 'N/A'}")
except Exception as e:
    fail("List sprints", str(e))

# 4. Activate sprint
if sprint_id:
    try:
        r = client.post(f"/api/sprints/{sprint_id}/activate")
        if r.status_code == 200 and r.json().get("status") == "active":
            ok("Activate sprint")
        else:
            fail("Activate sprint", f"status={r.status_code} body={r.text}")
    except Exception as e:
        fail("Activate sprint", str(e))

# 5. Create task in sprint (via story, then assign sprint_id via PATCH)
try:
    r = client.post(f"/api/stories/{story_id}/tasks", json={
        "project_id": proj_id,
        "title": f"SprintTask-{ts}",
        "type": "task"
    })
    if r.status_code == 201:
        sprint_task_id = r.json()["id"]
        # Assign to sprint
        r2 = client.patch(f"/api/tasks/{sprint_task_id}", json={"sprint_id": sprint_id})
        if r2.status_code == 200 and r2.json().get("sprint_id") == sprint_id:
            ok(f"Create task in sprint (id={sprint_task_id})")
        else:
            fail("Assign task to sprint", f"PATCH status={r2.status_code} body={r2.text}")
    else:
        fail("Create task in sprint", f"status={r.status_code} body={r.text}")
        sprint_task_id = None
except Exception as e:
    fail("Create task in sprint", str(e))
    sprint_task_id = None

# 6. Create backlog task (no sprint)
try:
    r = client.post(f"/api/stories/{story_id}/tasks", json={
        "project_id": proj_id,
        "title": f"BacklogTask-{ts}",
        "type": "task"
    })
    if r.status_code == 201:
        backlog_task_id = r.json()["id"]
        ok(f"Create backlog task (id={backlog_task_id}, sprint_id=null)")
    else:
        fail("Create backlog task", f"status={r.status_code} body={r.text}")
        backlog_task_id = None
except Exception as e:
    fail("Create backlog task", str(e))
    backlog_task_id = None

# 7. List sprint tasks
if sprint_id:
    try:
        r = client.get(f"/api/sprints/{sprint_id}/tasks")
        if r.status_code == 200:
            tasks = r.json()
            if any(t["id"] == sprint_task_id for t in tasks):
                ok(f"List sprint tasks (count={len(tasks)})")
            else:
                fail("List sprint tasks", "sprint task not in results")
        else:
            fail("List sprint tasks", f"status={r.status_code}")
    except Exception as e:
        fail("List sprint tasks", str(e))

# 8. Update sprint
if sprint_id:
    try:
        r = client.patch(f"/api/sprints/{sprint_id}", json={"goal": "Updated goal for review"})
        if r.status_code == 200 and r.json().get("goal") == "Updated goal for review":
            ok("Update sprint (PATCH)")
        else:
            fail("Update sprint", f"status={r.status_code} body={r.text}")
    except Exception as e:
        fail("Update sprint", str(e))

# 9. Complete sprint (should backfill tasks to backlog)
if sprint_id:
    try:
        r = client.post(f"/api/sprints/{sprint_id}/complete")
        if r.status_code == 200 and r.json().get("status") == "completed":
            ok("Complete sprint")
        else:
            fail("Complete sprint", f"status={r.status_code} body={r.text}")
    except Exception as e:
        fail("Complete sprint", str(e))

# 10. Verify task backfill after sprint completion
if sprint_task_id:
    try:
        r = client.get(f"/api/tasks/{sprint_task_id}")
        if r.status_code == 200 and r.json().get("sprint_id") is None:
            ok("Task backfilled to backlog after sprint completion")
        else:
            fail("Task backfill", f"sprint_id={r.json().get('sprint_id') if r.status_code==200 else 'N/A'}")
    except Exception as e:
        fail("Task backfill", str(e))

# 11. Delete sprint
if sprint_id:
    try:
        r = client.delete(f"/api/sprints/{sprint_id}")
        if r.status_code == 200 and r.json().get("ok"):
            ok("Delete sprint")
        else:
            fail("Delete sprint", f"status={r.status_code} body={r.text}")
    except Exception as e:
        fail("Delete sprint", str(e))

# 12. Single active sprint constraint
try:
    s1 = client.post(f"/api/projects/{proj_id}/sprints", json={"title": "S1", "start_date": "2026-07-12", "end_date": "2026-07-26"})
    s2 = client.post(f"/api/projects/{proj_id}/sprints", json={"title": "S2", "start_date": "2026-07-12", "end_date": "2026-07-26"})
    if s1.status_code == 201 and s2.status_code == 201:
        s1_id = s1.json()["id"]
        s2_id = s2.json()["id"]
        client.post(f"/api/sprints/{s1_id}/activate")
        r2 = client.post(f"/api/sprints/{s2_id}/activate")
        if r2.status_code == 400:
            ok("Single active sprint constraint enforced")
        else:
            fail("Single active sprint constraint", f"expected 400, got {r2.status_code}")
        client.delete(f"/api/sprints/{s1_id}")
        client.delete(f"/api/sprints/{s2_id}")
    else:
        fail("Single active sprint setup", f"s1={s1.status_code} s2={s2.status_code}")
except Exception as e:
    fail("Single active sprint constraint", str(e))

# ============================================================
# Task #84: Backlog Web View Tests
# ============================================================
sep("Task #84: Backlog Web View Tests")

try:
    r = httpx.get(f"{WEB}/", timeout=10)
    if r.status_code == 200:
        ok("Web SPA loads (index.html)")
    else:
        fail("Web SPA loads", f"status={r.status_code}")
except Exception as e:
    fail("Web SPA loads", str(e))

try:
    r = httpx.get(f"{WEB}/", timeout=10)
    js_match = re.search(r'(main-[A-Za-z0-9]+\.js)', r.text)
    if js_match:
        js_url = f"{WEB}/{js_match.group(1)}"
        r2 = httpx.get(js_url, timeout=10)
        if r2.status_code == 200 and "backlog" in r2.text.lower():
            ok("Backlog section found in JS bundle")
        else:
            fail("Backlog section in JS", f"status={r2.status_code if r2 else 'N/A'}")
    else:
        fail("Find JS bundle", "no main-*.js in index.html")
except Exception as e:
    fail("Backlog JS check", str(e))

# ============================================================
# Task #84: MCP Sprint Tools Tests
# ============================================================
sep("Task #84: MCP Sprint Tools Tests")

try:
    # Use Streamable HTTP transport with proper headers
    headers = {"Accept": "application/json, text/event-stream"}
    init_r = httpx.post(f"{MCP}/mcp", json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "review-test", "version": "1.0"}
        }
    }, headers=headers, timeout=10)

    session_id = init_r.headers.get("mcp-session-id", "")

    if init_r.status_code == 200:
        ok("MCP initialize")
    else:
        fail("MCP initialize", f"status={init_r.status_code} body={init_r.text[:200]}")

    # Send initialized notification
    httpx.post(f"{MCP}/mcp", json={
        "jsonrpc": "2.0",
        "method": "notifications/initialized"
    }, headers={**headers, "Mcp-Session-Id": session_id}, timeout=10)

    # List tools
    tools_r = httpx.post(f"{MCP}/mcp", json={
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }, headers={**headers, "Mcp-Session-Id": session_id}, timeout=10)

    if tools_r.status_code == 200:
        # Parse SSE response or JSON response
        body = tools_r.text
        # Try to extract JSON from SSE format
        if body.startswith("event:"):
            for line in body.split("\n"):
                if line.startswith("data: "):
                    body = line[6:]
                    break
        tools_data = json.loads(body)
        tool_names = [t["name"] for t in tools_data.get("result", {}).get("tools", [])]
        sprint_tools = [t for t in tool_names if "sprint" in t.lower()]
        if len(sprint_tools) >= 8:
            ok(f"MCP has {len(sprint_tools)} sprint tools: {sprint_tools}")
        else:
            fail("MCP sprint tools count", f"found {len(sprint_tools)}: {sprint_tools}")
    else:
        fail("MCP tools/list", f"status={tools_r.status_code} body={tools_r.text[:200]}")
except Exception as e:
    fail("MCP tools/list", str(e))

# ============================================================
# Task #85: Attachment Tests
# ============================================================
sep("Task #85: Attachment Model, Safe Storage, Size/MIME Limits")

# Create a task for attachment testing
try:
    r = client.post(f"/api/stories/{story_id}/tasks", json={
        "project_id": proj_id,
        "title": f"AttachTask-{ts}",
        "type": "task"
    })
    if r.status_code == 201:
        attach_task_id = r.json()["id"]
        ok(f"Create task for attachment test (id={attach_task_id})")
    else:
        fail("Create task for attachment", f"status={r.status_code} body={r.text}")
        attach_task_id = None
except Exception as e:
    fail("Create task for attachment", str(e))
    attach_task_id = None

# 1. Upload valid attachment (text/plain)
if attach_task_id:
    try:
        content = b"Hello Attachment Review Test"
        r = client.post(
            f"/api/tasks/{attach_task_id}/attachments",
            files={"file": ("test.txt", content, "text/plain")}
        )
        if r.status_code == 201:
            att = r.json()
            att_id = att["id"]
            checks = []
            checks.append(("id exists", att.get("id") is not None))
            checks.append(("task_id", att.get("task_id") == attach_task_id))
            checks.append(("original_name", att.get("original_name") == "test.txt"))
            checks.append(("size", att.get("size") == len(content)))
            checks.append(("mime_type", att.get("mime_type") == "text/plain"))
            checks.append(("filename is UUID", att.get("filename") != "test.txt" and len(att.get("filename", "")) == 32))
            if all(c[1] for c in checks):
                ok(f"Upload valid attachment (id={att_id}, filename={att['filename'][:8]}...)")
            else:
                fail("Upload attachment metadata", "; ".join(f"{n}={v}" for n, v in checks if not v))
        else:
            fail("Upload valid attachment", f"status={r.status_code} body={r.text}")
            att_id = None
    except Exception as e:
        fail("Upload valid attachment", str(e))
        att_id = None
else:
    att_id = None

# 2. List attachments
if attach_task_id and att_id:
    try:
        r = client.get(f"/api/tasks/{attach_task_id}/attachments")
        if r.status_code == 200 and len(r.json()) >= 1:
            ok(f"List attachments (count={len(r.json())})")
        else:
            fail("List attachments", f"status={r.status_code} count={len(r.json()) if r.status_code==200 else 'N/A'}")
    except Exception as e:
        fail("List attachments", str(e))

# 3. Get attachment info
if att_id:
    try:
        r = client.get(f"/api/attachments/{att_id}/info")
        if r.status_code == 200 and r.json()["id"] == att_id:
            ok("Get attachment info")
        else:
            fail("Get attachment info", f"status={r.status_code}")
    except Exception as e:
        fail("Get attachment info", str(e))

# 4. Download attachment (content matches)
if att_id:
    try:
        r = client.get(f"/api/attachments/{att_id}")
        if r.status_code == 200 and r.content == b"Hello Attachment Review Test":
            ok("Download attachment (content matches)")
        else:
            fail("Download attachment", f"status={r.status_code} content_len={len(r.content) if r.status_code==200 else 'N/A'}")
    except Exception as e:
        fail("Download attachment", str(e))

# 5. Upload PNG image
if attach_task_id:
    try:
        png_content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        r = client.post(
            f"/api/tasks/{attach_task_id}/attachments",
            files={"file": ("test.png", png_content, "image/png")}
        )
        if r.status_code == 201:
            ok("Upload PNG attachment (MIME whitelist)")
            png_att_id = r.json()["id"]
        else:
            fail("Upload PNG attachment", f"status={r.status_code} body={r.text}")
            png_att_id = None
    except Exception as e:
        fail("Upload PNG attachment", str(e))
        png_att_id = None
else:
    png_att_id = None

# 6. Upload PDF
if attach_task_id:
    try:
        pdf_content = b"%PDF-1.4\n%test pdf content"
        r = client.post(
            f"/api/tasks/{attach_task_id}/attachments",
            files={"file": ("doc.pdf", pdf_content, "application/pdf")}
        )
        if r.status_code == 201:
            ok("Upload PDF attachment (MIME whitelist)")
        else:
            fail("Upload PDF attachment", f"status={r.status_code} body={r.text}")
    except Exception as e:
        fail("Upload PDF attachment", str(e))

# 7. Upload invalid MIME type (should fail with 422)
if attach_task_id:
    try:
        r = client.post(
            f"/api/tasks/{attach_task_id}/attachments",
            files={"file": ("malware.exe", b"MZ\x90\x00", "application/x-msdownload")}
        )
        if r.status_code == 422:
            ok("Reject invalid MIME type (exe -> 422)")
        else:
            fail("Reject invalid MIME type", f"expected 422, got {r.status_code}")
    except Exception as e:
        fail("Reject invalid MIME type", str(e))

# 8. Upload oversized file (should fail with 422)
if attach_task_id:
    try:
        big_content = b"x" * (10 * 1024 * 1024 + 1)
        r = client.post(
            f"/api/tasks/{attach_task_id}/attachments",
            files={"file": ("big.txt", big_content, "text/plain")}
        )
        if r.status_code == 422:
            ok("Reject oversized file (>10MB -> 422)")
        else:
            fail("Reject oversized file", f"expected 422, got {r.status_code}")
    except Exception as e:
        fail("Reject oversized file", str(e))

# 9. Upload to non-existent task (should 404)
try:
    r = client.post(
        "/api/tasks/999999/attachments",
        files={"file": ("test.txt", b"test", "text/plain")}
    )
    if r.status_code == 404:
        ok("Upload to non-existent task (404)")
    else:
        fail("Upload to non-existent task", f"expected 404, got {r.status_code}")
except Exception as e:
    fail("Upload to non-existent task", str(e))

# 10. Get non-existent attachment (should 404)
try:
    r = client.get("/api/attachments/999999/info")
    if r.status_code == 404:
        ok("Get non-existent attachment info (404)")
    else:
        fail("Get non-existent attachment info", f"expected 404, got {r.status_code}")
except Exception as e:
    fail("Get non-existent attachment info", str(e))

# 11. Delete attachment
if att_id:
    try:
        r = client.delete(f"/api/attachments/{att_id}")
        if r.status_code == 200 and r.json().get("ok"):
            ok("Delete attachment")
        else:
            fail("Delete attachment", f"status={r.status_code}")
    except Exception as e:
        fail("Delete attachment", str(e))

# 12. Verify deleted attachment is gone
if att_id:
    try:
        r = client.get(f"/api/attachments/{att_id}/info")
        if r.status_code == 404:
            ok("Deleted attachment returns 404")
        else:
            fail("Deleted attachment check", f"expected 404, got {r.status_code}")
    except Exception as e:
        fail("Deleted attachment check", str(e))

# 13. Delete non-existent attachment (should 404)
try:
    r = client.delete("/api/attachments/999999")
    if r.status_code == 404:
        ok("Delete non-existent attachment (404)")
    else:
        fail("Delete non-existent attachment", f"expected 404, got {r.status_code}")
except Exception as e:
    fail("Delete non-existent attachment", str(e))

# 14. Verify UUID filename (security: no path traversal)
if png_att_id:
    try:
        r = client.get(f"/api/attachments/{png_att_id}/info")
        if r.status_code == 200:
            filename = r.json().get("filename", "")
            if len(filename) == 32 and "/" not in filename and ".." not in filename:
                ok(f"UUID filename security (filename={filename[:8]}...)")
            else:
                fail("UUID filename security", f"filename={filename}")
        else:
            fail("UUID filename check", f"status={r.status_code}")
    except Exception as e:
        fail("UUID filename check", str(e))

# 15. List attachments on non-existent task (should 404)
try:
    r = client.get("/api/tasks/999999/attachments")
    if r.status_code == 404:
        ok("List attachments on non-existent task (404)")
    else:
        fail("List attachments on non-existent task", f"expected 404, got {r.status_code}")
except Exception as e:
    fail("List attachments on non-existent task", str(e))

# --- Cleanup ---
sep("Cleanup")
if attach_task_id:
    client.delete(f"/api/tasks/{attach_task_id}")
if backlog_task_id:
    client.delete(f"/api/tasks/{backlog_task_id}")
if sprint_task_id:
    client.delete(f"/api/tasks/{sprint_task_id}")
if story_id:
    client.delete(f"/api/stories/{story_id}")
if epic_id:
    client.delete(f"/api/epics/{epic_id}")
if proj_id:
    client.delete(f"/api/projects/{proj_id}")
print("  Cleanup done")

# --- Summary ---
sep("SUMMARY")
print(f"\n  Total: {passed + failed}")
print(f"  Passed: {passed}")
print(f"  Failed: {failed}")
if errors:
    print(f"\n  Failures:")
    for e in errors:
        print(f"    - {e}")
print()

sys.exit(0 if failed == 0 else 1)
