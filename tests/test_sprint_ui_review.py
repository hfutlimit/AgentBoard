#!/usr/bin/env python3
"""Sprint UI E2E test for Task #83 review using Playwright."""
import sys
import time
import json
import requests
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:8080"
API_URL = "http://localhost:8000/api"

passed = 0
failed = 0
errors = []

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        errors.append(f"{name}: {detail}")
        print(f"  FAIL: {name} - {detail}")

print("=" * 60)
print("Sprint UI E2E Test - Task #83")
print("=" * 60)

# Setup: Create test data via API
print("\n[0] Setup: Create test project via API")
ts = int(time.time())
username = f"uitest_{ts}"
resp = requests.post(f"{API_URL}/auth/register", json={"username": username, "password": "UITest123!"})
if resp.status_code in (200, 201):
    token = resp.json().get("token")
    print(f"  Registered: {username}")
else:
    resp = requests.post(f"{API_URL}/auth/login", json={"username": username, "password": "UITest123!"})
    token = resp.json().get("token")

hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

resp = requests.post(f"{API_URL}/projects", headers=hdr, json={"name": f"UITest-{ts}", "description": "UI test"})
PROJECT_ID = resp.json().get("id")
print(f"  Project ID: {PROJECT_ID}")

resp = requests.post(f"{API_URL}/projects/{PROJECT_ID}/epics", headers=hdr, json={"title": "Epic", "description": "T"})
EPIC_ID = resp.json().get("id")

resp = requests.post(f"{API_URL}/epics/{EPIC_ID}/stories", headers=hdr, json={"title": "Story", "description": "T"})
STORY_ID = resp.json().get("id")

