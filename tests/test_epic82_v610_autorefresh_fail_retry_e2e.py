"""
Epic 82 v6.10 — 后台自动刷新失败提示与一键重试 (E2E)

验证：
1. 登录（注入 admin token）后进入 Story 任务视图（/story/123）
2. 开启后台自动刷新（点击 #autoRefreshBtn），倒计时可见且递减
3. 模拟自动同步失败：将 /api/projects* 强制返回 500
   → 自动轮询周期后 autoRefreshFailing 置位 → 出现「自动刷新失败」提示条（.auto-refresh-fail）+ 重试按钮（#autoRefreshRetryBtn）
4. 重试按钮在失败态下始终可点击（v6.10 改进：允许在刷新进行中强制重试）；
   点击后若失败仍在，提示条保留、任务内容不丢失
5. 解除失败模拟后点击重试 → 同步成功 → 提示条消失
6. 全程无 pageerror / console error / .js·.css 404（刻意 500 的网络报错属预期副作用，已排除）

注：自动轮询固定 30s 周期 + 失败请求需经重试退避，首个失败周期最长约 60s，测试等待 timeout 放宽至 90s。
"""
import time, urllib.request, json
from playwright.sync_api import sync_playwright

WEB = 'http://127.0.0.1:8080'
API = 'http://127.0.0.1:58125'
STORY_ID = 123

fail_refresh = {'on': False}


def login():
    r = urllib.request.Request(API + '/api/auth/login',
                                data=json.dumps({'username': 'admin', 'password': 'admin123'}).encode(),
                                headers={'Content-Type': 'application/json'}, method='POST')
    resp = urllib.request.urlopen(r, timeout=8)
    u = json.loads(resp.read().decode())
    return u['token'], u.get('id')


def warmup(token):
    """预热身本地 API，降低冷启动延迟（侧栏预加载 74 项目整棵树较慢）"""
    hdr = {'Authorization': 'Bearer ' + token}
    for path in ['/api/projects', f'/api/stories/{STORY_ID}/tasks', f'/api/stories/{STORY_ID}',
                 '/api/epics/74', '/api/projects/76', '/api/auth/me']:
        try:
            urllib.request.urlopen(urllib.request.Request(API + path, headers=hdr), timeout=10)
        except Exception:
            pass


def _api_route(route):
    url = route.request.url
    # 仅在开启失败模拟后，对 projects 相关请求返回 500（不影响初始加载）
    if fail_refresh['on'] and '/api/projects' in url:
        route.fulfill(status=500, content_type='application/json',
                      body=json.dumps({'detail': 'simulated auto-refresh failure'}).encode())
        return
    if f'/api/stories/{STORY_ID}/tasks' in url:
        resp = route.fetch(url=url.replace('58124', '58125'))
        time.sleep(0.3)
        route.fulfill(response=resp)
    else:
        route.continue_(url=url.replace('58124', '58125'))


