# Tasks: 筛选预设默认加载自动应用 (v4.6)

## Task 1 (high): 实现默认预设加载时自动应用
- **文件**：`frontend/src/app/app.ts`
- `ngOnInit()` 末尾调用 `applyDefaultPresetOnLoad()`
- 新增 `applyDefaultPresetOnLoad()` 方法（幂等，仅初始化执行一次）
- **状态**：in_review
- **验收**：刷新页面后默认预设自动套用，状态 chip 激活

## Task 2: E2E 验证
- **文件**：`tests/test_epic59_v46_preset_autoload_e2e.py`
- 流程：登录 → 进入 story → 选状态 chip → 存为预设 → 设为默认 → 刷新 → 断言 chip 激活 + 预设标记默认
- 回归：`pytest test_epic30_cache` + 既有 v 系列 E2E
- **状态**：done

## 交付物
- `frontend/src/app/app.ts`（+35 行）
- `openspec/changes/frontend-filter-preset-autoload-v46/{proposal,design,tasks}.md`
- `tests/test_epic59_v46_preset_autoload_e2e.py`
- 静态产物 `agentboard/web/static/main-*.js`