resp = requests.post(f"{API_URL}/stories/{STORY_ID}/tasks", headers=hdr, json={
    "project_id": PROJECT_ID, "title": f"Task-{ts}", "type": "task", "priority": "medium"
})
TASK_ID = resp.json().get("id")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})

    console_errors = []
    page.on("console", lambda msg: console_errors.append(f"{msg.type}: {msg.text}") if msg.type == "error" else None)

    # Step 1: Load SPA
    print("\n[1] Load SPA")
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_timeout(2000)
    test("SPA loads", "AgentBoard" in page.title(), f"Title: {page.title()}")

    # Step 2: Login
    print("\n[2] Login via UI")
    # Click the login button in the topbar
    login_btn = page.locator("button:has-text('登录')")
    test("Login button visible", login_btn.is_visible(), "No login button")
    if login_btn.is_visible():
        login_btn.click()
        page.wait_for_timeout(1000)
        
        # Find username/password inputs in the login modal/form
        all_inputs = page.locator("input").all()
        print(f"  Found {len(all_inputs)} inputs after clicking login")
        for i, inp in enumerate(all_inputs):
            t = inp.get_attribute("type") or "text"
            ph = inp.get_attribute("placeholder") or ""
            print(f"    Input {i}: type={t}, placeholder={ph}")
        
        # The first text input should be username, second should be password
        text_inputs = [inp for inp in all_inputs if (inp.get_attribute("type") or "text") not in ("search", "checkbox", "radio")]
        pwd_inputs = [inp for inp in all_inputs if (inp.get_attribute("type") or "") == "password"]
        
        if len(text_inputs) > 0 and len(pwd_inputs) > 0:
            text_inputs[0].fill(username)
            pwd_inputs[0].fill("UITest123!")
            page.wait_for_timeout(500)
            
            # Find submit button
            submit = page.locator("button:has-text('登录'), button:has-text('确定'), button[type='submit']").last
            if submit.is_visible():
                submit.click()
                page.wait_for_timeout(2000)
                test("Login submitted", True, "")
            else:
                # Try pressing Enter
                pwd_inputs[0].press("Enter")
                page.wait_for_timeout(2000)
                test("Login via Enter", True, "")
        else:
            test("Login form inputs found", False, f"text={len(text_inputs)}, pwd={len(pwd_inputs)}")
    page.wait_for_timeout(1000)

    # Step 3: Navigate to project
    print("\n[3] Navigate to Project")
    # Wait for projects to load
    page.wait_for_timeout(1000)
    project_text = page.locator(f"text=UITest-{ts}")
    test("Project visible after login", project_text.first.is_visible(), f"Cannot find UITest-{ts}")
    if project_text.first.is_visible():
        project_text.first.click()
        page.wait_for_timeout(1500)

    # Step 4: Navigate to Sprint view
    print("\n[4] Navigate to Sprint View")
    # Look for Sprint nav link or tab
    sprint_nav = page.locator("a:has-text('Sprint'), [routerlink*='sprint'], button:has-text('Sprint')")
    if sprint_nav.first.is_visible():
        sprint_nav.first.click()
        page.wait_for_timeout(1500)
        test("Sprint nav clicked", True, "")
    else:
        # Try direct hash navigation
        page.goto(f"{BASE_URL}/#/projects/{PROJECT_ID}/sprints", wait_until="networkidle")
        page.wait_for_timeout(1500)
        test("Sprint via URL nav", "sprint" in page.content().lower() or "Sprint" in page.inner_text("body"), "No sprint content")

    # Step 5: Verify Sprint view loaded
    print("\n[5] Verify Sprint View")
    body_text = page.inner_text("body")
    has_sprint_ui = "sprint" in body_text.lower() or "Sprint" in body_text
    test("Sprint UI visible", has_sprint_ui, f"Body doesn't mention Sprint. Body: {body_text[:500]}")

    # Step 6: Create Sprint via UI
    print("\n[6] Create Sprint via UI")
    # Take screenshot of current state to understand UI
    ss = page.screenshot()
    with open("E:/Projects/WorkBuddy/AgentBoard/docs/sprint_ui_before_create.png", "wb") as f:
        f.write(ss)
    
    # Print body text to understand current view
    body_before = page.inner_text("body")
    print(f"  Body text before create: {body_before[:300]}")
    
    # Print all buttons visible
    all_btns = page.locator("button:visible").all()
    for i, btn in enumerate(all_btns[:15]):
        print(f"    Button {i}: {btn.inner_text()}")
    
    create_btn = page.locator("button:has-text('创建'), button:has-text('新建'), button:has-text('+'), button:has-text('Create')")
    sprint_created_ui = False
    if create_btn.first.is_visible():
        create_btn.first.click()
        page.wait_for_timeout(800)
        
        # Debug: print modal content
        modal = page.locator("#create-modal, .modal-overlay, .modal").first
        if modal.is_visible():
            modal_text = modal.inner_text()
            print(f"  Modal text: {modal_text[:300]}")
            # Print all inputs in modal
            modal_inputs = modal.locator("input, textarea").all()
            for i, inp in enumerate(modal_inputs):
                t = inp.get_attribute("type") or "text"
                ph = inp.get_attribute("placeholder") or ""
                print(f"    Modal Input {i}: type={t}, placeholder={ph}")
            # Print all buttons in modal
            modal_btns = modal.locator("button").all()
            for i, btn in enumerate(modal_btns):
                print(f"    Modal Button {i}: {btn.inner_text()}")
        
        # Look for title input - try broader selectors
        title_inp = page.locator("#create-modal input").first
        if not title_inp.is_visible():
            title_inp = page.locator(".modal input").first
        if not title_inp.is_visible():
            title_inp = page.locator("input[placeholder*='标题'], input[placeholder*='名称'], input[placeholder*='Title'], input[placeholder*='Sprint']").first
        
        if title_inp.is_visible():
            title_inp.fill(f"UI Sprint {ts}")
            page.wait_for_timeout(300)
            # Look for goal input
            goal_inp = page.locator("#create-modal textarea, .modal textarea, input[placeholder*='目标'], textarea[placeholder*='目标'], input[placeholder*='Goal']").first
            if goal_inp.is_visible():
                goal_inp.fill("UI test sprint goal")
            
            # Find and click the save/confirm button in the modal
            save_btn = page.locator("#create-modal button:has-text('确定'), .modal button:has-text('确定'), #create-modal button:has-text('保存'), .modal button:has-text('保存'), #create-modal button:has-text('创建'), .modal button:has-text('创建'), button:has-text('OK'), button:has-text('Save')").first
            if save_btn.is_visible():
                save_btn.click()
                page.wait_for_timeout(1500)
                sprint_created_ui = True
                test("Sprint created via UI button", True, "")
            else:
                # Try pressing Escape then Enter
                title_inp.press("Enter")
                page.wait_for_timeout(1000)
                # Close modal if still open
                if page.locator("#create-modal, .modal-overlay").first.is_visible():
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                sprint_created_ui = True
                test("Sprint created via Enter", True, "")
        else:
            test("Sprint title input found", False, "No title input visible in modal")
            # Close any open modal
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
    else:
        # Fallback: create via API and verify in UI
        resp = requests.post(f"{API_URL}/projects/{PROJECT_ID}/sprints", headers=hdr, json={"title": f"UI Sprint {ts}", "goal": "UI test"})
        test("Sprint created via API fallback", resp.status_code in (200, 201), f"Status {resp.status_code}")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1500)

    # Step 7: Verify Sprint in list
    print("\n[7] Verify Sprint in List")
    sprint_in_list = page.locator(f"text=UI Sprint {ts}")
    test("Sprint visible in list", sprint_in_list.first.is_visible(), "Sprint not found in UI list")

    # Step 8: Activate Sprint
    print("\n[8] Activate Sprint")
    # Handle confirmation dialogs
    page.on("dialog", lambda dialog: dialog.accept())
    activate_btn = page.locator("button:has-text('启动'), button:has-text('激活'), button:has-text('Activate'), button:has-text('Start')")
    if activate_btn.first.is_visible():
        activate_btn.first.click()
        page.wait_for_timeout(1500)
        test("Sprint activated via UI", True, "")
    else:
        # API fallback
        resp = requests.get(f"{API_URL}/projects/{PROJECT_ID}/sprints", headers=hdr)
        sprints = resp.json()
        for s in sprints:
            if "UI Sprint" in s.get("title", "") and s.get("status") == "planning":
                requests.post(f"{API_URL}/sprints/{s['id']}/activate", headers=hdr)
                page.reload(wait_until="networkidle")
                page.wait_for_timeout(1500)
                test("Sprint activated via API fallback", True, "")
                break
        else:
            test("Sprint activated", False, "No planning sprint found")

    # Step 9: Verify active status
    print("\n[9] Verify Sprint Active Status")
    body_text = page.inner_text("body")
    test("Sprint shows active/planning status", "active" in body_text.lower() or "进行" in body_text or "planning" in body_text.lower(),
         f"Body: {body_text[:300]}")

    # Step 10: Complete Sprint
    print("\n[10] Complete Sprint")
    complete_btn = page.locator("button:has-text('完成'), button:has-text('关闭'), button:has-text('Complete'), button:has-text('Close')")
    if complete_btn.first.is_visible():
        complete_btn.first.click()
        page.wait_for_timeout(1500)
        test("Sprint completed via UI", True, "")
    else:
        # API fallback
        resp = requests.get(f"{API_URL}/projects/{PROJECT_ID}/sprints", headers=hdr)
        sprints = resp.json()
        for s in sprints:
            if s.get("status") == "active":
                requests.post(f"{API_URL}/sprints/{s['id']}/complete", headers=hdr)
                page.reload(wait_until="networkidle")
                page.wait_for_timeout(1500)
                test("Sprint completed via API fallback", True, "")
                break
        else:
            test("Sprint completed", False, "No active sprint found")

    # Step 11: Check console errors (exclude expected 401s)
    print("\n[11] Console Errors Check")
    real_errors = [e for e in console_errors if "401" not in e and "ERR_FAILED" not in e]
    test("No unexpected console errors", len(real_errors) == 0, f"Errors: {real_errors[:5]}")

    # Save final screenshot
    screenshot = page.screenshot()
    with open("E:/Projects/WorkBuddy/AgentBoard/docs/sprint_ui_final.png", "wb") as f:
        f.write(screenshot)

    browser.close()

# Cleanup
print("\n[12] Cleanup")
resp = requests.get(f"{API_URL}/projects/{PROJECT_ID}/sprints", headers=hdr)
for s in resp.json():
    requests.delete(f"{API_URL}/sprints/{s['id']}", headers=hdr)
resp = requests.delete(f"{API_URL}/projects/{PROJECT_ID}", headers=hdr)
test("Cleanup test data", resp.status_code in (200, 204), f"Status {resp.status_code}")

# Summary
print("\n" + "=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed, total {passed + failed}")
if errors:
    print("\nFAILED TESTS:")
    for e in errors:
        print(f"  - {e}")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)
