# Design: 命令面板接入 Sprint 搜索（v6.14）

## 现状

- Sprint 模型字段：`id / project_id / title / goal / status / start_date / end_date / created_at`（`agentboard/domains/projects/models.py`，`status ∈ planning|active|completed`）。
- 后端 `service.search_epics`：`title/description ilike %q%` + `id desc` + `limit`；端点 `/api/search/epics`（避开 `/api/epics/{eid}`）。
- 前端 `paletteRunSearch(q)`：≥2 字符触发，五类结果写入 `paletteTaskResults / paletteProjectResults / paletteStoryResults / paletteDocumentResults / paletteEpicResults`；`paletteItems` computed 合并后命令优先、实体结果置后。
- `PaletteCommand.category` 联合类型：`'command' | 'task' | 'project' | 'story' | 'document' | 'epic'`。
- 模板分类标签为三元链：`task→任务 / project→项目 / story→Story / document→文档 / epic→Epic / 兜底→命令`。
- 样式 `.palette-item-cat.cat-*` 五色（蓝/紫/青/橙/绿）。

## 方案

### 后端（增量，零契约破坏）

`agentboard/service.py` 新增（镜像 `search_epics`，字段为 `title/goal`）：

```python
def search_sprints(s: Session, q: str, limit: int = 20):
    """全局 Sprint 关键词搜索（title/goal），供命令面板等场景使用（v6.14）。"""
    like = f"%{q}%"
    qry = s.query(Sprint).filter(or_(Sprint.title.ilike(like), Sprint.goal.ilike(like)))
    qry = qry.order_by(Sprint.id.desc())
    return qry.limit(limit).all()
```

`agentboard/api.py` 新增（紧邻 `/api/search/epics`，路径避开 `/api/projects/{pid}/sprints` 项目级路由；项目级前缀带 `{pid}` 变量段，`/api/search/sprints` 前缀含 `/api/search/` 不会冲突）：

```python
@app.get("/api/search/sprints")
def search_sprints_api(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=50), s=Depends(get_session)):
    rows = service.search_sprints(s, q=q, limit=limit)
    return [service._ser(x) for x in rows]
```

### 前端

1. `api.service.ts`：`searchSprints(params)`，镜像 `searchEpics`（`apiCache` 30s TTL）。
2. `app.ts`：
   - `PaletteCommand.category` 增加 `'sprint'`；
   - 新增 `paletteSprintResults` 信号（open/close/短查询分支同步清空）；
   - `paletteRunSearch` 新增 sprint 分支：`/api/search/sprints` → `{id: sprint-{id}, title: Sprint #{id}：{title}, hint: {projectName} · {statusLabel}, category: 'sprint', run: navigate /sprint/{id}}`（Sprint 序列化含 project_id，可复用 `projectName()`）；
   - `paletteItems` 合并数组追加 `paletteSprintResults()`。
3. `app.html`：分类标签三元链补 `sprint→Sprint`。
4. `app.css`：`.palette-item-cat.cat-sprint` 紫色系（`#7c3aed`，与 .cat-epic 绿区分）。

## 交互流程

```
Ctrl+K → 输入 ≥2 字符 → 200ms 防抖 → /api/search/sprints?q=… → paletteSprintResults
→ paletteItems 合并 → 列表出现 Sprint 分类标签结果 → ↑↓ 选择 / 点击 → 关闭面板 → /sprint/{id}
```

## 数据流与权限

- 端点无项目级过滤（与 `search_epics` 一致：走 `get_session` 共享鉴权，`REQUIRE_AUTH=1` 时需登录；`project_access_middleware` 拦截范围不含只读全局搜索 `/api/search/*`）。

## 风险与回滚

- 路由冲突风险：`/api/search/sprints` 前缀 `/api/search/`，不会被 `/api/projects/{pid}/sprints` 匹配；单测覆盖 200 非 422/404。
- 回滚：删除端点与前端分支即可，无迁移。
