# .NET BFF — 依赖安全审计（NU1902 / NU1903）

> 配套：`dotnet/security-allowlist.json`、`scripts/nuget-audit-gate.py`、
> `.github/workflows/dotnet-contract-check.yml` 的 `security-audit` 作业。

## 背景

双栈 BFF 的 .NET 10 解决方案引入了若干传递依赖，其中 4 个包当前携带
**NU1902 / NU1903** 已知的供应链安全公告（GitHub Advisory，GHSA）。截至
2026-08-22，这些公告**上游尚无修复版本**，因此构建层面通过
`dotnet/Directory.Build.props` 的 `<WarningsNotAsErrors>NU1902;NU1903</WarningsNotAsErrors>`
将其降级为警告，避免阻断编译。

但"降级为警告"不等于"安全门关闭"。本目录的审计机制用于**显式闭环**这些已知风险：

- 已知公告进入 `security-allowlist.json`（含 owner / review-by / 接受理由）。
- CI 跑 `scripts/nuget-audit-gate.py`，将 `dotnet list package --vulnerable` 的
  结果与白名单比对。**任何不在白名单上的新公告都会让 CI 变红（fail-closed）**。

这样既能让构建继续，又能保证：一旦上游发布公告、或某个包被新 CVE 命中，
CI 立刻拦截，迫使负责人升级包或评审后加入白名单。

## 当前已接受公告（11 条）

| 包 | 版本 | 严重性 | GHSA | 说明 |
|----|------|--------|------|------|
| Microsoft.OpenApi | 2.0.1 | High | GHSA-v5pm-xwqc-g5wc | 仅构建期 NSwag 客户端生成，不在运行时请求路径 |
| OpenTelemetry.Api | 1.11.2 | Moderate | GHSA-g94r-2vxg-569j | 遥测埋点传递依赖，无 1.11.x 补丁 |
| SQLitePCLRaw.lib.e_sqlite3 | 2.1.11 | High | GHSA-2m69-gcr7-jv3q | 仅 dev/Testing SQLite，生产走 MariaDB（Stage 1 Pomelo） |
| System.Security.Cryptography.Xml | 9.0.0 | High | GHSA-37gx-xxp4-5rgx | .NET 9/10 共享框架包，无补丁版本 |
| System.Security.Cryptography.Xml | 9.0.0 | High | GHSA-w3x6-4m5h-cxqf | 同上 |
| System.Security.Cryptography.Xml | 9.0.0 | High | GHSA-cvvh-rhrc-wg4q | 同上 |
| System.Security.Cryptography.Xml | 9.0.0 | High | GHSA-g8r8-53c2-pm3f | 同上 |
| System.Security.Cryptography.Xml | 9.0.0 | High | GHSA-23rf-6693-g89p | 同上 |
| System.Security.Cryptography.Xml | 9.0.0 | High | GHSA-8q5v-6pqq-x66h | 同上 |
| System.Security.Cryptography.Xml | 9.0.0 | High | GHSA-mmjf-rqrv-855v | 同上 |
| System.Security.Cryptography.Xml | 9.0.0 | High | GHSA-6588-8gv4-xfgh | 同上 |

> 全部为 `accepted-no-upstream-fix` 状态。`review-by` 见白名单 `reviewBy`
> 字段（2026-11-22）。每次 .NET SDK 或 NuGet 包升级时重新评估。

## 本地校验

```bash
# 跑审计门（需先 dotnet restore）
python scripts/nuget-audit-gate.py --report security-audit-report.json
# 退出码：0 = 通过；1 = 出现未评审公告；2 = 基础设施错误（dotnet 缺失/白名单损坏）
```

## CI 门禁

`dotnet-contract-check.yml` 的 `security-audit` 作业在每次 push / PR（涉及
`dotnet/**`、`scripts/**`）时执行：

1. `setup-dotnet` + `dotnet restore`
2. 运行 `scripts/nuget-audit-gate.py --report security-audit-report.json`
3. 上传报告为 GitHub Actions artifact（`security-audit-report`）
4. 退出码非 0 → 作业失败 → PR 无法合并 / push 检查标红

## 如何接受一条新公告

1. 确认该包确无上游修复版本（查 nuget.org / GHSA 页面）。
2. 在 `dotnet/security-allowlist.json` 的 `advisories` 中追加一条：
   ```json
   {
     "ghsa": "GHSA-xxxx-xxxx-xxxx",
     "package": "Package.Name",
     "version": "1.2.3",
     "severity": "High",
     "nugetWarning": "NU1903",
     "status": "accepted-no-upstream-fix",
     "note": "为什么暂时无法升级"
   }
   ```
3. 将 `reviewBy` 顺延 3 个月。
4. 提交并推送（建议单独 PR，便于评审）。

## 如何消除一条公告（优先）

优先于"接受"的做法：升级到已修复版本。若公告已在新版本解决，
修改对应 `*.csproj` 的 `PackageReference` 版本，删除 `Directory.Build.props`
里对应的 `WarningsNotAsErrors` 项（若已无残留），并同步移除白名单条目。
