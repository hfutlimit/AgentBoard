"""Read-only business evidence extraction; does not advance the workflow.

Works against an isolated harness SQLite DB, never copies lease tokens or keys.
"""
import argparse
import json
from pathlib import Path
import sqlite3
from collections import Counter


def qa_acceptance_closed(tasks, completed, edges):
    """A reviewed failed QA is legitimate only with a verified repair/retest chain."""
    task_by_id = {t["id"]: t for t in tasks}
    def closed(task_id, visiting):
        if task_id in visiting:
            return False
        qa = max((w for w in completed if w["entity_type"] == "task" and w["entity_id"] == task_id
                  and w["kind"] == "qa"), key=lambda w: w["id"], default=None)
        review = max((w for w in completed if w["entity_type"] == "task" and w["entity_id"] == task_id
                      and w["kind"] == "qa_review"), key=lambda w: w["id"], default=None)
        if not qa or not review or review["result"].get("decision") != "approve" or review["iteration"] != qa["iteration"]:
            return False
        if qa["result"].get("tests_passed") is True:
            return True
        plan = review["result"].get("qa_followup", {})
        defects = qa["result"].get("defects", [])
        if (not defects or plan.get("source_work_id") != qa["id"] or plan.get("bugs") != defects):
            return False
        linked = [t for t in tasks if f"qa-source-work:{qa['id']}" in json.loads(t.get("labels", "[]"))]
        bugs = [t for t in linked if t["type"] == "bug"]
        retests = [t for t in linked if t["type"] == "qa"]
        return (len(bugs) == len(defects) and len(retests) == 1
            and all(t["status"] == "done" and (t["id"], task_id) in edges for t in bugs)
            and all((retests[0]["id"], t["id"]) in edges for t in bugs)
            and closed(retests[0]["id"], visiting | {task_id}))
    return all(closed(t["id"], set()) for t in task_by_id.values() if t["type"] == "qa")


def evidence(database: Path, proposal_id: int):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        proposal = dict(db.execute("SELECT id,status,story_id FROM proposals WHERE id=?", (proposal_id,)).fetchone())
        story_id = proposal["story_id"]
        story = db.execute("SELECT id,status FROM stories WHERE id=?", (story_id,)).fetchone()
        tasks = [dict(row) for row in db.execute("SELECT id,type,status,labels FROM tasks WHERE story_id=? ORDER BY id", (story_id,))]
        edges = {(r[0], r[1]) for r in db.execute("SELECT task_id,depends_on_id FROM task_dependencies")}
        has_history = "attempt_history" in {r[1] for r in db.execute("PRAGMA table_info(worker_work)")}
        history_column = "w.attempt_history" if has_history else "'[]'"
        rows = db.execute(f"""SELECT w.id,w.entity_type,w.entity_id,w.kind,w.iteration,w.state,
            {history_column} AS attempt_history,
            w.attempts,w.worker_id,a.agent_id,w.result FROM worker_work w LEFT JOIN agents a ON a.id=w.agent_id
            WHERE (w.entity_type='proposal' AND w.entity_id=?) OR
              (w.entity_type='task' AND w.entity_id IN (SELECT id FROM tasks WHERE story_id=?)) ORDER BY w.id""",
            (proposal_id, story_id))
        works = []
        for row in rows:
            item = dict(row)
            item["result"] = json.loads(item["result"] or "{}")
            item["attempt_history"] = json.loads(item["attempt_history"])
            works.append(item)
        failures = Counter()
        for work in works:
            results = [a.get("result") for a in work["attempt_history"]] + [work["result"]]
            for result in results:
                if result and "decision" not in result and result.get("summary"):
                    failures[result["summary"]] += 1
        completed = [w for w in works if w["state"] == "completed"]
        kinds = {w["kind"] for w in completed}
        independent = True
        for work in completed:
            if work["kind"].endswith("_review"):
                original = [w for w in completed if w["entity_id"] == work["entity_id"]
                            and w["kind"] == work["kind"].removesuffix("_review") and w["iteration"] == work["iteration"]]
                independent &= bool(original) and all(w["agent_id"] != work["agent_id"] for w in original)
            if work["kind"] == "qa":
                upstream, pending = set(), [work["entity_id"]]
                while pending:
                    current = pending.pop()
                    for child, parent in edges:
                        if child == current and parent not in upstream:
                            upstream.add(parent)
                            pending.append(parent)
                independent &= all(w["agent_id"] != work["agent_id"] for w in completed
                                   if w["kind"] == "dev" and w["entity_id"] in upstream)
        passed = bool(story and story["status"] == "done" and tasks and all(t["status"] == "done" for t in tasks)
            and kinds == {"proposal", "design", "design_review", "dev", "dev_review", "qa", "qa_review"}
            and independent and qa_acceptance_closed(tasks, completed, edges))
        return {"passed": passed, "proposal": proposal, "story": dict(story) if story else None,
                "tasks": tasks, "independent_agents": independent, "failure_reasons": dict(failures), "works": works}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--proposal-id", type=int, default=1)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = evidence(args.database, args.proposal_id)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "work_kinds": [w["kind"] for w in result["works"]],
        "tasks": result["tasks"], "report": str(args.report)}, ensure_ascii=False))
