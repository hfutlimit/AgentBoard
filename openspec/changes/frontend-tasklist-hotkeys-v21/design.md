# 设计：任务列表键盘快捷键增强

## 现有结构（已存在，复用）
- `app.ts` `handleTaskKeydown(event)` 绑定在任务列表容器 `(keydown)` 上，内部先判断 `event.target` 是否为 INPUT/TEXTAREA/SELECT，若是则直接 `return`（不拦截输入框按键）。
- `app.html` 任务搜索框：`<input class="task-search-input" type="search" [value]="taskSearchQuery()" (input)="taskSearchQuery.set(...)">`，外层 `<div class="task-search-bar">`。
- 既有快捷键：`j`/`ArrowDown` 下移焦点、`k`/`ArrowUp` 上移、`Enter` 打开、` ` 多选、`Escape` 清空选择/关闭面板、`Ctrl/Cmd+A` 全选。

## 方案
### 1. 聚焦搜索（`app.ts`）
在 `handleTaskKeydown` 的 `switch` 中新增：
```ts
case '/': {
  event.preventDefault();
  const searchEl = document.querySelector<HTMLInputElement>('.task-search-input');
  if (searchEl) { searchEl.focus(); searchEl.select(); }
  break;
}
```
由于输入框内按键已被 guard 提前 `return`，`/` 仅在列表区（entity-list / 任务项）触发，且 `preventDefault` 阻止 `/` 被输入到页面。调用 `select()` 让已有查询高亮，便于直接覆盖输入。

### 2. Esc 清空搜索（`app.html`）
在搜索 `<input>` 上直接绑定 Angular 键盘事件：
```html
(keydown.escape)="taskSearchQuery.set(''); $any($event.target).blur()"
```
输入框聚焦时按 Esc → 清空查询并失焦。列表区（非输入框）按 Esc 仍走 `handleTaskKeydown` 的 `case 'Escape'`（清空多选/关闭面板），二者不冲突。

### 3. 可视提示（`app.html` + `app.css`）
在 `.task-search-bar` 内、`<input>` 之后新增：
```html
<kbd class="search-kbd" title="按 / 快速聚焦搜索框">/</kbd>
```
`app.css` 补充 `.search-kbd` 样式（边框、圆角、`font-mono`、hover 变深），并同步更新 `<input>` 的 `placeholder` / `title` 说明快捷键。

## 边界与冲突分析
| 场景 | 行为 | 结论 |
|------|------|------|
| 列表区按 `/` | 搜索框聚焦、选中已有文本 | 期望 |
| 输入框内按 `/` | guard 提前返回，正常输入 `/` 到搜索 | 期望，无冲突 |
| 输入框内按 `Esc` | 清空搜索并失焦 | 期望 |
| 列表区按 `Esc` | 维持原有「清空选择 / 关闭面板」 | 期望，无冲突 |
| 点击搜索框后按 `/` | 已是 INPUT，guard 返回，输入 `/` | 期望 |

## 测试点
- Playwright：`page.keyboard.press('/')` 后断言 `.task-search-input` 为 `document.activeElement`；输入若干字符后 `press('Escape')` 断言 `value===''` 且输入框失焦。
- 回归：既有 Epic 34/35/36、v1.9、Epic 31 E2E 与 `test_epic30_cache.py` 全绿。
