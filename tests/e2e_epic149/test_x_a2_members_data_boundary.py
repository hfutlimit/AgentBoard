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

E2E 验证（Story 330 / Task 1323 改造为 pytest）：
1. API 字段收窄：GET /api/agents 不含 cli_command/auth_key/probe_message/user_id
2. dev 模式软鉴权：无 token 也 200
3. 前端文案：subtitle 含「全局 Agent 池」+「跨项目共享」+「按注册时间倒序」；
   section title ==「全局 Agent 池」；badge 含「Agent（全局）」

注：Task 1297 的后端契约由 ``tests/test_agent_public_dict.py``（pytest）
覆盖；本脚本主要负责「前端文案 + 浏览器实际渲染」一环。

2026-08-20 创建；Story 330 改造为 pytest 收集格式。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

import pytest
from playwright.sync_api import Page

from conftest import FRONTEND_ORIGIN, SHOT_DIR, log, goto_url_with_token


def login() -> str:
    """经 API 直接拿 token（dev 模式 + admin）。"""
    from conftest import API_BASE, ADMIN_USER, ADMIN_PASS
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
    from conftest import API_BASE
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
    """注册 1 个含敏感字段的 Agent（PART A 字段收窄验证需要）。"""
    from conftest import API_BASE
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


def goto_project_members(page: Page, token: str) -> None:
    """导航到 project view + 切到 members tab。"""
    # 2-step token 注入（避免 validateAuth race）
    goto_url_with_token(page, token, f"{FRONTEND_ORIGIN}/", first=True)

    candidates = ["/project/1", "/project/2", "/project/3"]
    for path in candidates:
        log(f"   try {path}...")
        goto_url_with_token(page, token, f"{FRONTEND_ORIGIN}{path}")
        try:
            page.wait_for_selector("app-workspace-heading", timeout=8000)
            log(f"   got workspace-heading at {path}")
            break
        except Exception:
            log(f"   no workspace-heading at {path}, trying next")
    time.sleep(0.4)
    members_btn = page.locator("a.project-nav-button-v7:has-text('成员与 Agents')").first
    members_btn.click()
    page.wait_for_selector("app-members-tab", timeout=15000)
    time.sleep(0.5)


# ============================================================
# Pytest tests
# ============================================================

@pytest.mark.e2e
def test_api_field_narrowing_and_soft_auth(admin_token: str) -> None:
    """PART A + B：API 字段收窄 + dev 模式软鉴权。"""
    # 确保有至少 1 个 agent
    rs = register_agent(admin_token)
    log(f"   register_agent returned {rs}")

    # 带 token 调
    status, rows = fetch_agents(token=admin_token)
    assert status == 200, f"GET /api/agents w/ token: expected 200, got {status}"
    assert rows, "GET /api/agents returned empty after register"
    sample = rows[0]
    log(f"   sample keys: {sorted(sample.keys())}")
    for forbidden in ("cli_command", "auth_key", "probe_message", "user_id"):
        assert forbidden not in sample, f"API leaked {forbidden}: {sample.get(forbidden)!r}"
    for must in ("id", "agent_id", "name", "model", "online", "enabled"):
        assert must in sample, f"API missing public field {must}"

    # dev 模式无 token 也 200
    status_noauth, _ = fetch_agents(token=None)
    assert status_noauth == 200, f"dev-mode GET /api/agents no token: expected 200, got {status_noauth}"


@pytest.mark.e2e
def test_members_tab_frontend_copy_and_render(page: Page, admin_token: str) -> None:
    """PART C：前端文案 + 浏览器渲染。"""
    goto_project_members(page, admin_token)

    # C1: subtitle
    sub = page.locator(
        "app-members-tab .workspace-heading-subtitle, "
        "app-members-tab .heading-subtitle, "
        "app-members-tab p:has-text('Agent 池')"
    ).first
    sub_text = (sub.text_content() or "").strip()
    log(f"   subtitle: {sub_text[:80]}")
    for needle in ("全局 Agent 池", "跨项目共享", "按注册时间倒序"):
        assert needle in sub_text, f"subtitle missing {needle!r}: {sub_text!r}"

    # C2: section title
    sec = page.locator("app-members-tab h4:has-text('全局 Agent 池')").first
    assert sec.count() > 0, "section title '全局 Agent 池' not found"
    log(f"   section title: {(sec.text_content() or '').strip()}")

    # C3: badge
    badge = page.locator("app-members-tab .count-badge").first
    badge_text = (badge.text_content() or "").strip()
    log(f"   badge: {badge_text}")
    assert "Agent（全局）" in badge_text, f"badge missing 'Agent（全局）': {badge_text!r}"

    page.screenshot(path=str(SHOT_DIR / "_x_a2_members.png"), full_page=True)


# ============================================================
# 兼容老 main() 入口
# ============================================================

def main() -> int:
    import urllib.request
    from playwright.sync_api import sync_playwright

    log(">>> login")
    try:
        token = login()
    except Exception as e:
        log(f"FAIL: cannot reach API: {e!r}")
        return 1
    log(f"   token len={len(token)}")

    failures: list[str] = []
    log(">>> PART A — API field narrowing")
    rs = register_agent(token)
    log(f"   register_agent status={rs}")
    status, rows = fetch_agents(token=token)
    if status != 200 or not rows:
        failures.append("GET /api/agents failed")
    else:
        for forbidden in ("cli_command", "auth_key", "probe_message", "user_id"):
            if forbidden in rows[0]:
                failures.append(f"API leaked {forbidden}")
    status_noauth, _ = fetch_agents(token=None)
    if status_noauth != 200:
        failures.append(f"dev-mode no-token: {status_noauth}")

    log(">>> PART C — frontend copy + render")
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
            goto_project_members(page, token)
            sub = page.locator("app-members-tab .workspace-heading-subtitle, "
                               "app-members-tab .heading-subtitle, "
                               "app-members-tab p:has-text('Agent 池')").first
            sub_text = (sub.text_content() or "").strip()
            for needle in ("全局 Agent 池", "跨项目共享", "按注册时间倒序"):
                if needle not in sub_text:
                    failures.append(f"subtitle missing {needle!r}")
            if page.locator("app-members-tab h4:has-text('全局 Agent 池')").count() == 0:
                failures.append("section title '全局 Agent 池' not found")
            badge = page.locator("app-members-tab .count-badge").first
            badge_text = (badge.text_content() or "").strip()
            if "Agent（全局）" not in badge_text:
                failures.append(f"badge missing 'Agent（全局）'")
        finally:
            ctx.close()
            browser.close()

    if failures:
        log("FAIL:")
        for f in failures:
            log("  -", f)
        return 1
    log("PASS — X.A2 MembersTab data boundary OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
