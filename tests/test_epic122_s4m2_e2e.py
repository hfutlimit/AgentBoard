"""Epic 122 S4 M2 E2E：多数决评审投票进度提示（评审运营面板 + 0 报错）。

覆盖（对应 Task 1017 验收）：
- 自起独立栈：uvicorn（AGENTBOARD_REVIEW_MODE=majority, quorum=3, 独立 SQLite,
  REQUIRE_AUTH=0 匿名可读）+ web_app 静态托管（AGENTBOARD_API_URL 指向该 api）；
- service 直连同一 DB 造数据：项目 + pending_review Story（reviewer 已指派）+ 2 票 approve；
- Playwright：项目页 → 统计 Tab → 评审运营面板：
  * review-mode-badge 渲染「多数决评审 · 法定 3 票」（.majority 激活态）；
  * 投票进度区块 #review-votes-block 渲染 Story 行（标题 + ✓ 2 + ✗ 0 + 2/3 票 + 进度条）;
  * 0 console / pageerror / js-css 404。
"""
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
PY = sys.executable


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return
        except Exception:
            pass
        time.sleep(0.4)
    raise RuntimeError(f"not ready: {url}")


def main():
    db = tempfile.mktemp(suffix=".db")
    api_port = _free_port()
    web_port = _free_port()
    api_url = f"http://127.0.0.1:{api_port}"

    env = dict(os.environ)
    env["AGENTBOARD_DB_URL"] = f"sqlite:///{db}"
    env["AGENTBOARD_REQUIRE_AUTH"] = "0"
    env["AGENTBOARD_REVIEW_MODE"] = "majority"
    env["AGENTBOARD_REVIEW_QUORUM"] = "3"
    # 本进程后续 import agentboard 也须读到同一 env（子进程与造数据同库）
    os.environ.update({k: v for k, v in env.items()
                       if k in ("AGENTBOARD_DB_URL", "AGENTBOARD_REQUIRE_AUTH",
                                "AGENTBOARD_REVIEW_MODE", "AGENTBOARD_REVIEW_QUORUM")})

    # 1) 起 API（majority 模式）
    api_proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(api_port)],
        cwd=_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 2) 起 Web 静态托管（API 注入；显式指定已部署静态目录）
    web_env = {**env,
               "AGENTBOARD_API_URL": api_url,
               "AGENTBOARD_WEB_STATIC_DIR": str(_ROOT / "agentboard" / "web" / "static")}
    web_proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "agentboard.web_app:app",
         "--host", "127.0.0.1", "--port", str(web_port)],
        cwd=_ROOT, env=web_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    fails = []
    try:
        _wait_http(api_url + "/api/meta")
        _wait_http(f"http://127.0.0.1:{web_port}/")

        # 3) 造数据：项目 + pending_review Story + 2 票 approve
        from agentboard import service
        from agentboard.database import SessionLocal
        import uuid
        tag = uuid.uuid4().hex[:8]
        with SessionLocal() as s:
            p = service.create_project(s, name=f"S4M2 E2E {tag}")
            dev = service.register_user(s, username=f"s4m2-dev-{tag}", password="password123")
            r1 = service.register_user(s, username=f"s4m2-r1-{tag}", password="password123")
            r2 = service.register_user(s, username=f"s4m2-r2-{tag}", password="password123")
            r3 = service.register_user(s, username=f"s4m2-r3-{tag}", password="password123")
            pid, dev_id, r1_id, r2_id, r3_id = p.id, dev.id, r1.id, r2.id, r3.id
            for uid in (dev_id, r1_id, r2_id, r3_id):
                service.add_project_member(s, project_id=pid, user_id=uid, role="member")
            for i, uid in ((1, r1_id), (2, r2_id), (3, r3_id)):
                aid = f"s4m2-{i}-{tag}"
                service.register_agent(s, agent_id=aid, name=f"A{i}",
                                       roles='["reviewer"]', user_id=uid)
                service.agent_heartbeat(s, aid, user_id=uid)
            epic = service.create_epic(s, project_id=pid, title=f"S4M2 Epic {tag}")
            from agentboard.models import Story
            st = Story(epic_id=epic.id, title="多数决投票进度验证 Story",
                       status="pending_review", reviewer_id=r1_id, review_round=0)
            s.add(st)
            s.flush()
            st_id = st.id
            s.commit()
        # 2 票 approve（r1 + r2）→ cast=2/3
        with SessionLocal() as s:
            service._upsert_review_vote(s, entity_type="story", entity_id=st_id,
                                        reviewer_user_id=r1_id, verdict="approve",
                                        comment_id=None, round=0)
            s.commit()
            service._upsert_review_vote(s, entity_type="story", entity_id=st_id,
                                        reviewer_user_id=r2_id, verdict="approve",
                                        comment_id=None, round=0)
            s.commit()

        # 4) Playwright 验证
        console_errors, page_errors, bad = [], [], []
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.on("console", lambda m: console_errors.append(m.text)
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.on("requestfailed", lambda r: bad.append(
                f"{r.method} {r.url} {r.failure}")
                if "api" in r.url and "ERR_ABORTED" not in (r.failure or "") else None)
            # 前端路由守卫需 localStorage token：匿名栈下注册用户登录拿真实 token
            import json as _json
            import urllib.request as _ur
            def _register(user):
                req = _ur.Request(
                    api_url + "/api/auth/register",
                    data=_json.dumps({"username": user, "password": "password123"}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with _ur.urlopen(req, timeout=10) as r:
                        return _json.loads(r.read())["token"]
                except Exception:
                    req = _ur.Request(
                        api_url + "/api/auth/login",
                        data=_json.dumps({"username": user, "password": "password123"}).encode(),
                        headers={"Content-Type": "application/json"}, method="POST")
                    with _ur.urlopen(req, timeout=10) as r:
                        return _json.loads(r.read())["token"]
            viewer = f"e2e-viewer-{tag}"
            token = _register(viewer)
            # viewer 加为项目成员（REQUIRE_AUTH=0 下项目列表仍按可见性过滤）
            with SessionLocal() as s:
                v = s.query(service.User).filter(service.User.username == viewer).first()
                service.add_project_member(s, project_id=pid, user_id=v.id, role="member")
                s.commit()
            page.add_init_script(f"localStorage.setItem('agentboard_token', {token!r})")

            page.goto(f"http://127.0.0.1:{web_port}/project/{pid}",
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector(".tab-bar", timeout=30000)
            page.get_by_role("button", name="统计", exact=True).click()
            page.wait_for_selector("#review-ops-panel", timeout=30000)

            # 模式徽标
            badge = page.locator(".review-mode-badge")
            assert badge.count() == 1, "评审模式徽标缺失"
            badge_text = badge.inner_text()
            assert "多数决评审" in badge_text and "法定 3 票" in badge_text, \
                f"徽标文案错误: {badge_text}"
            assert badge.evaluate("el => el.classList.contains('majority')"), \
                "majority 徽标未激活"

            # 投票进度区块
            block = page.locator("#review-votes-block")
            assert block.count() == 1, "投票进度区块缺失"
            text = block.inner_text()
            assert "多数决投票进度" in text, "区块标题缺失"
            assert "多数决投票进度验证 Story" in text, "Story 标题缺失"
            assert "✓ 2" in text and "✗ 0" in text, f"票数计数错误: {text}"
            assert "2/3 票" in text, f"已投/法定票数缺失: {text}"
            print("[1] 模式徽标 + 投票进度区块渲染 OK")

            # 进度条宽度 = 2/3 ≈ 67%
            fill = block.locator(".review-vote-bar .fill").first
            w = fill.evaluate("el => el.style.width")
            assert w and 60 <= float(w.replace('%', '')) <= 70, \
                f"进度条宽度异常: {w}"
            print(f"[2] 进度条 {w} OK")

            browser.close()

        errs = console_errors[:6] + page_errors[:6] + bad[:6]
        print("console errors:", console_errors[:6])
        print("page errors:", page_errors[:6])
        print("bad requests:", bad[:6])
        if errs:
            fails.append("JS 报错/失败请求非零")
    finally:
        api_proc.terminate()
        web_proc.terminate()
        for p in (api_proc, web_proc):
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
        for f in (db,):
            try:
                os.remove(f)
            except OSError:
                pass

    if fails:
        print("FAILS:", fails)
        sys.exit(1)
    print("PLAYWRIGHT S4M2 VOTES ALL PASS")


if __name__ == "__main__":
    main()
