"""
Epic 79 v6.7 — 手动刷新成功后显示成功 toast 提示 (E2E)

验证：
1. 登录（注入 admin token）后进入 Story 任务视图（/story/121，对应本次 tracked task 1145）
2. 点击 #refreshBtn 手动刷新（拦截 story tasks API 增加延迟使「刷新中」加载态可观测）
3. 刷新完成后出现成功 toast（文本含「视图已刷新」，class 含 success）
4. 无 pageerror / console error / .js·.css 404（排除良性 /api ERR_ABORTED）
"""
import time, urllib.request, json, sys
from playwright.sync_api import sync_playwright

WEB = 'http://127.0.0.1:8080'
API = 'http://127.0.0.1:58125'
STORY_ID = 121  # Story 79.1（含 task 1145）


def login():
    r = urllib.request.Request(API + '/api/auth/login',
                                data=json.dumps({'username': 'admin', 'password': 'admin123'}).encode(),
                                headers={'Content-Type': 'application/json'}, method='POST')
    resp = urllib.request.urlopen(r, timeout=8)
    u = json.loads(resp.read().decode())
    return u['token'], u.get('id')


def _api_route(route):
    """拦截 story tasks API 增加延迟使「刷新中」可观测；其余请求重定向 58124->58125"""
    url = route.request.url
    if f'/api/stories/{STORY_ID}/tasks' in url:
        resp = route.fetch(url=url.replace('58124', '58125'))
        time.sleep(0.9)
        route.fulfill(response=resp)
    else:
        route.continue_(url=url.replace('58124', '58125'))


def main():
    token, uid = login()
    print('[login] admin id=', uid)

    errors = []
    js_css_fail = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-proxy-server'])
        page = browser.new_page()
        page.route('**://127.0.0.1:58124/**', _api_route)
        init = (
            "localStorage.setItem('agentboard_token','%s');"
            "localStorage.setItem('agentboard_user','admin');"
            "localStorage.setItem('agentboard_story_view','list');"
            "localStorage.setItem('agentboard_story_group','none');" % token
        )
        page.add_init_script(init)
        page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
        page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' else None)
        page.on('requestfailed', lambda r: (
            js_css_fail.append(r.url) if (r.url.endswith('.js') or r.url.endswith('.css')) else None
        ))

        page.goto(WEB + '/story/' + str(STORY_ID), wait_until='domcontentloaded')
        page.wait_for_selector('#refreshBtn', timeout=20000)
        page.wait_for_selector('.entity-item, .kanban-card', timeout=20000)
        print('[ok] 任务视图与刷新按钮渲染；任务列表已加载')

        # 初始态
        btn = page.locator('#refreshBtn')
        assert btn.is_enabled(), '刷新按钮初始应可点击'
        assert '刷新' in btn.inner_text(), '初始文案应为「刷新」'
        print('[init] 刷新按钮可点击，文案「刷新」')

        # 点击刷新
        btn.click()
        # 刷新中加载态（API 被延迟 ~900ms）
        page.wait_for_function(
            "document.querySelector('#refreshBtn') && "
            "document.querySelector('#refreshBtn').disabled && "
            "document.querySelector('#refreshBtn .refresh-spinner')",
            timeout=5000)
        print('[refresh] 刷新中加载态已观测（禁用 + spinner）')

        # 等待成功 toast
        page.wait_for_selector('#toast .toast', timeout=10000)
        toast_text = page.inner_text('#toast .toast')
        toast_cls = page.get_attribute('#toast .toast', 'class') or ''
        print('[toast] text=', repr(toast_text), 'class=', repr(toast_cls))
        assert '已刷新' in toast_text, f'预期成功 toast，实际: {toast_text!r}'
        assert 'success' in toast_cls, f'toast 应含 success class，实际: {toast_cls!r}'
        print('[toast] 成功 toast「视图已刷新」已显示')

        # 刷新按钮恢复
        page.wait_for_function(
            "document.querySelector('#refreshBtn') && !document.querySelector('#refreshBtn').disabled",
            timeout=8000)
        print('[refresh] 按钮已恢复（刷新结束）')
        page.wait_for_timeout(600)

        browser.close()

    print('=== RESULTS ===')
    print('errors:', errors)
    print('js_css_fail:', js_css_fail)
    ok = (not errors) and (not js_css_fail)
    if ok:
        print('PASS: 手动刷新成功 toast 正常显示，无报错')
        sys.exit(0)
    else:
        print('FAIL: 存在报错')
        sys.exit(1)


if __name__ == '__main__':
    main()
