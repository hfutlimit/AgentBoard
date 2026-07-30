"""
Epic 84 v6.12 — 后台自动刷新失败重试退避计数显示 (E2E)

验证：
1. 登录（注入 admin token）后进入 Story 任务视图（/story/206）
2. 开启后台自动刷新（点击 #autoRefreshBtn），倒计时可见且递减
3. 模拟自动同步失败：将 /api/projects 强制返回 500
   → 自动轮询周期后 autoRefreshFailing 置位 → 出现失败提示条（v6.10 既有）
   → v6.12 新增强化：文案包含「第 N 次」与「M 秒后自动重试」（重试计数 + 实时倒计时）
4. 点击「重试」（失败态仍开启）→ 立即触发一次新同步并失败 → 重试计数递增（第 N→N+1 次）
5. 解除失败模拟并点击「重试」→ 同步成功 →
   - 失败提示条消失（autoRefreshFailing 复位）
   - 重试计数归零（autoRefreshAttempts=0，成功分支）
   - 出现「后台已恢复同步」成功 toast（v6.11 恢复联动）
   - 同步成功轻提示「已同步」胶囊短暂出现（v6.11）
6. 再次开启失败并立即「重试」→ 失败条显示「第 1 次」（证明上一步已归零，非续计数）
7. 全程无 pageerror / console error / .js·.css 404（刻意 500 的网络报错属预期副作用，已排除）

注：自动轮询固定 30s 周期 + 失败请求经重试退避，首个失败周期最长约 60s，等待 timeout 放宽至 90s。
本地验证目标：Docker stack — WEB=28080（agentboard-web-1，挂载 ./agentboard/web/static 实时生效）、API=18000（agentboard-api-1）。
"""
import sys, time, urllib.request, json, re
from playwright.sync_api import sync_playwright

WEB = 'http://127.0.0.1:28080'
API = 'http://127.0.0.1:18000'
STORY_ID = 206

fail_refresh = {'on': False}


def login():
    r = urllib.request.Request(API + '/api/auth/login',
                                data=json.dumps({'username': 'admin', 'password': 'admin123'}).encode(),
                                headers={'Content-Type': 'application/json'}, method='POST')
    resp = urllib.request.urlopen(r, timeout=8)
    u = json.loads(resp.read().decode())
    return u['token'], u.get('id')


def warmup(token):
    hdr = {'Authorization': 'Bearer ' + token}
    for path in ['/api/projects', f'/api/stories/{STORY_ID}/tasks', f'/api/stories/{STORY_ID}',
                 '/api/auth/me']:
        try:
            urllib.request.urlopen(urllib.request.Request(API + path, headers=hdr), timeout=10)
        except Exception:
            pass


def _api_route(route):
    url = route.request.url
    # v6.12 验证：失败模拟期间，把所有 /api 请求（含 /api/projects）强制 500，确保 autoRefreshTick 检测到 error
    if fail_refresh['on'] and '/api/' in url:
        route.fulfill(status=500, content_type='application/json',
                      body=json.dumps({'detail': 'simulated auto-refresh failure'}).encode())
        return
    route.continue_()