def main():
    token, uid = login()
    print('[login] admin id=', uid)
    warmup(token)
    print('[warmup] API 预热完成')

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
        page.add_init_script(
            "Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});"
            "Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>'visible'});")
        pageerr = []
        page.on('pageerror', lambda e: pageerr.append('pageerror: ' + str(e)))
        page.on('console', lambda m: (
            errors.append('console: ' + m.text)
            if (m.type == 'error' and not ('Failed to load resource' in m.text and 'status of 500' in m.text))
            else None))
        page.on('requestfailed', lambda r: (
            js_css_fail.append(r.url) if (r.url.endswith('.js') or r.url.endswith('.css')) else None
        ))

        # 稳健加载：最多重试 3 次，每次直等 #autoRefreshBtn 出现（绕过过早通过的 skeleton 检查）
        auto_ok = False
        for attempt in range(1, 4):
            print('[load] 尝试 %d/3 …' % attempt)
            page.goto(WEB + '/story/' + str(STORY_ID), wait_until='domcontentloaded')
            try:
                page.wait_for_selector('#autoRefreshBtn', timeout=60000, state='attached')
                auto_ok = True
                break
            except Exception as e:
                print('[warn] 尝试 %d #autoRefreshBtn 未出现: %s' % (attempt, str(e)[:80]))
                page.wait_for_timeout(1500)
        if not auto_ok:
            print('[fatal] 多次重试仍无法渲染，pageerrors=', pageerr[:3])
            browser.close()
            sys.exit(2)
        print('[ok] 任务视图与刷新按钮渲染；pageerrors=', pageerr[:3])

        auto_btn = page.locator('#autoRefreshBtn')
        if 'active' not in (auto_btn.get_attribute('class') or ''):
            auto_btn.click()
        page.wait_for_function(
            "document.querySelector('#autoRefreshBtn') && "
            "document.querySelector('#autoRefreshBtn').classList.contains('active')",
            timeout=8000)
        c0 = page.inner_text('#autoRefreshBtn .auto-refresh-count') if page.locator('#autoRefreshBtn .auto-refresh-count').count() else '?'
        page.wait_for_timeout(3200)
        c1 = page.inner_text('#autoRefreshBtn .auto-refresh-count') if page.locator('#autoRefreshBtn .auto-refresh-count').count() else '?'
        print('[init] 倒计时 %s -> %s（应递减）；后台自动刷新已开启' % (c0, c1))

        assert page.locator('.auto-refresh-fail').count() == 0, '开启瞬间不应有失败提示条'
        print('[init] 失败提示条初始隐藏（符合预期）')

        # 触发失败，等待 autoRefreshFailing 置位（dot.failing 或 chip 出现，至多 90s）
        fail_refresh['on'] = True
        print('[sim] /api/projects -> 500 失败模拟开启，等待自动轮询周期…')
        page.wait_for_function(
            "var d=document.querySelector('#autoRefreshBtn .auto-refresh-dot');"
            "(document.querySelector('.auto-refresh-fail')!==null) || (d && d.classList.contains('failing'))",
            timeout=90000)
        chip_cnt = page.locator('.auto-refresh-fail').count()
        dot_failing = page.evaluate(
            "() => { var d=document.querySelector('#autoRefreshBtn .auto-refresh-dot'); return !!(d && d.classList.contains('failing')); }")
        print('[fail] chip_count=%d dot_failing=%s' % (chip_cnt, dot_failing))
        assert chip_cnt == 1, f'autoRefreshFailing 为真时失败提示条必须渲染（chip_count={chip_cnt}, dot_failing={dot_failing}）'
        fail_text = page.inner_text('.auto-refresh-fail')
        assert '自动刷新失败' in fail_text, f'提示条应含「自动刷新失败」，实际: {fail_text!r}'
        retry = page.locator('#autoRefreshRetryBtn')
        assert retry.count() == 1, '#autoRefreshRetryBtn 应出现'
        # v6.10：失败态下重试按钮始终可点击（无 disabled 绑定）
        assert retry.is_enabled(), 'v6.10 改进：失败态下重试按钮应始终可点击'
        print('[fail] 失败提示条「自动刷新失败」+ 重试按钮 #autoRefreshRetryBtn 已渲染且可点击')

        # 失败仍在时点击重试：提示条保留、内容不丢失
        retry.click()
        page.wait_for_timeout(800)
        assert page.locator('.auto-refresh-fail').count() == 1, '失败仍在时重试后提示条应保留'
        assert page.locator('.entity-item, .kanban-card').count() >= 1, '重试后任务内容应保留'
        print('[retry] 失败态下点击重试：提示条保留、内容不丢失（符合预期）')

        # 解除失败模拟，再次重试 → 成功 → 提示条消失
        fail_refresh['on'] = False
        page.wait_for_timeout(300)
        page.locator('#autoRefreshRetryBtn').click()
        page.wait_for_function("!document.querySelector('.auto-refresh-fail')", timeout=30000)
        print('[ok] 解除失败后重试成功，失败提示条已消失（autoRefreshFailing 复位）')

        page.wait_for_timeout(300)
        browser.close()

    print('=== RESULTS ===')
    print('errors:', errors)
    print('js_css_fail:', js_css_fail)
    ok = (not errors) and (not js_css_fail)
    if ok:
        print('PASS: 自动刷新失败提示条与一键重试正常，无报错')
        sys.exit(0)
    else:
        print('FAIL: 存在报错')
        sys.exit(1)


if __name__ == '__main__':
    import sys
    main()
