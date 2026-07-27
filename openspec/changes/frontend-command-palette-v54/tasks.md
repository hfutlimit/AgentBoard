# Tasks: 命令面板 (Ctrl/Cmd+K)

- [x] 新增 `PaletteCommand` 接口与 `paletteOpen/paletteQuery/paletteIndex` 信号
- [x] 实现 `buildPaletteCommands()`（静态命令 + 最近项目动态命令）
- [x] 实现 `paletteItems` computed 过滤逻辑
- [x] 实现 `openPalette/togglePalette/closePalette/paletteMove/paletteRun/onPaletteKeydown`
- [x] 全局 `Ctrl`/`Cmd`+`K` 绑定
- [x] `app.html` 顶栏触发按钮 + 命令面板浮层模板
- [x] `styles.css` 玻璃拟态样式 + 暗色适配
- [x] `npm run build` 通过并部署静态产物到 `agentboard/web/static/`
- [x] Playwright E2E 验证（打开/过滤/键盘执行/关闭/导航/零报错）
- [x] 状态流转：Task 1122 / Story 106 / Epic 57 → in_review
