"""One-shot bookkeeping: record Epic 30 v0.8 cache work as tasks in the RUNTIME SQLite DB (58125).

Why not MCP: the MCP server is on an isolated DB; its create_task returned a
phantom id (708) that exists in no verifiable DB, and set_status is known-buggy.
The runtime SQLite (58125) is what the verified web (8080) + Playwright use,
so task status is tracked there via the documented REST workaround.

Run: managed python scripts/track_epic30_tasks.py
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:58125"
EPIC_ID = 13  # SQLite "Epic 20: API 增强与批量操作 (v0.5)" — best-fit parent for a cache enhancement


def req(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method=method,
    )
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise SystemExit(f"HTTP {e.code} on {method} {path}: {body[:400]}")


def drive_to_in_review(tid):
    # valid state machine: backlog -> todo -> in_progress -> in_review
    for st in ("todo", "in_progress", "in_review"):
        req("PUT", f"/api/tasks/{tid}/status", {"status": st})


def main():
    # 1) Create a story under the API-enhancement epic
    story = req("POST", f"/api/epics/{EPIC_ID}/stories", {
        "title": "API 缓存强化 v0.8（TTL 可配置 + 命中率统计）",
        "description": (
            "Epic 30 等价实现（runtime SQLite）。\n"
            "- Task 801: 全局默认缓存 TTL 经 AGENTBOARD_CACHE_TTL 可配置，"
            "stats/list 端点 TTL 未单独配置时回退到全局默认。\n"
            "- Task 802: SimpleCache 线程安全命中/未命中统计 + GET /api/cache/stats 端点。"
        ),
    })
    sid = story["id"]
    print(f"[story] created id={sid} title={story.get('title')!r}")

    tasks = [
        {
            "title": "Task 801: 扩展 API 缓存 TTL 配置",
            "priority": "high",
            "description": "AGENTBOARD_CACHE_TTL 全局默认；_CACHE_TTL_STATS/_LIST 未单独配置时回退到全局默认。",
            "spec": "cache.py: API_CACHE_TTL = int(getenv('AGENTBOARD_CACHE_TTL','30'))，作为 SimpleCache 默认 TTL。",
        },
        {
            "title": "Task 802: 添加缓存命中率统计",
            "priority": "high",
            "description": "SimpleCache 增加线程安全 hit/miss 计数与 stats()；新增 GET /api/cache/stats 端点（require_business_auth 统一鉴权）。",
            "spec": "cache.py stats() 返回 size/hits/misses/hit_rate/default_ttl；api.py 新增 /api/cache/stats。",
        },
    ]

    tids = []
    for t in tasks:
        body = {"title": t["title"], "type": "task", "priority": t["priority"],
                "description": t["description"], "spec": t["spec"],
                "project_id": 3}
        task = req("POST", f"/api/stories/{sid}/tasks", body)
        tid = task["id"]
        tids.append(tid)
        print(f"[task] created id={tid} title={task.get('title')!r}")

    # 2) Drive both tasks backlog -> todo -> in_progress -> in_review
    for tid in tids:
        drive_to_in_review(tid)
        print(f"[task] {tid} -> in_review")

    # 3) Mark the story in_review
    req("PATCH", f"/api/stories/{sid}", {"status": "in_review"})
    print(f"[story] {sid} -> in_review")

    print("RESULT", json.dumps({"story_id": sid, "task_ids": tids}))


if __name__ == "__main__":
    main()
