# 文档详情三视图 + 新标签页打开

## 问题

文档详情页仅有单页渲染 + 弹窗编辑，无法满足分屏编辑需求；列表点击已支持新标签页打开（既有实现），详情页体验需升级。

## 方案

### 1. 列表新标签页（既有）

- 文档列表项 `<a href="/documents/{id}" target="_blank">` 已实现；
- `/documents/{id}` 路由重定向到 `/project/{pid}/documents/{docId}`（项目 Tab 内联详情），URL 稳定可分享。

### 2. 详情页三视图

新增 `docViewMode: 'preview' | 'split-edit' | 'split-read'` 信号：

| 模式 | 布局 | 顶部按钮 |
|---|---|---|
| 完全预览（默认） | 仅渲染 Markdown | 编辑文档（元数据弹窗）/ 编辑内容（进分屏编辑）/ 主题切换 |
| 分屏编辑 | 左编辑器 + 右预览，同步滚动 | 保存 / 取消 / 退出 / 主题切换 |
| 分屏只读 | 左原始文本（只读）+ 右预览，同步滚动 | 编辑 / 取消（禁用）/ 保存（禁用）/ 退出 / 主题切换 |

- 状态流：`preview --[编辑内容]--> split-edit --[保存|取消]--> split-read --[退出]--> preview`；
- 取消/退出有未保存改动时弹**自定义确认弹窗**（禁用原生 confirm），确认后丢弃改动继续流转；
- `syncDocSplitScroll` 两侧按比例同步滚动。

### 3. 复用既有能力

- 主题切换复用全局 `toggleTheme()`；
- 元数据编辑弹窗复用 `openDocModal('edit')`；
- 列表 tooltip 复用 `[title]` 属性。

## 改动清单

- `frontend/src/app/app.ts`：三视图信号 + 方法（enterDocSplitEdit/saveDocSplitContent/cancelDocSplitEdit/exitDocSplit/confirmDiscardDocChanges/syncDocSplitScroll）；
- `frontend/src/app/app.html`：document 视图重构为三视图 + 自定义确认弹窗；
- `frontend/src/app/app.css`：分屏布局样式（`.doc-split`、`.doc-split-pane`、`.doc-confirm-modal`）。

## 验证

- `ng build` 通过；
- `npm test`（vitest）53 passed；
- 手动路径：列表 → 新标签打开 → 编辑内容 → 保存 → 分屏只读 → 退出。
