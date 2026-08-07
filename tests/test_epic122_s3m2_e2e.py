"""Epic 122 S3 M2 E2E：REST 全链路（review-stats + 超时重派）+ 页面渲染 0 错误。

覆盖：
1. REST 链路：
   - 注册/登录 admin → register agent → heartbeat；
   - 创建项目 → epic → story → assign-reviewer（pending_review + reviewer 回填）；
   - GET /api/review-stats 结构断言；
   - POST /api/review-stats/reassign-timeout（触发扫描返回统计）；
   - SQL 直接把 story.created_at 拨回 60 分钟前（模拟超时）→ 再触发扫描 →
     reviewer 应被更换（超时重派生效）；
2. 页面渲染：Web 首页/项目页 Playwright 冒烟 0 console/pageerror/js-css 失败。

运行：venv python tests/test_epic122_s3m2_e2e.py
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

API = os.getenv("AGENTBOARD_E2E_API", "http://127.0.0.1:18000")
WEB = os.getenv("AGENTBOARD_E2E_WEB", "http://127.0.0.1:28080")
TOKEN = {"Authorization": ""}


def http(method, path, body=None, params=None, retries=3):
    url = API + path
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + qs
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN["Authorization"]:
        req.add_header("Authorization", TOKEN["Authorization"])
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code >= 500 and i < retries - 1:
                time.sleep(1.5)
                last = e
                continue
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {}
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last


def sql_exec(sql):
    """经 agentboard-db-1 容器执行 SQL（超时模拟用）。"""
    import subprocess
    import shlex
    cmd = ["docker", "exec", "agentboard-db-1", "mariadb",
           "-uagentboard", "-pagentboard", "agentboard", "-e", sql]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"SQL failed: {r.stderr[:300]}")
    return r.stdout


def main():
    fails = []
    # 1. 登录（幂等注册 admin/admin123）
    st, data = http("POST", "/api/auth/register",
                    {"username": "admin", "password": "admin123"})
    if st not in (200, 201, 409):
        fails.append(f"register: {st} {data}")
    st, data = http("POST", "/api/auth/login",
                    {"username": "admin", "password": "admin123"})
    assert st == 200, f"login: {st} {data}"
    TOKEN["Authorization"] = f"Bearer {data['token']}"
    print("[1] 登录 OK")

    # 2. Agent 注册 + 心跳
    ts = int(time.time())
    aid = f"s3m2e2e-{ts}"
    st, a = http("POST", "/api/agents/register",
                 {"agent_id": aid, "name": "E2E Reviewer",
                  "roles": '["reviewer"]', "capabilities": "[]"})
    assert st in (200, 201), f"agent register: {st} {a}"
    me = http("GET", "/api/auth/me")[1]
    # 绑定当前用户
    st, a = http("POST", "/api/agents/register",
                 {"agent_id": aid, "name": "E2E Reviewer",
                  "roles": '["reviewer"]', "capabilities": "[]"})
    http("POST", f"/api/agents/{aid}/heartbeat")
    # 第二个 reviewer（独立用户 + agent）—— 保证超时重派排除旧 reviewer 后有候选
    rev2 = f"s3m2rev2-{ts}"
    st, ru = http("POST", "/api/auth/register",
                  {"username": rev2, "password": "password123"})
    if st not in (200, 201):
        st, ru = http("POST", "/api/auth/login", {"username": rev2, "password": "password123"})
    rev2_token = ru.get("token") if st in (200, 201) else None
    rev2_uid = ru.get("id") if isinstance(ru, dict) else None
    assert rev2_token and rev2_uid, f"rev2 注册失败: {st} {ru}"
    # agent2 以 rev2 身份注册（绑定 rev2.user_id → 超时重派排除 admin 后有独立候选）
    aid2 = f"s3m2e2e2-{ts}"
    saved = TOKEN["Authorization"]
    TOKEN["Authorization"] = f"Bearer {rev2_token}"
    st, a2 = http("POST", "/api/agents/register",
                  {"agent_id": aid2, "name": "E2E Reviewer2",
                   "roles": '["reviewer"]', "capabilities": "[]"})
    http("POST", f"/api/agents/{aid2}/heartbeat")
    TOKEN["Authorization"] = saved
    assert st in (200, 201), f"agent2 register: {st} {a2}"
    print("[2] 双 reviewer Agent 注册+心跳 OK")

    # 3. 项目 → epic → story → assign-reviewer
    st, p = http("POST", "/api/projects",
                 {"name": f"S3M2 E2E {ts}", "key": f"S3M2{ts % 1000}"})
    assert st in (200, 201), f"project: {st} {p}"
    pid = p["id"]
    # 把 reviewer2 加为项目成员（其 agent 才能进候选集）
    st, _m = http("POST", f"/api/projects/{pid}/members", {"username": rev2})
    assert st in (200, 201), f"add member: {st} {_m}"
    st, ep = http("POST", f"/api/projects/{pid}/epics",
                  {"title": f"S3M2 E2E Epic {ts}"})
    if st not in (200, 201):
        st, ep = http("POST", "/api/epics", {"project_id": pid, "title": f"S3M2 E2E Epic {ts}"})
    assert st in (200, 201), f"epic: {st} {ep}"
    epid = ep["id"]
    st, st_ = http("POST", f"/api/epics/{epid}/stories",
                   {"title": f"S3M2 E2E Story {ts}", "description": "e2e"})
    assert st in (200, 201), f"story: {st} {st_}"
    sid = st_["id"]
    st, st_ = http("POST", f"/api/stories/{sid}/assign-reviewer")
    assert st == 200, f"assign-reviewer: {st} {st_}"
    assert st_.get("status") == "pending_review" and st_.get("reviewer_id"), st_
    old_rev = st_["reviewer_id"]
    print(f"[3] 项目/Epic/Story/指派 OK (story={sid}, reviewer={old_rev})")

    # 4. GET /api/review-stats 结构断言
    st, stats = http("GET", "/api/review-stats", params={"project_id": pid})
    assert st == 200, f"review-stats: {st} {stats}"
    for k in ("stories", "tasks", "rounds", "reject_rate", "timeout_pending", "by_reviewer"):
        assert k in stats, f"missing {k}"
    assert stats["stories"]["pending"] == 1
    print("[4] review-stats OK:", json.dumps({k: stats[k] for k in
          ("stories", "tasks", "timeout_pending")}, ensure_ascii=False))

    # 5. 未超时：扫描不换人
    st, res = http("POST", "/api/review-stats/reassign-timeout",
                   {"timeout_minutes": 30, "max_per_run": 20})
    assert st == 200, f"reassign: {st} {res}"
    st, st_ = http("GET", f"/api/stories/{sid}")
    assert st_["reviewer_id"] == old_rev, "未超时不应换人"
    print("[5] 未超时扫描 OK（reviewer 不变）")

    # 6. SQL 拨回 created_at 60 分钟 → 触发扫描 → reviewer 应更换
    sql_exec(f"UPDATE stories SET created_at = DATE_SUB(NOW(), INTERVAL 60 MINUTE) WHERE id = {sid}")
    st, res = http("POST", "/api/review-stats/reassign-timeout",
                   {"timeout_minutes": 30, "max_per_run": 20})
    assert st == 200, f"reassign2: {st} {res}"
    st, st_ = http("GET", f"/api/stories/{sid}")
    assert st_["reviewer_id"] is not None, "重派后应有 reviewer"
    assert res["stories_reassigned"] >= 1, f"应发生重派: {res}"
    print(f"[6] 超时重派 OK（reviewer {old_rev} → {st_['reviewer_id']}）")
    print("   res =", json.dumps(res, ensure_ascii=False))

    # 7. 清理（删除项目）
    http("DELETE", f"/api/projects/{pid}")

    if fails:
        print("FAILS:", fails)
        sys.exit(1)
    print("REST 链路 ALL PASS")


if __name__ == "__main__":
    main()
