"""
Epic 81 v6.9 — 任务视图后台自动轮询刷新 (E2E)

验证：
1. 登录（注入 admin token）后进入 Story 任务视图（/story/123，含 tracked task 1147）
2. 初始：#autoRefreshBtn 渲染、文案「自动」、未激活（默认关闭，init 脚本清除偏好确保默认态）
3. 开启：点击后 #autoRefreshBtn 加 .active，出现 .auto-refresh-dot（状态点）与 .auto-refresh-count 倒计时（形如「30s」）
4. 倒计时递减：约 3s 后 count 数值应小于初始（证明 1s 心跳在跑）
5. 静默自动同步：开启后等待至多 ~33s，倒计时归零触发 autoRefreshTick → loadRoute(false) →
   对 story tasks API 产生一次新请求（相比开启时刻计数 +1），且全程不弹手动刷新成功 toast（「视图已刷新」），
   同步后按钮 title 含「同步」字样（lastSyncedLabel），证明 lastSyncedAt 已写入
6. 偏好持久化：reload 后按钮仍 .active（localStorage agentboard_auto_refresh='on'）；再次点击关闭 → 取消 .active、倒计时隐藏
7. 全程无 pageerror / console error / .js·.css 404（排除良性 /api ERR_ABORTED 与本地 58124→58125 重写副作用）
"""
import time, urllib.request, json, sys
from playwright.sync_api import sync_playwright

WEB = 'http://127.0.0.1:8080'
API = 'http://127.0.0.1:58125'
STORY_ID = 123  # Story 81.1（含 task 1147）


def login():
    r = urllib.request.Request(API + '/api/auth/login',
                                data=json.dumps({'username': 'admin', 'password': 'admin123'}).encode(),
                                headers={'Content-Type': 'application/json'}, method='POST')
    resp = urllib.request.urlopen(r, timeout=8)
    u = json.loads(resp.read().decode())
    return u['token'], u.get('id')


def _api_route(route):
    url = route.request.url
    # 本地 uvicorn 监听 58125，前端默认注入 58124 → 统一重写到 58125
    if '/api/' in url:
        route.continue_(url=url.replace('58124', '58125'))
    else:
        route.continue_()


def parse_count(text):
    try:
        return int(text.replace('s', '').strip())
    except Exception:
        return -1


