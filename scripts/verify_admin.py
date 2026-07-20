import json
import urllib.request
import urllib.error

BASE = "http://localhost:18000"

def req(method, path, token=None, body=None, expect=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            code = resp.status
            text = resp.read().decode()
    except urllib.error.HTTPError as e:
        code = e.code
        text = e.read().decode()
    ok = (code == expect) if expect is not None else True
    return code, text, ok

# login as the promoted system admin
code, text, _ = req("POST", "/api/auth/login", body={"username": "admin", "password": "admin123"})
assert code == 200, f"admin login failed {code} {text}"
tokAdmin = json.loads(text)["token"]
print("admin is_admin:", json.loads(text).get("is_admin"))

# Find a private project to test against: create one as a fresh owner, then admin should see it
import secrets
suffix = secrets.token_hex(3)
tokOwner = req("POST", "/api/auth/register", body={"username": f"adm_owner_{suffix}", "password": "Password123!"})[2] if False else None
c, t, _ = req("POST", "/api/auth/register", body={"username": f"adm_owner_{suffix}", "password": "Password123!"})
tokOwner = json.loads(t)["token"]
c, t, _ = req("POST", "/api/projects", token=tokOwner, body={"name": f"AdminVis {suffix}", "key": f"AV{suffix[:4].upper()}", "is_private": True})
pid = json.loads(t)["id"]
print("created private project pid=", pid)

results = []
def check(name, cond, detail=""):
    results.append(cond)
    print(("PASS" if cond else "FAIL"), "-", name, detail)

# admin: GET /api/admin/projects should include pid
code, text, _ = req("GET", "/api/admin/projects", token=tokAdmin)
ids = [p["id"] for p in json.loads(text).get("items", [])]
check("admin lists all projects (incl. private)", pid in ids, f"count={len(ids)}")

# admin: GET private project detail -> 200
code, _, ok = req("GET", f"/api/projects/{pid}", token=tokAdmin, expect=200)
check("admin can view private project detail", ok, f"code={code}")

# admin: GET epics -> 200
code, _, ok = req("GET", f"/api/projects/{pid}/epics", token=tokAdmin, expect=200)
check("admin can view private project epics", ok, f"code={code}")

# admin: GET stats -> 200
code, _, ok = req("GET", f"/api/projects/{pid}/stats", token=tokAdmin, expect=200)
check("admin can view private project stats", ok, f"code={code}")

# admin: GET task search by project -> 200
code, _, ok = req("GET", f"/api/tasks?project_id={pid}", token=tokAdmin, expect=200)
check("admin can search private project tasks", ok, f"code={code}")

# control: a NON-admin, NON-member cannot see it (regression check)
c, t, _ = req("POST", "/api/auth/register", body={"username": f"adm_stranger_{suffix}", "password": "Password123!"})
tokStranger = json.loads(t)["token"]
code, _, ok = req("GET", f"/api/projects/{pid}", token=tokStranger, expect=403)
check("stranger still blocked from admin's private project", ok, f"code={code}")

passed = sum(1 for r in results if r)
print(f"\n=== ADMIN VISIBILITY SUMMARY ===\n{passed}/{len(results)} checks passed")
if passed != len(results):
    raise SystemExit(1)
print("ADMIN_OK")
