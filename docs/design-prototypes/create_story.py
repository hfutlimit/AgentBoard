import json, urllib.request, urllib.error

API = "http://localhost:18000"
BASE = r"E:\Projects\WorkBuddy\AgentBoard\docs\design-prototypes"
TOKENS_CSS = r"C:\Users\jason\.workbuddy\teams\design-engine-agentboard-ui\design-tokens.css"

# 1) login
req = urllib.request.Request(API + "/api/auth/login",
    data=json.dumps({"username":"admin","password":"admin123"}).encode(),
    headers={"Content-Type":"application/json"})
token = json.load(urllib.request.urlopen(req))["token"]
H = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept":"application/json"}

description = f"""## 背景
用户反馈各类详情页的「编辑」控件（如 Epic 详情底部的「▸ 编辑 Epic」）为左对齐纯文字 disclosure 按钮，既不像按钮也不像链接，与同页紫色主按钮（如「+ 新建 Story」）形成强烈反差，整体操作按钮层级混乱。

## 目标
基于设计原型专家团产出，统一 AgentBoard 全局操作按钮视觉规范，覆盖 Epic / Story / Task / Document 等详情页。

## 设计决策
- 设计基因：Linear（基底）+ Stripe（四档按钮层级纪律）+ 沿用现有主色 --brand-500:#4F46E5（零新主色）。
- 按钮四档：Primary（紫填充白字，主操作如「+ 新建」「下一状态」）/ Secondary（白底紫字浅紫描边，次操作如「编辑」「完成」）/ Ghost（透明紫字，轻操作如「生成子任务」「文档内联编辑」）/ Danger-ghost（透明红字红描边，危险操作如「删除」）。
- 按钮几何：高 36px、padding 0 14px、圆角 8px、图标-文字 gap 8px、文字 14px/500；Card Footer 操作区右对齐、按钮组 gap 8px。

## 原型图（本地路径，供后续自动化任务实现参考）
- Epic 详情页：{BASE}\\epic-detail.png （HTML 源：epic-detail.html）
- Story 详情页：{BASE}\\story-detail.png （HTML 源：story-detail.html）
- Task 详情页：{BASE}\\task-detail.png （HTML 源：task-detail.html）
- 文档详情页：{BASE}\\doc-detail.png （HTML 源：doc-detail.html）
- 设计令牌（直接粘贴到 :root）：{TOKENS_CSS}

## 具体改动点
1. Epic/Story 详情：底部「▸ 编辑 X」disclosure 文字按钮 → 头部右上标准 Secondary 按钮「编辑 X」。
2. Task 详情：操作区 5 个动作统一为右对齐 Button Group（下一状态 primary / 完成+编辑 secondary / 生成子任务 ghost / 删除 danger-ghost）。
3. 文档详情：右上角弱文本「编辑」链接 → 标准 Ghost 按钮「编辑」。
4. 全站补键盘可达性（icon-btn 增加 :focus-visible）；移动端 <720px 提供导航入口。

## 验收标准
- 所有详情页操作按钮符合四档层级，无 disclosure 文字按钮残留。
- 沿用现有紫/蓝主色，不引入新主色。
- 移动端（<=720px）按钮组竖排、可导航。
- 原型图与最终实现视觉一致。
"""

payload = {
    "title": "统一全局操作按钮视觉规范（修复丑陋的「编辑」控件）",
    "description": description,
}

req = urllib.request.Request(API + "/api/epics/89/stories",
    data=json.dumps(payload).encode(), headers=H, method="POST")
try:
    resp = urllib.request.urlopen(req)
    d = json.load(resp)
    print("CREATED HTTP", resp.status)
    print("id", d.get("id"), "| title", d.get("title"), "| status", d.get("status"),
          "| epic", d.get("epic_id"), "| project", d.get("project_id"))
except urllib.error.HTTPError as e:
    print("ERROR", e.code, e.read().decode()[:500])
