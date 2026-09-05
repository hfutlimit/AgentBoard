"""Read-only business evidence extraction; does not advance the workflow.

Works against an isolated harness SQLite DB, never copies lease tokens or keys.
"""
import argparse
import json
from pathlib import Path
import sqlite3
from collections import Counter


def evidence(database: Path, proposal_id: int):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        proposal = dict(db.execute("SELECT id,status,story_id FROM proposals WHERE id=?", (proposal_id,)).fetchone())
        story_id = proposal["story_id"]
        story = db.execute("SELECT id,status FROM stories WHERE id=?", (story_id,)).fetchone()
        tasks = [dict(row) for row in db.execute("SELECT id,type,status FROM tasks WHERE story_id=? ORDER BY id", (story_id,))]
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
                # This harness has one design/dev/QA DAG; QA must not be any
                # Dev implementer, even when profiles share an account.
                independent &= all(w["agent_id"] != work["agent_id"] for w in completed if w["kind"] == "dev")
        passed = bool(story and story["status"] == "done" and tasks and all(t["status"] == "done" for t in tasks)
            and kinds == {"proposal", "design", "design_review", "dev", "dev_review", "qa", "qa_review"}
            and independent and all(max((w for w in completed if w["entity_id"] == t["id"] and w["kind"] == "qa"),
                key=lambda w: w["id"], default={"result": {}})["result"].get("tests_passed") is True
                for t in tasks if t["type"] == "qa"))
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
