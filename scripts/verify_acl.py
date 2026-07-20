import json
import secrets
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
    ok = True
    if expect is not None:
        ok = code == expect
    return code, text, ok

def register(username, password="Password123!"):
    code, text, _ = req("POST", "/api/auth/register", body={"username": username, "password": password})
    if code != 201:
        raise RuntimeError(f"register {username} failed: {code} {text}")
    return json.loads(text)["token"]

def login(username, password="Password123!"):
    code, text, _ = req("POST", "/api/auth/login", body={"username": username, "password": password})
    if code != 200:
        raise RuntimeError(f"login {username} failed: {code} {text}")
    return json.loads(text)["token"]

results = []
def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(("PASS" if cond else "FAIL"), "-", name, detail)

suffix = secrets.token_hex(3)
userA = f"owner_{suffix}"
userB = f"member_{suffix}"

# 1) Register owner A
tokA = register(userA)
check("register owner A", True)

# 2) Create PRIVATE project P as A
code, text, _ = req("POST", "/api/projects", token=tokA, body={"name": f"Private Project {suffix}", "key": f"PV{suffix[:4].upper()}", "is_private": True})
check("create private project", code == 201, f"code={code}")
pid = json.loads(text)["id"]

# 3) A creates an epic inside P (so we can test sub-resource reads)
code, text, _ = req("POST", f"/api/projects/{pid}/epics", token=tokA, body={"title": "Epic 1", "description": ""})
check("owner creates epic", code == 201, f"code={code}")
eid = json.loads(text)["id"]

# 4) Register non-member B
tokB = register(userB)
check("register member B", True)

# 5) B lists projects -> P must NOT appear (private, B not a member)
code, text, _ = req("GET", "/api/projects", token=tokB)
items = json.loads(text).get("items", [])
ids = [p["id"] for p in items]
check("non-member cannot see private project in list", pid not in ids, f"list_ids={ids}")

# 6) B GET /api/projects/{pid} -> 403
code, _, ok = req("GET", f"/api/projects/{pid}", token=tokB, expect=403)
check("non-member blocked from project detail", ok, f"code={code}")

# 7) B GET epics -> 403
code, _, ok = req("GET", f"/api/projects/{pid}/epics", token=tokB, expect=403)
check("non-member blocked from epics", ok, f"code={code}")

# 8) B GET stats -> 403
code, _, ok = req("GET", f"/api/projects/{pid}/stats", token=tokB, expect=403)
check("non-member blocked from stats", ok, f"code={code}")

# 9) B POST epic -> 403 (write leak closed)
code, _, ok = req("POST", f"/api/projects/{pid}/epics", token=tokB, body={"title": "x"}, expect=403)
check("non-member blocked from creating epic", ok, f"code={code}")

# 10) B searches tasks by project_id -> 403
code, _, ok = req("GET", f"/api/tasks?project_id={pid}", token=tokB, expect=403)
check("non-member blocked from task search", ok, f"code={code}")

# 11) Anonymous (no token) to sub-resource -> 401
code, _, ok = req("GET", f"/api/projects/{pid}/epics", expect=401)
check("anonymous blocked (401)", ok, f"code={code}")

# 12) Owner A adds B by username
code, text, _ = req("POST", f"/api/projects/{pid}/members", token=tokA, body={"username": userB, "role": "member"})
check("owner adds member by username", code == 201, f"code={code}")

# 13) Now B can see project detail
code, _, ok = req("GET", f"/api/projects/{pid}", token=tokB, expect=200)
check("member can see project detail after add", ok, f"code={code}")

# 14) B can list epics
code, _, ok = req("GET", f"/api/projects/{pid}/epics", token=tokB, expect=200)
check("member can list epics after add", ok, f"code={code}")

# 15) B now sees project in list
code, text, _ = req("GET", "/api/projects", token=tokB)
ids = [p["id"] for p in json.loads(text).get("items", [])]
check("member sees project in list after add", pid in ids, f"list_ids={ids}")

# 16) B can create an epic (member write allowed)
code, _, ok = req("POST", f"/api/projects/{pid}/epics", token=tokB, body={"title": "Epic by member"}, expect=201)
check("member can create epic", ok, f"code={code}")

print("\n=== OWNER/MEMBER FLOW SUMMARY ===")
passed = sum(1 for _, c, _ in results if c)
print(f"{passed}/{len(results)} checks passed")
if passed != len(results):
    print("SOME CHECKS FAILED")
    raise SystemExit(1)
print("FLOW_OK")
