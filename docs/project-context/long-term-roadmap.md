# AgentBoard 长远规划

> 战略基线日期：2026-08-17（deepseek 5 路并行深审）。
> 真源：AgentBoard MCP `project 3 → memory → AgentBoard 产品定位、商业模式与护城河`。
> 评审周期：**每季度**重审一次,定位/路线/决策有变时即更新。

---

## 1. 战略金标准

> **未来 18 个月,当团队说"我们用 Codex/Claude Code + 一个工具管 AI 任务",那个工具应该叫 AgentBoard。**

判定成功的两个抓手：
1. **认得出**：在任何 AI 工具对比里,AgentBoard 必须被作为"Agent-native 协作"的代表提及
2. **用得上**：从"内部 dogfooding"扩展到"1-3 个外部付费客户"再到"百人级团队标配"

---

## 2. 12 个月路线（Q4 2026 → Q3 2027）

### Q4 2026（当前季度）

| 里程碑 | 状态 | 说明 |
|---|---|---|
| ✅ P0 安全整改 | 完成 | 7 项 P0 一日内修复（API key 轮换 / SPA 路径穿越 / markdown XSS / probe RCE / .env 镜像层 / 默认配置开放 / MariaDB 迁移工具） |
| ✅ 后端 9 阶段垂直切片重构 | 完成 | `service.py` 5449→2926 行,`api.py` 4000→435 行,10 个 APIRouter |
| ✅ 前端拆 tab 进行中 | 进行中 | Epic 149 Story 319 已拆 5/8（documents/epics/proposals/backlog/tickets/stats） |
| ✅ Epic 11 UI 风格重设计 15 项 | 完成 | P-01~P-15 全部 [x]（设计 token / 字体 / 品牌 / 暗色主题） |
| ✅ Epic 11 A 类 22 项 | 完成 | A-01~A-22 全部 [x]（看板 / 状态色 / 类型图标 / 行内编辑 / 抽屉 / 快捷键 等） |
| ✅ Epic 11 B 类 6 项 | 完成 | B-01~B-06（标签 / 负责人 / 截止日期 / 看板拖拽 / 评论 / 分组 + 折叠） |
| ✅ 双栈 BFF Stage 0 | 完成 | .NET 10 BFF 脚手架 + 契约冻结 + Serilog/OTel + docker-compose（commit `8faee87`） |
| 🟡 仓库清理 | 进行中 | `2026-08-19-repository-cleanup` 4 任务,Task 1~3 未开始 |
| 🟡 README 定位话术统一 | 进行中 | 产品定位未在 README 落地 |
| 🟡 测试 / CI 体系跑绿 | 进行中 | 215 测试无 pytest.ini / 无 CI,需 pytest config + GitHub Actions |

### Q1 2027

| 目标 | 验收 |
|---|---|
| **"AI 原生小团队"定位版本** | 多用户 + RBAC 雏形 + 项目级 / 字段级权限 |
| **数据备份/恢复 + 灾难恢复演练** | 一键导出 / 导入 / 月度演练 + 文档化 runbook |
| **商业化路径 A 试点** | 1-3 个外部 SaaS 客户,按用户+Agent 数量定价 |
| **前端拆 tab 收尾** | Epic 149 Story 319 8/8 全部 [x],首页/工作台进入"组件化"成熟期 |
| **仓库清理 + CI 落地** | `.github/workflows` 补全 Python + Frontend + .NET 三栈 CI,缓存 + 矩阵 + 失败拦截 |

### Q2 2027

| 目标 | 验收 |
|---|---|
| **企业版基线** | RBAC + SSO(OIDC/SAML) + 审计日志完整 |
| **Agent 适配器市场开放** | Qoder/GLM/Deepseek 适配器入库 + 第三方贡献通道 |
| **数据驱动护城河显化** | playbook 库 ≥ 1000 任务沉淀 + episode RAG 跨项目命中率可观测 |
| **双栈 BFF Stage 2** | 写迁 .NET + Webhooks/Notifications/SignalR 落地 + 灰度切流 |

### Q3 2027

| 目标 | 验收 |
|---|---|
| **双栈 BFF Stage 3** | FastAPI 业务 router 下架,FastAPI 内部化为 AI service |
| **第三方 OAuth** | GitHub/Google/Microsoft 登录 |
| **复杂报表** | Sprint 燃尽、Agent 能力画像、团队产能仪表盘 |
| **SOC 2 / GDPR** | 合规基线（如走企业版路线） |

---

## 3. 四条商业化路径

### 路线 A：开源核心 + 托管 SaaS（**推荐主路径**）
- **开源核心**：自托管版（AGPL-3.0）,社区驱动
- **托管 SaaS**：agentboard.cloud,按用户/项目/Agent 数量阶梯定价
- 对标：Linear（个人 + 团队版）
- **决策**：license 倾向 **AGPL-3.0**（保 SaaS 优势）

### 路线 B：自托管企业版
- **RBAC 增强**：项目级 / 字段级权限
- **SSO**：OIDC / SAML
- **审计**：完整操作日志 + 导出
- **合规**：SOC 2 / GDPR
- **触发条件**：B 端客户出现时启动,否则不主动做

### 路线 C：Agent 连接器市场
- 每种 CLI/模型适配器作增值（已验证 workbuddy/codex/minimax,待做 qoder/GLM/deepseek）
- 第三方开发者可贡献新适配器
- 与开源核心协同,贡献者获 SaaS 配额

