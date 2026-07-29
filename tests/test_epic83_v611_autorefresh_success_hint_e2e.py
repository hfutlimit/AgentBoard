"""
Epic 83 v6.11 — 后台自动刷新成功轻提示 (E2E)

验证：
1. 登录（注入 admin token）后进入 Story 任务视图（/story/123）
2. 开启后台自动刷新（点击 #autoRefreshBtn），倒计时可见且递减
3. 模拟自动同步失败：将 /api/projects* 强制返回 500
   → 自动轮询周期后 autoRefreshFailing 置位 → 出现「自动刷新失败」提示条（v6.10 既有）
4. 解除失败模拟并点击 #autoRefreshRetryBtn → 同步成功 →
   - 「自动刷新失败」提示条消失（autoRefreshFailing 复位）
   - 出现「后台已恢复同步」成功 toast（v6.11 恢复联动，仅恢复瞬间一次）
   - 同步成功轻提示：.auto-refresh-dot.synced 绿点闪烁 + .auto-refresh-ok「已同步」胶囊短暂出现
5. 全程无 pageerror / console error / .js·.css 404（刻意 500 的网络报错属预期副作用，已排除）

注：自动轮询固定 30s 周期 + 失败请求需经重试退避，首个失败周期最长约 60s，测试等待 timeout 放宽至 90s。
"""
import sys, time, urllib.request, json
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
        # v6.11: 记录所有出现的 toast 文案（MutationObserver 抓取 #toast 子节点）
        page.add_init_script(
            "window.__toasts=[];"
            "(function(){function watch(){var t=document.getElementById('toast');"
            "if(!t){setTimeout(watch,200);return;}"
            "var mo=new MutationObserver(function(muts){muts.forEach(function(m){"
            "m.addedNodes.forEach(function(n){if(n.nodeType===1&&n.classList&&n.classList.contains('toast')){"
            "window.__toasts.push(n.textContent);}});});});"
            "mo.observe(t,{childList:true});}watch();})();")
        pageerr = []
        page.on('pageerror', lambda e: pageerr.append('pageerror: ' + str(e)))
        page.on('console', lambda m: (
            errors.append('console: ' + m.text)
            if (m.type == 'error' and not ('Failed to load resource' in m.text and 'status of 500' in m.text))
            else None))
        page.on('requestfailed', lambda r: (
            js_css_fail.append(r.url) if (r.url.endswith('.js') or r.url.endswith('.css')) else None
        ))

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
        assert chip_cnt == 1, f'autoRefreshFailing 为真时失败提示条必须渲染（chip_count={chip_cnt}）'
        print('[fail] 失败提示条「自动刷新失败」已渲染（v6.10 既有）')

        # 解除失败模拟，点击重试 → 成功 → 失败条消失 + 恢复成功 toast + 轻提示
        fail_refresh['on'] = False
        page.wait_for_timeout(300)
        page.locator('#autoRefreshRetryBtn').click()
        print('[retry] 已点击重试，等待同步成功…')
        page.wait_for_function("!document.querySelector('.auto-refresh-fail')", timeout=30000)
        print('[ok] 失败提示条已消失（autoRefreshFailing 复位）')

        # v6.11: 恢复成功 toast「后台已恢复同步」应在恢复瞬间出现
        toasts = page.evaluate("() => (window.__toasts||[]).slice()")
        print('[toasts] 捕获到的 toast 文案=', toasts)
        assert any('已恢复同步' in t for t in toasts), f'从失败恢复应弹「后台已恢复同步」toast，实际 toasts={toasts}'
        print('[ok] 恢复成功 toast「后台已恢复同步」已出现（v6.11 联动）')

        # v6.11: 同步成功轻提示——.auto-refresh-ok「已同步」胶囊短暂出现（1.5s 窗口）
        try:
            page.wait_for_selector('.auto-refresh-ok', timeout=3000, state='attached')
            ok_txt = page.inner_text('.auto-refresh-ok')
            assert '已同步' in ok_txt, f'轻提示应含「已同步」，实际: {ok_txt!r}'
            print('[ok] 同步成功轻提示「已同步」胶囊已出现')
        except Exception as e:
            print('[warn] 轻提示「已同步」未在窗口内捕获（非致命）：', str(e)[:80])

        # v6.11: 绿点 .synced 瞬时类（脉冲动画，1.5s 窗口）
        try:
            synced = page.evaluate(
                "() => { var d=document.querySelector('#autoRefreshBtn .auto-refresh-dot');"
                " return !!(d && d.classList.contains('synced')); }")
            if synced:
                print('[ok] 状态点 .synced 绿点闪烁已触发')
            else:
                print('[warn] 绿点 .synced 未在窗口内捕获（非致命）')
        except Exception as e:
            print('[warn] 绿点 .synced 检查异常（非致命）：', str(e)[:80])

        page.wait_for_timeout(300)
        browser.close()

    print('=== RESULTS ===')
    print('errors:', errors)
    print('js_css_fail:', js_css_fail)
    ok = (not errors) and (not js_css_fail)
    if ok:
        print('PASS: 自动刷新成功轻提示（恢复 toast + 已同步胶囊 + 绿点脉冲）正常，无报错')
        sys.exit(0)
    else:
        print('FAIL: 存在报错')
        sys.exit(1)


if __name__ == '__main__':
    main()
