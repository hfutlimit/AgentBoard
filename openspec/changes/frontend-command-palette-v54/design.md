# Design: 命令面板 (Ctrl/Cmd+K)

## 组件改动
- `app.ts`
  - 新增信号：`paletteOpen` / `paletteQuery` / `paletteIndex`。
  - 新增接口 `PaletteCommand { id, title, hint?, keywords?, run }`。
  - `buildPaletteCommands()`：构建静态命令 + 遍历 `recentProjects()` 追加「打开项目」命令。
  - `paletteItems` computed：按 `paletteQuery` 对 title/keywords/hint 做子串过滤。
  - `openPalette()`：重置 query/index 并聚焦输入框；`togglePalette()` / `closePalette()`。
  - `paletteMove(delta)`：循环移动高亮项并滚动到可视区。
  - `paletteRun(cmd?)`：执行高亮或指定命令后关闭面板。
  - `onPaletteKeydown(e)`：ArrowUp/Down 移动、Enter 执行、Esc 关闭（绑定在输入框，避免与全局导航冲突）。
  - 全局 `keydown` 监听最前面新增 `Ctrl`/`Cmd`+`K` → `togglePalette()`，优先于其它快捷键。
- `app.html`
  - 顶栏新增 `#command-palette-toggle`（⌘ 图标）按钮。
  - 模板末尾新增 `@if (paletteOpen())` 命令面板浮层（遮罩 + 搜索框 + 列表 + 底部提示）。
- `app.css`（全局 `styles.css`）
  - `.command-palette-*` 玻璃拟态（blur + 半透明 + 渐变高亮），含暗色主题适配。

## 关键决策
- **复用 `recentProjects()`**：命令面板天然获得「最近项目」动态入口，无需新增存储。
- **输入框内处理方向键/Enter/Esc**：因输入框聚焦时全局 `isInputFocused()` 守卫已使其它快捷键失效，面板内键位不会与列表导航冲突。
- **computed 而非方法**：`paletteItems` 用 `computed`，随 `paletteQuery` 与 `recentProjects` 自动重算。

## 兼容性
- 不引入新依赖；`computed`/`signal` 已在使用。
