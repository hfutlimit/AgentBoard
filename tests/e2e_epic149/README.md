# E2E 测试（tests/e2e_epic149/）

2026-08-20 起所有 E2E 脚本支持 pytest 收集（`pytest -m e2e tests/e2e_epic149/`）+ 手动运行（`python tests/.../test_xxx.py`）。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENTBOARD_E2E_USER` | `admin` | E2E 测试登录用户名 |
| `AGENTBOARD_E2E_PASS` | `admin123` | E2E 测试登录密码 |
| `AGENTBOARD_API_BASE` | `http://127.0.0.1:18000` | API 地址（dev 默认） |
| `AGENTBOARD_E2E_BASE` | `http://127.0.0.1:4200` | 前端地址（dev 默认） |

## 运行方式

### 手动（兼容旧调用）

```bash
# 全部
for f in tests/e2e_epic149/test_*.py; do python "$f"; done

# 单个
python tests/e2e_epic149/test_x_b1_route_8tab.py
```

### pytest 收集（CI 用）

```bash
# 全部 E2E（需 ng serve + API 在跑）
pytest -m e2e tests/e2e_epic149/ -v

# 单个
pytest tests/e2e_epic149/test_x_b1_route_8tab.py -v

# 自定义凭据
AGENTBOARD_E2E_USER=alice AGENTBOARD_E2E_PASS=alice123 pytest -m e2e tests/e2e_epic149/
```

## 前置条件

- ng serve 在 4200 跑（dev 用 `npm run start` 或 `ng serve`）
- FastAPI dev API 在 18000 跑（dev 用 `python -m uvicorn agentboard.web_app:app --port 18000`，或 `scripts/local-start-api.ps1`）
- Python 依赖：playwright（`pip install playwright && playwright install chromium`）

## 脚本清单

| 文件 | 状态 | 说明 |
|---|---|---|
| `test_x_a1_first_login_agent_load.py` | ✅ | 首次登录后 Agents 立即加载（Task 1298 验证） |
| `test_x_a2_members_data_boundary.py` | ✅ | MembersTab 数据边界 + 字段脱敏（Task 1297 验证） |
| `test_x_b1_route_8tab.py` | ✅ | 8 tab 路由化 + URL 断言（Task 1300 + 1302a 验证） |
| `test_x_b2_responsive_a11y.py` | ✅ | 5 视口响应式 + a11y + btb 高亮（Task 1310 + 1317a 验证） |
| `test_story31x_e2e.py` | legacy | Story 318/319/320 老脚本（仍可跑，新功能已迁移到 x_*） |
| `test_x1_pr3_route_switch.py` | legacy | 旧 route switch 测试 |
| `test_x2_pr3_heading_settings.py` | legacy | 旧 heading 测试 |
| `test_x3_pr1_list_views.py` | legacy | 旧 list view 测试 |
| `test_x3_pr2_detail_views.py` | legacy | 旧 detail view 测试 |
| `test_review_all_views.py` | legacy | 旧 review 截图脚本 |

## CI 集成建议

```yaml
# .github/workflows/e2e.yml
- name: Start dev API
  run: python -m uvicorn agentboard.web_app:app --port 18000 &
- name: Start ng serve
  run: cd frontend && npm run start &
- name: Wait for services
  run: sleep 30
- name: Run E2E
  run: pytest -m e2e tests/e2e_epic149/ -v
```

## 维护

- 新加 E2E 脚本：放 `tests/e2e_epic149/test_xxx.py`，顶部声明 env var 读取
- 截图：自动存到 `tests/e2e_epic149/screenshots/`，gitignored
- 报告 markdown：测试生成的 `report_*.md` 文件可提交，作为该次 review 的视觉证据
