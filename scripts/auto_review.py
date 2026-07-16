"""自动化 Code Review — 对 in_review 任务进行数据完整性检查并更新状态。

审查规则：
- PASS (→done): 有 description + spec 内容，外键有效
- FAIL (→in_progress): 缺少 description 或 spec，或数据异常
"""

import pymysql
import json
from datetime import datetime, timezone

DB_CONFIG = {
    "host": "localhost",
    "port": 13306,
    "user": "agentboard",
    "password": "agentboard",
    "database": "agentboard",
    "charset": "utf8mb4",
}

REVIEWER = "auto-review-bot"


def get_conn():
    return pymysql.connect(**DB_CONFIG)


def fetch_in_review_tasks(conn):
    sql = """
        SELECT id, project_id, story_id, sprint_id, assignee_id,
               type, title, status, priority,
               description, spec, created_at, updated_at
        FROM tasks WHERE status='in_review' ORDER BY id
    """
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(sql)
        return cur.fetchall()


def fetch_projects(conn):
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute("SELECT id, name FROM projects")
        return {r["id"]: r["name"] for r in cur.fetchall()}


def fetch_stories(conn):
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute("SELECT id, title, status FROM stories")
        return {r["id"]: r for r in cur.fetchall()}


def add_comment(conn, task_id, content):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    sql = """
        INSERT INTO comments (task_id, author, content, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (task_id, REVIEWER, content, now, now))
    conn.commit()


def update_task_status(conn, task_id, new_status):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    sql = "UPDATE tasks SET status=%s, updated_at=%s WHERE id=%s"
    with conn.cursor() as cur:
        cur.execute(sql, (new_status, now, task_id))
    conn.commit()


def review_task(task, projects, stories):
    """返回 (verdict, reasons) — verdict: 'pass' | 'fail'"""
    tid = task["id"]
    title = task["title"]
    reasons = []
    issues = []

    # 1. Check project_id
    pid = task["project_id"]
    if pid not in projects:
        issues.append(f"project_id={pid} 不存在于 projects 表")
    else:
        reasons.append(f"project: {projects[pid]} (id={pid})")

    # 2. Check story_id
    sid = task["story_id"]
    if sid is not None:
        if sid not in stories:
            issues.append(f"story_id={sid} 不存在于 stories 表")
        else:
            story = stories[sid]
            reasons.append(f"story: {story['title']} (id={sid}, status={story['status']})")

    # 3. Check description
    desc = (task["description"] or "").strip()
    if not desc:
        issues.append("description 为空 — 任务缺少功能描述")

    # 4. Check spec
    spec = (task["spec"] or "").strip()
    if not spec:
        issues.append("spec 为空 — 任务缺少实现规范")

    if issues:
        return ("fail", issues)
    return ("pass", reasons)


def main():
    conn = get_conn()
    try:
        tasks = fetch_in_review_tasks(conn)
        if not tasks:
            print("没有 in_review 状态的任务，跳过。")
            return

        projects = fetch_projects(conn)
        stories = fetch_stories(conn)

        print(f"找到 {len(tasks)} 个 in_review 任务\n")
        print("=" * 70)

        results = {"pass": [], "fail": []}

        for task in tasks:
            tid = task["id"]
            title = task["title"]
            verdict, findings = review_task(task, projects, stories)

            print(f"\n[{verdict.upper()}] Task #{tid}: {title}")

            if verdict == "pass":
                update_task_status(conn, tid, "done")
                summary = " | ".join(findings)
                comment = (
                    f"## Auto-Review: PASS → done\n\n"
                    f"审查时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
                    f"### 检查结果\n"
                    + "\n".join(f"- ✅ {r}" for r in findings)
                    + f"\n\n### 结论\n所有检查通过，任务标记为 done。"
                )
                add_comment(conn, tid, comment)
                results["pass"].append(tid)
                print(f"  → done")

            else:
                update_task_status(conn, tid, "in_progress")
                comment = (
                    f"## Auto-Review: FAIL → in_progress\n\n"
                    f"审查时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
                    f"### 发现的问题\n"
                    + "\n".join(f"- ❌ {r}" for r in findings)
                    + f"\n\n### 修复建议\n"
                    f"1. 补充 description（任务功能描述）\n"
                    f"2. 补充 spec（实现规范/技术细节）\n"
                    f"3. 确保所有外键引用有效\n"
                    f"\n修复后重新提交审核（状态改为 in_review）。"
                )
                add_comment(conn, tid, comment)
                results["fail"].append(tid)
                for f in findings:
                    print(f"  ❌ {f}")
                print(f"  → in_progress")

        print("\n" + "=" * 70)
        print(f"\n审查完成：{len(results['pass'])} 通过, {len(results['fail'])} 未通过")

        # Summary
        if results["pass"]:
            print(f"\n✅ 已通过 (done): {results['pass']}")
        if results["fail"]:
            print(f"\n❌ 未通过 (in_progress): {results['fail']}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
