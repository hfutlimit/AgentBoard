"""临时端到端验证：文档模块 REST API（含中文 / 状态机 / 评论权限 / 双 Agent review 闭环）。"""
import json
import urllib.request

B = "http://127.0.0.1:58125"


def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        B + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def assert_eq(name, got, exp):
    ok = got == exp
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: got={got} exp={exp}")
    return ok


results = []
# 1. 创建 plan 文档（中文内容）
st, doc = call("POST", "/api/documents", {
    "project_id": 3, "title": "Epic 15 多 Agent 评审闭环示例",
    "content": "# 计划\n正文 **加粗**\n\n```mermaid\nflowchart LR\n A-->B\n```",
    "type": "plan", "status": "draft",
})
results.append(assert_eq("create doc 201", st, 201))
did = doc["id"]

# 2. 列表
st, lst = call("GET", "/api/documents?project_id=3")
results.append(assert_eq("list returns list", isinstance(lst, list), True))

# 3. 非法迁移 draft->approved 期望 400
st, _ = call("PUT", f"/api/documents/{did}/status", {"status": "approved"})
results.append(assert_eq("illegal transition draft->approved 400", st, 400))

# 4. 合法 draft->in_review
st, d = call("PUT", f"/api/documents/{did}/status", {"status": "in_review"})
results.append(assert_eq("draft->in_review 200", st, 200))
results.append(assert_eq("status=in_review", d["status"], "in_review"))

# 5. agent-alpha 评论
st, c1 = call("POST", f"/api/documents/{did}/comments",
              {"author": "agent-alpha", "content": "建议补充验收标准（中文）"})
results.append(assert_eq("comment1 201", st, 201))
cid1 = c1["id"]

# 6. agent-beta 评论
st, c2 = call("POST", f"/api/documents/{did}/comments",
              {"author": "agent-beta", "content": "同意，已补充验收标准"})
results.append(assert_eq("comment2 201", st, 201))
cid2 = c2["id"]

# 7. 错误作者编辑评论 -> 422
st, _ = call("PATCH", f"/api/document-comments/{cid2}",
             {"content": "篡改", "author": "agent-alpha"})
results.append(assert_eq("wrong-author edit 422", st, 422))

# 8. 正确作者编辑评论 -> 200
st, _ = call("PATCH", f"/api/document-comments/{cid2}",
             {"content": "已修订验收标准", "author": "agent-beta"})
results.append(assert_eq("correct-author edit 200", st, 200))

# 9. 评论列表（按时间正序）
st, comments = call("GET", f"/api/documents/{did}/comments")
results.append(assert_eq("comments count 2", len(comments), 2))
results.append(assert_eq("comment order", comments[0]["author"], "agent-alpha"))

# 10. in_review->approved（review 闭环完成）
st, d = call("PUT", f"/api/documents/{did}/status", {"status": "approved"})
results.append(assert_eq("in_review->approved 200", st, 200))

# 11. approved->draft 可重编辑
st, d = call("PUT", f"/api/documents/{did}/status", {"status": "draft"})
results.append(assert_eq("approved->draft 200", st, 200))

# 12. 删除评论+文档
st, _ = call("DELETE", f"/api/document-comments/{cid1}")
results.append(assert_eq("delete comment1 200", st, 200))
st, _ = call("DELETE", f"/api/documents/{did}")
results.append(assert_eq("delete doc 200", st, 200))
st, d = call("GET", f"/api/documents/{did}")
results.append(assert_eq("get deleted 404", st, 404))

print("\n=== SUMMARY ===")
print("ALL PASS" if all(results) else f"{results.count(False)} FAILED")
