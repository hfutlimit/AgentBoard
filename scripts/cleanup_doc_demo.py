from playwright.sync_api import sync_playwright

BASE = "http://localhost:28080"
PROJECT_ID = 3

with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page()
    pg.goto(BASE + "/", wait_until="networkidle")
    pg.fill("input[name=username]", "admin")
    pg.fill("input[name=password]", "admin123")
    pg.click(".login-submit")
    pg.wait_for_timeout(1500)
    # list docs for project
    docs = pg.evaluate(
        "async () => {"
        "  const t = localStorage.getItem('agentboard_token');"
        "  const r = await fetch('/api/documents?project_id=%d', {headers:{'Authorization':'Bearer '+t}});"
        "  return await r.json();"
        "}" % PROJECT_ID
    )
    print("total docs in project:", len(docs))
    deleted = 0
    for d in docs:
        if d.get("title") == "Tab 文档验证 Demo":
            pg.evaluate(
                "async (id) => {"
                "  const t = localStorage.getItem('agentboard_token');"
                "  await fetch('/api/documents/'+id, {method:'DELETE', headers:{'Authorization':'Bearer '+t}});"
                "}",
                d["id"],
            )
            deleted += 1
    print("deleted demo docs:", deleted)
    b.close()
