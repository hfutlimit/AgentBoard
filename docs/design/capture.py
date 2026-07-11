import asyncio, os
from playwright.async_api import async_playwright

OUT = os.path.join(os.path.dirname(__file__), "shots")
os.makedirs(OUT, exist_ok=True)
BASE = "http://localhost:8080"

async def shot(page, name, full=True):
    path = os.path.join(OUT, name)
    await page.screenshot(path=path, full_page=full)
    print("saved", path)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        await pg.goto(BASE + "/", wait_until="networkidle")
        await pg.wait_for_timeout(800)
        await shot(pg, "01-dashboard.png")

        # all projects
        await pg.evaluate("location.hash='#/projects'")
        await pg.wait_for_timeout(800)
        await shot(pg, "02-projects.png")

        # grab first project id from API and open it
        proj = await pg.evaluate("""async () => {
            const r = await fetch('http://localhost:8000/api/projects');
            const d = await r.json();
            return d && d.length ? d[0].id : null;
        }""")
        print("first project id:", proj)
        if proj:
            await pg.evaluate(f"location.hash='#/project/{proj}'")
            await pg.wait_for_timeout(1000)
            await shot(pg, "03-project.png")

            # pick an epic/story id
            epic = await pg.evaluate("""async () => {
                const r = await fetch('http://localhost:8000/api/epics?project_id=%s');
                const d = await r.json();
                return d && d.length ? d[0].id : null;
            }""" % proj)
            print("first epic id:", epic)
            if epic:
                await pg.evaluate(f"location.hash='#/epic/{epic}'")
                await pg.wait_for_timeout(1000)
                await shot(pg, "04-epic.png")

                story = await pg.evaluate("""async () => {
                    const r = await fetch('http://localhost:8000/api/stories?epic_id=%s');
                    const d = await r.json();
                    return d && d.length ? d[0].id : null;
                }""" % epic)
                print("first story id:", story)
                if story:
                    await pg.evaluate(f"location.hash='#/story/{story}'")
                    await pg.wait_for_timeout(1200)
                    await shot(pg, "05-story.png")
        await b.close()

asyncio.run(main())
