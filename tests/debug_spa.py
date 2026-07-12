#!/usr/bin/env python3
"""Debug script: take screenshot of SPA to understand current state."""
import sys
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:8080"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    
    console_msgs = []
    page.on("console", lambda msg: console_msgs.append(f"{msg.type}: {msg.text}"))
    
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_timeout(3000)
    
    # Save screenshot
    screenshot = page.screenshot()
    with open("E:/Projects/WorkBuddy/AgentBoard/docs/debug_spa_state.png", "wb") as f:
        f.write(screenshot)
    
    # Get page content
    content = page.content()
    print("Page title:", page.title())
    print("\n--- Body text (first 2000 chars) ---")
    print(page.inner_text("body")[:2000])
    
    print("\n--- All input elements ---")
    inputs = page.locator("input").all()
    for i, inp in enumerate(inputs):
        print(f"  Input {i}: type={inp.get_attribute('type')}, placeholder={inp.get_attribute('placeholder')}, name={inp.get_attribute('name')}")
    
    print("\n--- All buttons ---")
    buttons = page.locator("button").all()
    for i, btn in enumerate(buttons):
        print(f"  Button {i}: text={btn.inner_text()}")
    
    print("\n--- All links ---")
    links = page.locator("a").all()
    for i, link in enumerate(links[:20]):
        print(f"  Link {i}: text={link.inner_text()}, href={link.get_attribute('href')}")
    
    print("\n--- Console messages ---")
    for msg in console_msgs[:20]:
        print(f"  {msg}")
    
    browser.close()