### 路线 D：数据资产变现（远期）
- Agent 能力画像市场（企业可买"擅长 Python 后端 + FastAPI + 复杂状态机"的 Agent 排名）
- 项目知识库 SaaS（独立项目沉淀的 playbook 可被买/卖）
- **当前不做**,待数据护城河显化后再评估

---

## 4. 关键决策待办

| # | 决策 | 选项 | 现状 | 建议 | 触发 |
|---|---|---|---|---|---|
| 1 | **定位二选一** | A. 个人 + Agent 协作 / B. 团队 + 多 Agent 协作 | 当前默认 A（无鉴权） | 短期保留 A（产品文档明确）,中期推 B（Q1 2027） | Q4 2026 末复盘 |
| 2 | **license** | AGPL-3.0 / Apache 2.0 / 商业 | 未定 | AGPL-3.0（保 SaaS 优势） | SaaS 上线前必须定 |
| 3 | **SaaS 定价** | 按用户 / 按项目 / 按 Agent 数量 | 未定 | 按用户 + Agent 数量组合（限 Agent 防滥用） | 第一个外部客户前定 |
| 4 | **企业版基线** | RBAC + SSO + 审计 | 仅有基础权限 | 必须先有,否则 B 端无机会 | Q1 2027 起 |
| 5 | **前端组件库选型** | 自研 / Angular Material / CDK + 自研 | 当前 CDK + 自研 | 保持自研,避免引入外部视觉语言 | 出现跨项目复用时再评估 |
| 6 | **双栈 BFF 切流策略** | 一次切 / 灰度 / 蓝绿 | 计划灰度 | 灰度（`scripts/cutover.ps1` 控 nginx upstream 权重） | Stage 2 启动时定细节 |

---

## 5. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 安全现状不允许对外 | 高 | P0 一天内已修；持续关注 `validate_runtime_security()` 护栏 |
| 竞争挤压（Jira/Linear/GitHub 都加 AI） | 中-高 | 12-18 个月窗口期,**速度即护城河** |
| 生态依赖（agent CLI 版本漂移） | 中 | 适配器抽象 + 自动化回归测试（CI 必接） |
| 前端技术债（上帝组件 → 拆 tab 进行中） | 中-低 | Epic 149 拆 tab 是第一刀,Q1 2027 进"组件化成熟期" |
| 定位张力（默认单用户开放 vs 多 Agent 协作） | 中 | 二选一写进 README,产品定义先行 |
| 国内模型 API 不稳定 | 中 | 适配器层加重试 / 降级 / 切流 |
| AGPL-3.0 吓退企业客户 | 中 | 同时提供商业 license 给付费客户 |
| 数据网络效应"冷启动" | 中 | 内部 dogfooding 先填料,playbook 库 1000+ 是触发点 |

---

## 6. 不做什么（长期边界）

> **"内部工具已验证、产品化未完成"** —— 现阶段不是"做更多功能",而是补完 4 件事:

1. 把安全做对
2. 把架构做对
3. 把定位说清
4. 把护城河做厚

明确不做的：
- ❌ 复杂报表 / BI 仪表盘（Q3 2027 后再说）
- ❌ 移动端原生 App（web responsive 优先）
- ❌ 多语言 i18n（v1 只做中英,Q2 2027 后看）
- ❌ 任务间依赖图谱（暂不做,需求不强烈）
- ❌ 工作流引擎 / 自定义字段（用模板 + Spec 兜底）

---

## 7. 里程碑看板

| 阶段 | 关键交付 | 截止 | 状态 |
|---|---|---|---|
| **MVP v0.1** | CRUD + spec + MCP + SQLite | 2026-07-10 | ✅ |
| **重构 v0.2** | API/Web/MCP 拆分 + 鉴权 + MariaDB | 2026-07-13 | ✅ |
| **v0.3 Jira 核心** | 优先级 / 评论 / 附件 / Sprint / Agent 定时 | 2026-07-13 | ✅ |
| **Epic 11 持续前端优化** | UI 风格 / A 类 / B 类 44 项 | 2026-07-11 | ✅ |
| **Epic 149 前端拆 tab** | 8 tab 全部组件化 | 进行中 | 🟡 5/8 |
| **后端 9 阶段重构** | service/api 瘦身 + 状态机基类 | 2026-08-14 | ✅ |
| **双栈 BFF Stage 0** | .NET 脚手架 + 契约冻结 | 2026-08-19 | ✅ |
| **双栈 BFF Stage 1** | 只读业务迁 .NET | Q1 2027 | ⬜ |
| **双栈 BFF Stage 2** | 写迁 .NET + SignalR + 灰度 | Q2 2027 | ⬜ |
| **企业版 v1.0** | RBAC + SSO + 审计 | Q1 2027 | ⬜ |
| **商业化 SaaS 公测** | 1-3 个外部客户 | Q1 2027 | ⬜ |
| **数据护城河显化** | playbook 1000+ + RAG 命中率 | Q2 2027 | ⬜ |

---

## 维护

- **真源**：AgentBoard MCP `project 3 → memory`（产品定位文档）
- **更新策略**：
  - 路线状态变更 → 改本文件 + MCP
  - 决策拍板 → 改本文件 §4 + MCP
  - 风险变化 → 改本文件 §5 + MCP
- **下次评审**：2026-11-15（Q4 末）