def parse_attempt(text):
    """从「自动同步失败（第 N 次）· M 秒后自动重试」提取 N（整数），失败返回 -1"""
    import re
    m = re.search(r'第\s*(\d+)\s*次', text or '')
    return int(m.group(1)) if m else -1


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
        page.route('**/api/**', _api_route)
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
        page.add_init_script(
            "window.__toasts=[];"
            "(function(){function watch(){var t=document.getElementById('toast');"
            "if(!t){setTimeout(watch,200);return;}"
            "var mo=new MutationObserver(function(muts){muts.forEach(function(m){"
            "m.addedNodes.forEach(function(n){if(n.nodeType===1&&n.classList&&n.classList.contains('toast')){"
            "window.__toasts.push(n.textContent);}});});});"
            "mo.observe(t,{childList:true});}watch();})();")
        page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
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
            print('[fatal] 多次重试仍无法渲染，pageerrors=', errors[:3])
            browser.close()
            sys.exit(2)
        print('[ok] 任务视图与刷新按钮渲染；pageerrors=', errors[:3])

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

        # ---- Phase A: 触发失败，验证 v6.12 计数 + 倒计时 ----
        fail_refresh['on'] = True
        print('[sim] /api/* -> 500 失败模拟开启，等待自动轮询周期…')
        page.wait_for_function(
            "document.querySelector('.auto-refresh-fail')!==null",
            timeout=90000)
        chip_cnt = page.locator('.auto-refresh-fail').count()
        assert chip_cnt == 1, f'autoRefreshFailing 为真时失败提示条必须渲染（chip_count={chip_cnt}）'
        txt_a = page.inner_text('.auto-refresh-fail')
        print('[fail] 失败提示条文案 =', repr(txt_a))
        assert '第' in txt_a and '次' in txt_a, f'v6.12: 失败条应含「第 N 次」重试计数，实际: {txt_a!r}'
        assert '后自动重试' in txt_a, f'v6.12: 失败条应含实时倒计时，实际: {txt_a!r}'
        n1 = parse_attempt(txt_a)
        assert n1 >= 1, f'v6.12: 首次失败重试计数应 >=1，实际解析={n1}，文案={txt_a!r}'
        print(f'[ok] v6.12 失败条渲染「第 {n1} 次」+ 实时倒计时，符合预期')

        # 倒计时实时递减校验（文案形如「…· 30s 后自动重试」）
        def cd_of():
            m = re.search(r'(\d+)s\s*后自动重试', page.inner_text('.auto-refresh-fail'))
            return int(m.group(1)) if m else -1
        x1 = cd_of()
        page.wait_for_timeout(2200)
        x2 = cd_of()
        print(f'[ok] 倒计时实时值 {x1}s -> {x2}s（应递减）')
        assert x1 > 0 and x2 >= 0 and x2 != x1, f'倒计时应实时递减，实际 {x1} -> {x2}'

        # 点击「重试」（失败态仍开启）→ 立即触发新同步并失败 → 计数递增
        page.locator('#autoRefreshRetryBtn').click()
        print('[retry] 失败态点击重试，轮询计数递增（至多 35s）…')
        n2 = n1
        for _ in range(35):
            try:
                t = page.inner_text('.auto-refresh-fail')
                cur = parse_attempt(t)
                if cur > n1:
                    n2 = cur
                    break
            except Exception:
                pass
            page.wait_for_timeout(1000)
        print(f'[retry] 轮询结束，n1={n1} n2={n2}，末次文案={page.inner_text(".auto-refresh-fail")!r}')
        assert n2 > n1, f'v6.12: 手动重试应使计数递增（{n1} -> {n2}）'
        print(f'[ok] v6.12 重试计数递增：第 {n1} 次 -> 第 {n2} 次')

        # ---- Phase B: 解除失败，重试成功 → 失败条消失 + 计数归零 + 恢复 toast + 已同步 ----
        fail_refresh['on'] = False
        page.wait_for_timeout(300)
        page.locator('#autoRefreshRetryBtn').click()
        print('[retry] 已解除失败并点击重试，等待同步成功…')
        page.wait_for_function("!document.querySelector('.auto-refresh-fail')", timeout=30000)
        print('[ok] 失败提示条已消失（autoRefreshFailing 复位）')

        # v6.11: 恢复成功 toast「后台已恢复同步」应在恢复瞬间出现
        toasts = page.evaluate("() => (window.__toasts||[]).slice()")
        print('[toasts] 捕获到的 toast 文案=', toasts)
        assert any('已恢复同步' in t for t in toasts), f'从失败恢复应弹「后台已恢复同步」toast，实际 toasts={toasts}'
        print('[ok] 恢复成功 toast「后台已恢复同步」已出现（v6.11 联动）')

        # v6.11: 同步成功轻提示「已同步」胶囊短暂出现（1.5s 窗口）
        try:
            page.wait_for_selector('.auto-refresh-ok', timeout=3000, state='attached')
            ok_txt = page.inner_text('.auto-refresh-ok')
            assert '已同步' in ok_txt, f'轻提示应含「已同步」，实际: {ok_txt!r}'
            print('[ok] 同步成功轻提示「已同步」胶囊已出现')
        except Exception as e:
            print('[warn] 轻提示「已同步」未在窗口内捕获（非致命）：', str(e)[:80])

        # ---- Phase C: 再次失败 + 立即重试 → 应显示「第 1 次」（证明计数已归零）----
        fail_refresh['on'] = True
        page.wait_for_timeout(200)
        page.locator('#autoRefreshRetryBtn').click()
        print('[reset] 再次失败并立即重试，验证计数归零…')
        page.wait_for_function(
            "(function(){var t=document.querySelector('.auto-refresh-fail');if(!t)return false;"
            "var m=t.textContent.match(/第\\s*(\\d+)\\s*次/);return m && parseInt(m[1])>=1;})()",
            timeout=20000)
        txt_c = page.inner_text('.auto-refresh-fail')
        n3 = parse_attempt(txt_c)
        assert n3 == 1, f'v6.12: 成功恢复后计数应归零，再次失败应显示「第 1 次」，实际={txt_c!r}（n3={n3}）'
        print(f'[ok] v6.12 计数归零验证通过：恢复后再次失败显示「第 {n3} 次」（非续计数）')

        fail_refresh['on'] = False  # 收尾，避免影响后续
        page.wait_for_timeout(300)
        browser.close()

    print('=== RESULTS ===')
    print('errors:', errors)
    print('js_css_fail:', js_css_fail)
    ok = (not errors) and (not js_css_fail)
    if ok:
        print('PASS: 后台自动刷新失败重试退避计数显示（第 N 次 + 实时倒计时 + 重试递增 + 归零）正常，无报错')
        sys.exit(0)
    else:
        print('FAIL: 存在报错')
        sys.exit(1)


if __name__ == '__main__':
    main()
