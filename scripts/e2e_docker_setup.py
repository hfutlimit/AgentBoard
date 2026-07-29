"""Docker 环境 E2E 准备脚本：创建测试用户与 API Key，输出 JSON 到 stdout。"""
import json
import urllib.request

API = "http://127.0.0.1:18000/api"


def req(method: str, path: str, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


out = {}

# admin 登录
st, admin = req("POST", "/auth/login", {"username": "admin", "password": "admin123"})
assert st == 200, f"admin login failed: {st} {admin}"
out["admin_token"] = admin["token"]
out["admin_id"] = admin["id"]

# 普通用户（幂等）
st, reg = req("POST", "/auth/register", {"username": "mcp_e2e_user", "password": "E2ePass123"})
st, usr = req("POST", "/auth/login", {"username": "mcp_e2e_user", "password": "E2ePass123"})
assert st == 200, f"user login failed: {st} {usr}"
out["user_token"] = usr["token"]
out["user_id"] = usr["id"]
out["user_is_admin"] = usr.get("is_admin")

# 各自建 API Key（明文仅创建时返回）
st, ak = req("POST", "/api-keys", {"name": "e2e-admin-key"}, token=admin["token"])
assert st in (200, 201), f"admin key failed: {st} {ak}"
out["admin_key"] = ak.get("key") or ak.get("plaintext") or ak
st, uk = req("POST", "/api-keys", {"name": "e2e-user-key"}, token=usr["token"])
assert st in (200, 201), f"user key failed: {st} {uk}"
out["user_key"] = uk.get("key") or uk.get("plaintext") or uk

print(json.dumps(out, ensure_ascii=False, indent=2))
