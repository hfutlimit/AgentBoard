"""
Epic 80 v6.8 — 手动刷新失败时显示错误 toast 提示 (E2E)

验证：
1. 登录（注入 admin token）后进入 Story 任务视图（/story/122，含 tracked task 1146）
2. 成功路径：点击 #refreshBtn（拦截 story tasks API 增加延迟使「刷新中」加载态可观测）
   → 出现成功 toast（文本含「视图已刷新」，class 含 success），按钮恢复可点击
3. 失败路径：将 /api/projects（loadRoute 首个调用）强制返回 500 模拟服务端异常
   → 点击刷新后出现错误 toast（文本含「刷新失败」，class 含 error），且不渲染「加载失败」横幅，
     按钮保持可用（可重试）；当前任务内容保留不丢失
4. 全程无 pageerror / console error / .js·.css 404（排除良性 /api ERR_ABORTED）
"""
import time, urllib.request, json, sys
from playwright.sync_api import sync_playwright

WEB = 'http://127.0.0.1:8080'
API = 'http://127.0.0.1:58125'
STORY_ID = 122  # Story 80.1（含 task 1146）

fail_refresh = {'on': False}


def login():
    r = urllib.request.Request(API + '/api/auth/login',
                                data=json.dumps({'username': 'admin', 'password': 'admin123'}).encode(),
                                headers={'Content-Type': 'application/json'}, method='POST')
    resp = urllib.request.urlopen(r, timeout=8)
    u = json.loads(resp.read().decode())
    return u['token'], u.get('id')


def _api_route(route):
    """成功路径：拦截 story tasks API 增加延迟使「刷新中」可观测；
       失败路径：/api/projects 返回 500 模拟刷新异常。其余请求重定向 58124->58125。"""
    url = route.request.url
    if fail_refresh['on'] and '/api/projects' in url:
        route.fulfill(status=500, content_type='application/json',
                      body=json.dumps({'detail': 'simulated refresh failure'}).encode())
        return
    if f'/api/stories/{STORY_ID}/tasks' in url:
        resp = route.fetch(url=url.replace('58124', '58125'))
        time.sleep(0.7)
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
        def _on_console(m):
            if m.type != 'error':
                return
            text = m.text
            # 良性：本测试刻意将 /api/projects 强制返回 500 以验证失败 toast，
            # 浏览器对此类 HTTP 失败的网络请求的 console 报错属预期副作用，非应用缺陷
            if 'Failed to load resource' in text and 'status of 500' in text:
                return
            errors.append('console: ' + text)
        page.on('console', _on_console)
        page.on('requestfailed', lambda r: (
            js_css_fail.append(r.url) if (r.url.endswith('.js') or r.url.endswith('.css')) else None
        ))

        page.goto(WEB + '/story/' + str(STORY_ID), wait_until='domcontentloaded')
        # 等待首屏初始加载完成（骨架屏消失）再定位刷新按钮：侧栏预加载全部项目树，数据集较大时首屏渲染可能接近 20s
        page.wait_for_function("!document.querySelector('.skeleton')", timeout=60000)
        page.wait_for_selector('#refreshBtn', timeout=15000)
        page.wait_for_selector('.entity-item, .kanban-card', timeout=20000)
        print('[ok] 任务视图与刷新按钮渲染；任务列表已加载')

        btn = page.locator('#refreshBtn')
        assert btn.is_enabled(), '刷新按钮初始应可点击'
        assert '刷新' in btn.inner_text(), '初始文案应为「刷新」'
        print('[init] 刷新按钮可点击，文案「刷新」')

        # ---- 成功路径 ----
        btn.click()
        page.wait_for_function(
            "document.querySelector('#refreshBtn') && "
            "document.querySelector('#refreshBtn').disabled && "
            "document.querySelector('#refreshBtn .refresh-spinner')",
            timeout=5000)
        print('[refresh] 刷新中加载态已观测（禁用 + spinner）')

        page.wait_for_selector('#toast .toast', timeout=10000)
        toast_text = page.inner_text('#toast .toast')
        toast_cls = page.get_attribute('#toast .toast', 'class') or ''
        print('[toast] success text=', repr(toast_text), 'class=', repr(toast_cls))
        assert '已刷新' in toast_text, f'预期成功 toast，实际: {toast_text!r}'
        assert 'success' in toast_cls, f'toast 应含 success class，实际: {toast_cls!r}'
        print('[toast] 成功 toast「视图已刷新」已显示')

        page.wait_for_function(
            "document.querySelector('#refreshBtn') && !document.querySelector('#refreshBtn').disabled",
            timeout=8000)
        print('[refresh] 按钮已恢复（刷新结束）')
        page.wait_for_timeout(400)

        # ---- 失败路径 ----
        fail_refresh['on'] = True
        btn.click()
        # 失败后按钮应立即恢复可用（refreshing 在 finally 复位），并出现错误 toast
        page.wait_for_function(
            "document.querySelector('#refreshBtn') && !document.querySelector('#refreshBtn').disabled",
            timeout=8000)
        print('[refresh] 失败后按钮已恢复可用（可重试）')

        page.wait_for_selector('#toast .toast', timeout=10000)
        err_text = page.inner_text('#toast .toast')
        err_cls = page.get_attribute('#toast .toast', 'class') or ''
        print('[toast] error text=', repr(err_text), 'class=', repr(err_cls))
        assert '刷新失败' in err_text, f'预期错误 toast，实际: {err_text!r}'
        assert 'error' in err_cls, f'toast 应含 error class，实际: {err_cls!r}'
        print('[toast] 错误 toast「刷新失败」已显示')

        # 当前内容应保留（任务卡片仍在），不出现「加载失败」横幅
        page.wait_for_timeout(300)
        assert page.locator('.entity-item, .kanban-card').count() >= 1, '失败后任务内容应保留'
        err_banner = page.locator('.card.error-state').count()
        assert err_banner == 0, '手动刷新失败不应渲染「加载失败」横幅'
        print('[content] 失败后任务内容保留，无「加载失败」横幅')

        # 关闭失败模拟，避免影响后续
        fail_refresh['on'] = False
        page.wait_for_timeout(300)

        browser.close()

    print('=== RESULTS ===')
    print('errors:', errors)
    print('js_css_fail:', js_css_fail)
    ok = (not errors) and (not js_css_fail)
    if ok:
        print('PASS: 手动刷新失败 toast 正常显示，成功路径无异常，无报错')
        sys.exit(0)
    else:
        print('FAIL: 存在报错')
        sys.exit(1)


if __name__ == '__main__':
    main()
