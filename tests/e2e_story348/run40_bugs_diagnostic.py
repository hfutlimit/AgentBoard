"""
Run40 targeted diagnostic for /bugs: confirm whether the blank/skeleton state
is a real, reproducible bug vs a transient cold-start artifact.
Repeats the visit 3 times with long settle waits; captures console errors,
network failures to /api/bugs*, DOM skeleton indicators, and main text.
"""
import json, os, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_run40")
os.makedirs(SHOT, exist_ok=True)

def login():
    for _ in range(8):
        try:
            req = urllib.request.Request(BASE + "/api/auth/login",
                data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())["token"]
        except Exception:
            time.sleep(2)
    raise SystemExit("login failed")

TOKEN = login()

# Pre-check API directly (bypassing the dev proxy) to isolate backend vs frontend.
api_results = []
for q in ["/api/bugs?limit=5", "/api/bugs?project_id=3&limit=5", "/api/bugs"]:
    try:
        req = urllib.request.Request("http://124.220.44.12" + q,
            headers={"Authorization": f"Bearer {TOKEN}"}, method="GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read()
            api_results.append({"path": q, "http": r.status,
                                "len": len(body),
                                "sample": body[:200].decode("utf-8", "replace")})
    except Exception as e:
        api_results.append({"path": q, "error": repr(e)[:200]})
print("[API]", json.dumps(api_results, ensure_ascii=False))

report = {"api": api_results, "runs": []}

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")

    for run_i in range(1, 4):
        page = ctx.new_page()
        console_errors = []
        network_failures = []
        page_errors = []
        page.on("console", lambda m: console_errors.append({"type": m.type, "text": m.text[:200]})
                if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)[:200]))

        def _resp(resp):
            if "/api/bugs" in resp.url and resp.status >= 400:
                network_failures.append({"url": resp.url, "status": resp.status,
                                         "body": (lambda: "")()})
        page.on("response", _resp)

        try:
            page.goto(BASE + "/", wait_until="domcontentloaded")
            page.evaluate("t=>localStorage.setItem('agentboard_token', t)", TOKEN)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("app-root", timeout=20000)
            time.sleep(2)

            page.goto(BASE + "/bugs", wait_until="domcontentloaded", timeout=60000)
            # Long settle (30s) with frequent polling
            samples = []
            for k in range(60):
                d = page.evaluate("""()=>{
                    const root=document.querySelector('app-root');
                    const main=document.querySelector('main')||root;
                    const h1=document.querySelector('h1');
                    const skel=document.querySelectorAll('[class*=skeleton],[class*=Skeleton],.loading,.spinner,[class*=loading]');
                    const empty=(document.querySelector('.empty,[class*=empty-state],[class*=Empty]')||{}).innerText||'';
                    return {
                        root_len: root?((root.innerText||'').trim().length):0,
                        main_len: main?((main.innerText||'').trim().length):0,
                        h1: h1?h1.innerText.trim():'',
                        skeleton_count: skel.length,
                        empty_hint: empty.trim().slice(0,80)
                    };
                }""")
                samples.append(d)
                if k % 10 == 0:
                    print(f"[RUN{run_i} t={k*0.5:.1f}s] root={d['root_len']} main={d['main_len']} h1={d['h1'][:30]!r} skel={d['skeleton_count']} empty={d['empty_hint'][:30]!r}")
                if d["main_len"] > 30 and d["skeleton_count"] == 0:
                    break
                page.wait_for_timeout(500)

            final = samples[-1] if samples else {}
            shot = os.path.join(SHOT, f"bugs_run{run_i}.png")
            page.screenshot(path=shot, full_page=True)
            report["runs"].append({
                "run": run_i,
                "samples_count": len(samples),
                "final": final,
                "main_text_sample": page.evaluate("()=>{const m=document.querySelector('main')||document.querySelector('app-root'); return m?m.innerText.slice(0,500):'';}"),
                "screenshot": os.path.relpath(shot, OUT),
                "console_errors": console_errors[:10],
                "page_errors": page_errors[:10],
                "network_failures": network_failures[:10],
            })
            print(f"[RUN{run_i} DONE] main={final.get('main_len')} h1={final.get('h1')[:30]!r} skel={final.get('skeleton_count')} netfail={len(network_failures)} console_err={len(console_errors)}")
        except Exception as e:
            report["runs"].append({"run": run_i, "error": repr(e)[:300]})
            print(f"[RUN{run_i} EXC] {repr(e)[:200]}")
        finally:
            page.close()

    # Also: navigate from a known-good page to /bugs via SPA click to test in-app routing
    try:
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.evaluate("t=>localStorage.setItem('agentboard_token', t)", TOKEN)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("app-root", timeout=20000)
        time.sleep(2)
        page.goto(BASE + "/projects", wait_until="domcontentloaded")
        time.sleep(2)
        # Try direct goto
        page.goto(BASE + "/bugs", wait_until="domcontentloaded")
        time.sleep(8)
        d = page.evaluate("""()=>{
            const m=document.querySelector('main')||document.querySelector('app-root');
            const h1=document.querySelector('h1');
            return {main_len:m?m.innerText.trim().length:0,h1:h1?h1.innerText.trim():''};
        }""")
        page.screenshot(path=os.path.join(SHOT, "bugs_after_warmup.png"), full_page=True)
        report["after_warmup"] = d
        print(f"[WARMUP] main={d['main_len']} h1={d['h1'][:40]!r}")
    except Exception as e:
        report["after_warmup"] = {"error": repr(e)[:200]}
    finally:
        page.close()

    b.close()

with open(os.path.join(OUT, "report_run40_bugs_diagnostic.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("DONE")