def main():
    token, uid = login()
    print('[login] admin id=', uid)

    errors = []
    js_css_fail = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-proxy-server'])
        page = browser.new_page()
        page.route('**://127.0.0.1:58124/**', _api_route)

        # 统计 story tasks API 请求次数，用于证明「静默自动同步」确实发起了网络请求
        sync_calls = {'n': 0}

        def _on_request(req):
            if f'/api/stories/{STORY_ID}/tasks' in req.url:
                sync_calls['n'] += 1
        page.on('request', _on_request)

        init = (
            "localStorage.setItem('agentboard_token','%s');"
            "localStorage.setItem('agentboard_user','admin');"
            "localStorage.setItem('agentboard_story_view','list');"
            "localStorage.setItem('agentboard_story_group','none');"
            # 注：不清除 agentboard_auto_refresh —— 全新 Playwright 上下文 localStorage 本就为空（默认关闭）；
            # 且 add_init_script 在 reload 时也会重跑，若此处清除会破坏「偏好持久化」断言
            % token
        )
        page.add_init_script(init)
        page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
        seen_toasts = []

        def _on_console(m):
            if m.type != 'error':
                return
            text = m.text
            if 'Failed to load resource' in text and ('58124' in text or 'ERR_' in text):
                return
            errors.append('console: ' + text)
        page.on('console', _on_console)
        page.on('requestfailed', lambda r: (
            js_css_fail.append(r.url) if (r.url.endswith('.js') or r.url.endswith('.css')) else None
        ))

        page.goto(WEB + '/story/' + str(STORY_ID), wait_until='domcontentloaded')
        page.wait_for_function("!document.querySelector('.skeleton')", timeout=60000)
        page.wait_for_selector('.entity-item, .kanban-card', timeout=20000)
        # 以 #refreshBtn 作为工具栏已挂载的锚点（所有任务视图均渲染），避免骨架屏清除瞬间工具栏尚未挂载的竞态
        page.wait_for_selector('#refreshBtn', timeout=20000)
        page.wait_for_selector('#autoRefreshBtn', timeout=20000)
        print('[ok] 任务视图与自动刷新按钮渲染；任务列表已加载')

        btn = page.locator('#autoRefreshBtn')
        assert '自动' in btn.inner_text(), '初始文案应为「自动」'
        assert 'active' not in (btn.get_attribute('class') or ''), '默认应未激活（关闭态）'
        print('[init] 自动刷新按钮初始为关闭态，文案「自动」')

        # 记录初始同步请求数（首屏加载已发起一次）
        base_calls = sync_calls['n']
        print('[sync] 首屏加载后 story tasks 请求数 =', base_calls)

        # ---- 开启自动刷新 ----
        btn.click()
        page.wait_for_function(
            "document.querySelector('#autoRefreshBtn') && "
            "document.querySelector('#autoRefreshBtn').classList.contains('active')",
            timeout=5000)
        page.wait_for_selector('#autoRefreshBtn .auto-refresh-dot', timeout=5000)
        page.wait_for_selector('#autoRefreshBtn .auto-refresh-count', timeout=5000)
        c0 = parse_count(page.inner_text('#autoRefreshBtn .auto-refresh-count'))
        print('[on] 已激活；状态点+倒计时渲染；初始 countdown =', c0)
        assert c0 > 0, f'倒计时应为正数，实际: {c0}'

        # ---- 倒计时递减 ----
        page.wait_for_timeout(3200)
        c1 = parse_count(page.inner_text('#autoRefreshBtn .auto-refresh-count'))
        print('[tick] 3s 后 countdown =', c1)
        assert c1 < c0, f'倒计时应递减（{c0} -> {c1}）'
        print('[tick] 倒计时递减正常（1s 心跳运行）')

        # ---- 静默自动同步：等待倒计时归零触发一次 loadRoute(false) ----
        # 倒计时约 30s；最多等待 35s 让一次自动同步完成（请求数 +1）且不弹手动成功 toast
        print('[wait] 等待自动同步触发（倒计时归零，至多 35s）…')
        t0 = time.time()
        synced = False
        while time.time() - t0 < 37:
            # 同步请求数增加 → 证明自动静默刷新已发起
            if sync_calls['n'] > base_calls:
                synced = True
                break
            # 若出现手动刷新成功 toast 即判定失败（自动刷新应静默）
            try:
                if page.locator('#toast .toast').count() > 0:
                    tt = page.inner_text('#toast .toast')
                    if '已刷新' in tt:
                        errors.append('auto-refresh should be silent but showed success toast: ' + tt)
            except Exception:
                pass
            page.wait_for_timeout(500)

        assert synced, f'开启后应在 30s 内触发一次静默自动同步（story tasks 请求数应 > {base_calls}，实际 {sync_calls["n"]}）'
        print('[sync] 自动静默同步已触发（请求数', base_calls, '->', sync_calls['n'], '）')

        # 同步后 title 应含「同步」字样（lastSyncedLabel）
        page.wait_for_timeout(500)
        title = btn.get_attribute('title') or ''
        print('[sync] 按钮 title =', repr(title))
        assert '同步' in title, f'同步后 title 应含「同步」（lastSyncedLabel），实际: {title!r}'

        # 激活态下不应出现手动成功 toast 文本
        assert not any('已刷新' in t for t in seen_toasts), '自动刷新不应弹「视图已刷新」成功 toast'

        # ---- 偏好持久化：reload 后仍激活 ----
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function("!document.querySelector('.skeleton')", timeout=60000)
        page.wait_for_selector('#refreshBtn', timeout=20000)
        page.wait_for_selector('#autoRefreshBtn', timeout=20000)
        reloaded_active = 'active' in (page.locator('#autoRefreshBtn').get_attribute('class') or '')
        print('[persist] reload 后激活态 =', reloaded_active)
        assert reloaded_active, 'auto_refresh 偏好应持久化，reload 后仍为激活态'

        # ---- 关闭自动刷新 ----
        page.locator('#autoRefreshBtn').click()
        page.wait_for_function(
            "document.querySelector('#autoRefreshBtn') && "
            "(!document.querySelector('#autoRefreshBtn').classList.contains('active'))",
            timeout=5000)
        page.wait_for_timeout(500)
        off_active = 'active' in (page.locator('#autoRefreshBtn').get_attribute('class') or '')
        count_hidden = page.locator('#autoRefreshBtn .auto-refresh-count').count() == 0
        print('[off] 已关闭；激活态 =', off_active, '倒计时隐藏 =', count_hidden)
        assert not off_active, '关闭后应取消激活'
        assert count_hidden, '关闭后倒计时徽标应隐藏'

        browser.close()

    print('=== RESULTS ===')
    print('errors:', errors)
    print('js_css_fail:', js_css_fail)
    ok = (not errors) and (not js_css_fail)
    if ok:
        print('PASS: 后台自动轮询刷新（开关/倒计时/静默同步/状态点/持久化）验证通过，无报错')
        sys.exit(0)
    else:
        print('FAIL: 存在报错')
        sys.exit(1)


if __name__ == '__main__':
    main()
