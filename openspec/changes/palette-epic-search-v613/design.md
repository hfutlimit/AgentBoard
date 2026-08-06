# Design: 命令面板接入 Epic 后端搜索（v6.13）

## 现状

- 后端 `service.search_stories`：`title/description ilike %q%` + `id desc` + `limit`；端点 `/api/search/stories`（路径避开 `/api/stories/{sid}` int 路由冲突）。
- 前端 `paletteRunSearch(q)`：≥2 字符触发防抖搜索，四类结果分别写入 `paletteTaskResults / paletteProjectResults / paletteStoryResults / paletteDocumentResults`；`paletteItems` computed 合并后命令优先、实体结果置后。
- `PaletteCommand.category` 联合类型：`'command' | 'task' | 'project' | 'story' | 'document'`。
- 模板分类标签为三元链：`task→任务 / project→项目 / story→Story / document→文档 / 兜底→命令`。
- 样式 `.palette-item-cat.cat-*` 四色（蓝/紫/青/橙）。

## 方案

### 后端（增量，零契约破坏）

`agentboard/service.py` 新增（镜像 `search_stories`）：

```python
def search_epics(s: Session, q: str, limit: int = 20):
    like = f"%{q}%"
    qry = s.query(Epic).filter(or_(Epic.title.ilike(like), Epic.description.ilike(like)))
    qry = qry.order_by(Epic.id.desc())
    return qry.limit(limit).all()
```

`agentboard/api.py` 新增（紧邻 `/api/search/stories`，路径同样避开 `/api/epics/{eid}`）：

```python
@app.get("/api/search/epics")
def search_epics_api(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=50), s=Depends(get_session)):
    rows = service.search_epics(s, q=q, limit=limit)
    return [service._ser(x) for x in rows]
```

### 前端

1. `api.service.ts`：`searchEpics(params)`，镜像 `searchStories`（`apiCache` 30s TTL）。
2. `app.ts`：
   - `PaletteCommand.category` 增加 `'epic'`；
   - 新增 `paletteEpicResults` 信号（open/close/短查询分支同步清空）；
   - `paletteRunSearch` 新增 epic 分支：`/api/search/epics` → `{id: epic-{id}, title: Epic #{id}：{title}, hint: {projectName} · {status}, category: 'epic', run: navigate /epic/{id}}`；
   - `paletteItems` 合并数组追加 `paletteEpicResults()`。
3. `app.html`：分类标签三元链补 `epic→Epic`。
4. `styles.css`：`.palette-item-cat.cat-epic` 绿色系（`#059669`）。

## 交互流程

```
Ctrl+K → 输入 ≥2 字符 → 200ms 防抖 → /api/search/epics?q=… → paletteEpicResults
→ paletteItems 合并 → 列表出现 Epic 分类标签结果 → ↑↓ 选择 / 点击 → 关闭面板 → /epic/{id}
```

## 数据流与权限

- 端点无项目级过滤，返回全部可见性下的 Epic（与 `search_stories` 一致：走 `get_session` 共享鉴权中间件，`REQUIRE_AUTH=1` 时需登录；项目级 `project_access_middleware` 拦截范围不含只读全局搜索，与既有 `/api/search/*` 行为一致）。

## 风险与回滚

- 路由冲突风险：`/api/search/epics` 前缀含 `/api/search/`，不会被 `/api/epics/{eid}`（`/api/epics/` 前缀）匹配；单测覆盖 200 非 422。
- 回滚：删除端点与前端分支即可，无迁移。
