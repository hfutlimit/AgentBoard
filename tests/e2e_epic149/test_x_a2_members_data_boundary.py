"""Epic 151 / Story 326 / Task 1297 端到端验证：MembersTab 数据边界。

背景（Epic 149 静态 Review 阻断级 2）：
- 后端 ``/api/agents`` 无 project 过滤 + 返回 ``_ser`` 全列（含 cli_command/
  auth_key/probe_message/user_id）；
- 前端 MembersTab 文案「参与本项目的 Agent 池」与后端数据边界不一致。

Task 1297 修复（后端+前端）：
- 后端：Agent 加 ``to_public_dict()`` 脱敏；``/api/agents`` 加软鉴权（REQUIRE_AUTH=1
  时未登录 401）；service.list_agents 加 ``order_by_created``。
- 前端：heading subtitle 改「上半区…下半区展示全局 Agent 池（跨项目共享，按
  注册时间倒序）」；下半区 section title 改「全局 Agent 池」；badge 改
  「N 个 Agent（全局）」。

E2E 验证（main() 脚本，与同目录 test_x1_*.py 一致）：
1. 启动 dev web_app（REQUIRE_AUTH=0 宽容模式）+ 注入 admin token。
2. 打开 /projects/3/tab=members（侧栏选项目 + tab 切换）。
3. 验证文案：
   - heading subtitle 包含「全局 Agent 池」+「跨项目共享」+「按注册时间倒序」；
   - 下半区 section title ==「全局 Agent 池」；
   - badge ==「N 成员 · N Agent（全局）」（含中文括号）。
4. 验证脱敏（API 直连 dev server）：
   - ``GET /api/agents`` 响应不含 cli_command/auth_key/probe_message/user_id。
5. 验证鉴权（模拟 REQUIRE_AUTH=1）：未登录 → 401；带 token → 200。
6. 截图 ``_x_a2_members.png``。

注：Task 1297 的后端契约由 ``tests/test_agent_public_dict.py``（pytest）
覆盖；本脚本主要负责「前端文案 + 浏览器实际渲染」一环。

2026-08-20 创建。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

FRONTEND_ORIGIN = "http://127.0.0.1:4200"
# 默认走 127.0.0.1:18000 dev web_app（E2E 启动脚本会拉起），可被 env 覆盖
API_BASE = os.environ.get("AGENTBOARD_API_BASE", "http://127.0.0.1:18000")
ADMIN_USER = os.environ.get("AGENTBOARD_E2E_USER", "admin")
ADMIN_PASS = os.environ.get("AGENTBOARD_E2E_PASS", "admin123")

ROOT = Path(__file__).resolve().parent
SHOT_DIR = ROOT / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def login() -> str:
    """经 API 直接拿 token（dev 模式 + admin）。"""
    req = urllib.request.Request(
        f"{API_BASE}/api/auth/login",
        data=json.dumps({"username": ADMIN_USER, "password": ADMIN_PASS}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["token"]


def fetch_agents(token: str | None = None) -> tuple[int, list[dict]]:
    """GET /api/agents 返回 (status_code, body)。"""
    req = urllib.request.Request(f"{API_BASE}/api/agents")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, []


def register_agent(token: str, agent_id: str = "e2e-x-a2-bot",
                   name: str = "E2E X.A2 Bot") -> int:
    """注册 1 个含敏感字段的 Agent（PART A 字段收窄验证需要）。返回 status。

    注意：``AgentRegisterIn`` schema 要求 ``roles`` 是 JSON 字符串
    （与 ``capabilities`` 一致），不是 list。CLI 模板 ``codebuddy --model {model}``
    是合法模板（不含 shell 元字符），供 service.validate_cli_command 放行。
    """
    body = json.dumps({
        "agent_id": agent_id,
        "name": name,
        "roles": '["reviewer"]',
        "capabilities": "[]",
        "cli_command": "codebuddy --model {model}",
        "model": "hy3",
        "auth_key": "abk_e2e_x_a2_fingerprint",  # noqa: S105
    }).encode()
    req = urllib.request.Request(
        f"{API_BASE}/api/agents/register", data=body, method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def goto_project_members(page: Page) -> None:
    """导航到 project view + 切到 members tab（members tab 内容组件加载）。

    路径：``/project/1`` 进 project view → 侧栏点「成员与 Agents」button
    （新版 ``project-nav-v7`` 侧栏，不是 emoji tab bar）。
    """
    candidates = ["/project/1", "/project/2", "/project/3"]
    for path in candidates:
        print(f"   try {path}...")
        page.goto(f"{FRONTEND_ORIGIN}{path}", wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector("app-workspace-heading", timeout=8000)
            print(f"   got workspace-heading at {path}")
            break
        except Exception:
            print(f"   no workspace-heading at {path}, trying next")
    time.sleep(0.4)
    # 切到 members tab：项目工作台 navy 侧栏 8 tab（project-nav-v7）
    members_btn = page.locator("button.project-nav-button-v7:has-text('成员与 Agents')").first
    members_btn.click()
    page.wait_for_selector("app-members-tab", timeout=15000)
    time.sleep(0.5)


def main() -> int:
    failures: list[str] = []
    print(">>> login")
    try:
        token = login()
        print(f"   token len={len(token)}")
    except Exception as e:
        print(f"FAIL: cannot reach API at {API_BASE}: {e!r}")
        return 1

    # ---- Part A: API 字段收窄 ----
    print(">>> PART A — API field narrowing")
    # 先确保有至少 1 个 agent（dev DB 可能空）
    rs = register_agent(token)
    if rs not in (200, 201):
        # 已存在（200 idempotent）也 OK，201 首次
        print(f"   register_agent returned {rs} (continuing)")
    else:
        print(f"   register_agent ok (status={rs})")

    status, rows = fetch_agents(token=token)
    if status != 200:
        failures.append(f"GET /api/agents w/ token: expected 200, got {status}")
    elif not rows:
        failures.append("GET /api/agents returned empty after register")
    else:
        sample = rows[0]
        print(f"   sample keys: {sorted(sample.keys())}")
        for forbidden in ("cli_command", "auth_key", "probe_message", "user_id"):
            if forbidden in sample:
                failures.append(
                    f"API leaked {forbidden}: {sample[forbidden]!r}"
                )
        # 公开字段应存在
        for must in ("id", "agent_id", "name", "model", "online", "enabled"):
            if must not in sample:
                failures.append(f"API missing public field {must}")

    # ---- Part B: API 软鉴权（dev 模式无 token 也 200，但 REQUIRE_AUTH=1 时 401）----
    # 本 E2E 跑 dev 模式（REQUIRE_AUTH=0），所以无 token 也 200；只验证有 token
    # 的状态码。REQUIRE_AUTH=1 的分支由 test_agent_public_dict.py 单元测试覆盖。
    print(">>> PART B — API dev-mode allow (no token)")
    status_noauth, rows_noauth = fetch_agents(token=None)
    if status_noauth != 200:
        failures.append(f"dev-mode GET /api/agents no token: expected 200, got {status_noauth}")

    # ---- Part C: 前端文案（Playwright）----
    print(">>> PART C — frontend copy + render")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            page.add_init_script(
                "localStorage.setItem('agentboard_token', %s);"
                "localStorage.setItem('agentboard_user', 'admin');"
                % json.dumps(token)
            )
            goto_project_members(page)

            # C1: heading subtitle
            sub = page.locator("app-members-tab .workspace-heading-subtitle, "
                               "app-members-tab .heading-subtitle, "
                               "app-members-tab p:has-text('Agent 池')").first
            sub_text = (sub.text_content() or "").strip()
            print(f"   subtitle: {sub_text[:80]}…")
            for needle in ("全局 Agent 池", "跨项目共享", "按注册时间倒序"):
                if needle not in sub_text:
                    failures.append(f"subtitle missing {needle!r}: {sub_text!r}")

            # C2: section title
            sec = page.locator("app-members-tab h4:has-text('全局 Agent 池')").first
            if sec.count() == 0:
                failures.append("section title '全局 Agent 池' not found")
            else:
                print(f"   section title: {(sec.text_content() or '').strip()}")

            # C3: badge 包含「Agent（全局）」
            badge = page.locator("app-members-tab .count-badge").first
            badge_text = (badge.text_content() or "").strip()
            print(f"   badge: {badge_text}")
            if "Agent（全局）" not in badge_text:
                failures.append(f"badge missing 'Agent（全局）': {badge_text!r}")

            page.screenshot(path=str(SHOT_DIR / "_x_a2_members.png"), full_page=True)
        except Exception as e:
            failures.append(f"exception: {e!r}")
            try:
                page.screenshot(path=str(SHOT_DIR / "_x_a2_error.png"), full_page=True)
            except Exception:
                pass
        finally:
            ctx.close()
            browser.close()

    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS — X.A2 MembersTab data boundary OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
