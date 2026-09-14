# AgentBoard 代码评审报告

- **基线 commit**：`5ab136a`（HEAD == origin/main，工作区 clean）
- **评审范围**：`d9e0f1a2b3c4`..`5ab136a`（含此前被本地 ref 故障遮蔽的 4 个 commit）
- **评审时间**：2026-09-10
- **性质**：只读评审，未改动任何代码或 git 状态

---

## 〇、前置：git ref 故障已修复

上轮评审结论（"远端陈旧在 8db5057、709 个本地 commit 未推送"）**是错误的**，根因是本地 `.git/packed-refs` 被固定在 `8db5057`（Jul 19），而真实远端是 `5ab136a`。

| 项 | 修复前 | 修复后 |
|---|---|---|
| `.git/packed-refs` origin/main | `8db5057`（陈旧） | `5ab136a` ✅ |
| `git rev-parse origin/main` | `8db5057` | `5ab136a` ✅ |
| `git status` | 疑似大量分叉 | `up to date with 'origin/main'` ✅ |

备份位于 `.git/_backup_refs/`。以 `git ls-remote` 为权威源修正后 `reset --hard`，现已完全同步。

---

## 一、结论先行

本轮新暴露的 4 个 commit（`9faf32d` / `95e8852` / `1661af1` / `5ab136a`）整体质量**中上**：`9faf32d` 的 conftest 修复是**教科书级根因定位**，`95e8852` 的 Node 端结构化错误协议方向完全正确。但 `ClaimFailureClassifier` 这个新增的跨语言契约层存在**一个真功能缺陷 + 一个死常量 + 一个假绿测试**，且三者互相掩盖。

| # | 等级 | 问题 | 位置 | 状态 |
|---|---|---|---|---|
| 1 | 🟡 重要 | `new_token_required` 被归为 `Conflict`，注释声称应是 `AckAndDrop`；实际导致无界重投 | `ClaimFailureClassifier.cs:76,105-108` | 未修 |
| 2 | ⚪ 纠偏 | `Reasons.WorkFailed = "work_failed"` 是**死常量**，Python 侧不存在该字面量 | `ClaimFailureClassifier.cs:47` | 未修 |
| 3 | ⚪ 纠偏 | 单测用**捏造的 body** 断言，自证式假绿 | `WorkerOwnedTests.cs:277-280` | 未修 |
| 4 | 🔴 阻断（沿用上轮） | `_vote_majority` 用 `created_by_user_id` 判投票权，Task 移交后新 owner 投不了 | `features/scheduling/service.py:2561` | 未修 |
| 5 | 🟡 重要（沿用上轮） | MQ 路径不写 Task 级重试桶，per-task 封顶失效 | `coordinator.py:261` | 未修 |
| 6 | ⚪ 纠偏 | pr9 / golden 两个 E2E 仍红，与 #4 同源，已被 commit 自述为"新 Story" | `tests/e2e/happy_path/` | 已知 |

本轮**无新增阻断级问题**；`9faf32d` / `5ab136a` 两个 commit 未发现缺陷。

---

## 二、🟡 问题 1：`new_token_required` 分类错误（真功能缺陷）

### 证据链

**① Python 侧发出的实际形态是裸字符串**（`worker_work.py:436`）：

```python
raise HTTPException(409, "new_token_required after lease expiry")
```

FastAPI 序列化为 `{"detail": "new_token_required after lease expiry"}` —— **`detail` 是 `JsonValue`，不是 `JsonObject`**。

**② C# 解析器对 `JsonValue` 返回整句**（`ClaimFailureClassifier.cs:105-108`）：

```csharp
if (detail is JsonValue val)
{
    return val.GetValue<string>() ?? "";   // 返回 "new_token_required after lease expiry"
}
```

**③ 结构化分支用 `string.Equals` 精确匹配，必然不命中**（同文件 :76）：

```csharp
if (string.Equals(parsed, Reasons.NewTokenRequired, StringComparison.Ordinal))
    return new Outcome(Kind.AckAndDrop, parsed, body);   // ❌ 走不到
```

**④ 落到 `Conflict`**（:82）—— 与注释 `:19-24` 明确写的 "AckAndDrop ... `<c>new_token_required</c>`" **直接矛盾**。

### 实测验证（精确复刻 C# 逻辑）

```
真实 Python 输出：ghost (结构化 409)
   parsed='ghost_work_row'                     => Kind=AckAndDrop  ✅
真实 Python 输出：new_token_required (裸串 409)
   parsed='new_token_required after lease expiry'
                                               => Kind=Conflict    ❌
真实 Python 输出：work failed (结构化 409)
   parsed='work failed; manual reconciliation required'
                                               => Kind=Conflict    ✅
```

### 杀伤路径（会真发生）

`WorkerOwnedService.cs:349` 的 journal token 复用规则是问题的引信：

```csharp
var entry = saved?.AgentId == profile.Id ? saved
          : new JournalEntry(workId, profile.Id, Guid.NewGuid().ToString("N"), null);
```

**情形：worker 重启后重放旧投递**（journal 有记录、agent 相同 → 复用旧 token）。若该 work 已被另一个 worker 用新 token 租走：

1. claim 带旧 token → 命中 `worker_work.py:435` → 409 `new_token_required`
2. C# 归为 `Conflict` → 发 `GET api/worker-work/{id}` → 读到 `state = "leased"`
3. `ReconcileTerminalHistory(attemptMatches: false)` 恒返回 false，`terminal` 非终态 → **`Execute` 返回 `false`**
4. 上层 `WorkerOwnedService.cs:267` → `ReturnToTail` 重新入队
5. 2 秒后取回同一行 → 旧 token 再发 → **回到第 1 步**

**这正是 `fdfe9ad` 当初想消灭的"同一行反复重投、饿死后续工作"模式**，现在从另一个入口复现了。

### 修法（两条，建议都做）

**方案 A（推荐，改 Python —— 让契约本身自洽）**：把 `worker_work.py:436` 改为结构化 detail，与其他 409 一致：

```python
raise HTTPException(409, detail={"reason": "new_token_required",
                                 "state": row.state,
                                 "detail": "lease expired; server has re-leased this work"})
```

**方案 B（改 C# —— 让解析器容忍裸串）**：在 `Classify` 的 409 分支、`string.Equals` 之前，加一次子串兜底判定；或让 `TryParseDetailReason` 的 `JsonValue` 分支对已知 reason 前缀做归一化。

> 建议 A + B 都做：A 修数据源头，B 防未来再出现裸串。修完必须补一条真实 Python 响应的回归测试（见问题 3）。

---

## 三、⚪ 问题 2：`WorkFailed` 是死常量

全库搜索 `work_failed` 的结果：

```
src/backend-fastapi/...              无
src/nodes/.../ClaimFailureClassifier.cs:47    public const string WorkFailed = "work_failed";
src/nodes/.../ClaimFailureClassifier.cs:80    // 注释提到
src/nodes/.../WorkerOwnedTests.cs:279         测试用捏造 body
```

**Python 侧从不产生 `work_failed`**；真实字面量是 `"work failed; manual reconciliation required"`（`worker_work.py:419`，在结构化 envelope 里）。因此 `Reasons.WorkFailed`：

- 结构化路径永不命中（`detail.reason` 是整句，不是 `work_failed`）
- 子串兜底路径**只在测试的捏造 body 上命中**（因为那句话里被手动加了 `(work_failed)` 后缀）

后果：语义上 `work_failed` 与真实值不同，日志/diagnostic 会输出一个服务端不认识的原因码。功能上因 `Conflict` 兜底未酿成事故，但**这是个会误导人的假契约**。

**修法**：删掉该常量，或改为与 Python 完全一致的值并在两处同步（若真要稳定的机器可读码，那就回到方案 A 的思路 —— **Python 侧补一个短 `reason` 码**，这才是正解）。

---

## 四、⚪ 问题 3：自证式假绿测试

`WorkerOwnedTests.cs:277-280`：

```csharp
var outcome = ClaimFailureClassifier.Classify(HttpStatusCode.Conflict,
    "work failed; manual reconciliation required (work_failed)");   // ← 服务端不会这样发
Assert.Equal(ClaimFailureClassifier.Kind.Conflict, outcome.Kind);
Assert.Equal(ClaimFailureClassifier.Reasons.WorkFailed, outcome.Reason);
```

该 body **不是任何 Python 端点会产生的东西**——括号里的 `(work_failed)` 是为让断言通过而手工加的。测试验证的是"我自己构造的输入能被我自己解析"。

`1661af1` 自述修了"PR8 422 假绿"，但**没有碰这个文件**（`git show 1661af1 --name-only` 只含 `ClaimFailureClassifier.cs` / `test_pr7` / `test_pr8`）。同类问题在 Python 侧修了、C# 侧漏了。

**修法**：三条测试全部改用**从 Python 源码抄来的真实 body**：

```csharp
// 真实：worker_work.py:373-379
"""{"detail":{"reason":"ghost_work_row","state":"available","entity_type":"","entity_id":0}}"""
// 真实：worker_work.py:416-420
"""{"detail":{"state":"failed","attempt_matches":true,"reason":"work failed; manual reconciliation required"}}"""
// 真实：worker_work.py:436（修方案 A 后）
"""{"detail":{"reason":"new_token_required","state":"leased"}}"""
```

并**新增一条**覆盖 `new_token_required → AckAndDrop` 的用例 —— 这条测试在修问题 1 之前必然失败，这正是它该有的可证伪性。

---

## 五、本轮无缺陷的 commit

### `9faf32d` — conftest 共享同库 session ⭐ 推荐作为范例

根因定位精准：`api_helpers` 在 module-import 时通过 `from .database import SessionLocal` 冻结了绑定，导致 conftest 只替换 `database.SessionLocal` 时中间件仍走旧 engine，撞上仓库里 5 字节的 `dummy` 占位文件 → `file is not a database`。

修法正确（yield-style `app.dependency_overrides[get_session]` + teardown 还原），副作用收益明确（11 fail → 4 fail）。实测验证：`test_worker_work_ghost_rows.py` + `test_worker_owned_work.py` **27 passed**。

### `95e8852` — `api_helpers.py` 的 `_db.SessionLocal()` 改造 ✅

同一根因的第二处修复，5 处调用点全改，并留了详尽的 WHY 注释（文件头 :32-44）。实测确认 `api_helpers.py` 已无 import-time `SessionLocal` 冻结绑定（`grep` 仅剩注释行）。

> 顺带发现：`features/learning/judge.py:342` 用的是**函数内** `from ...database import SessionLocal`（非 module-level），行为正确、无需修改。这是唯一另一处 `SessionLocal` 局部导入点。

### `5ab136a` — drawer 宽度调整 ✅

`DEFAULT_WIDTH` 520 → 视口 60%（clamp `[600, viewport-120]`），`MIN_WIDTH` 360 → 600，CSS `min-width` 同步。逻辑自洽，`readWidth()` 对 localStorage 旧值（520 < 600）自动回退到新 default，无残留。

> 唯一提示（非缺陷）：`MIN_AVAILABLE_WIDTH = 120` 与 CSS `max-width: calc(100vw - 120px)` 一致，但两者是**重复定义**。未来改一处忘另一处会分叉，建议其中一个引用另一个（CSS 变量注入或注释互指）。

---

## 六、沿用上轮、仍然有效的发现

### 🔴 `_vote_majority` 用错归属字段（`features/scheduling/service.py:2561`）

```python
# 归属收敛（2026-09-01）：Task 投票人必须是 owner（created_by_user_id）本人
if entity.created_by_user_id is not None:
    if reviewer_user_id != entity.created_by_user_id:
        raise InvalidValue("only the task owner's agent can vote on this task (majority mode)")
```

注释把 `owner` 和 `created_by_user_id` **当成同一个概念**，但两者语义不同：`owner_user_id` 可变、`created_by_user_id` 不可变。Task 移交后：

- 新 owner 的 agent 投票被拒
- **旧 owner（已不该管）反而能投**

统一真源 `features/work_items/ownership.py:75` 的 `work_item_owner_user_id()` 就在同仓库，改成它即可。需补「移交后投票」回归测试 —— `tests/test_m2_transfer.py` 15 个用例无一覆盖。

> 本轮实测佐证：happy_path 中 `test_pr9` / `test_golden` 两个失败，报错是 `task 5 has no owner (owner_user_id is NULL)` —— 与 T1.5 owner 门改动同源，commit message 也已自述为"新 Story 规模重写"。

### 🟡 MQ 路径不写 Task 级重试桶（`coordinator.py:261`）

`_burn_poll_cycle` 仅在 `coordinator.py:710/721`（轮询路径）调用；MQ 路径 `_message_consumed` 死信后不调用，其 retry_key 的 ref_id 来自消息（assignment），永远不是 0。→ **MQ 模式下 per-task 封顶失效**。worker-owned 队列另有 `worker_work.attempts < 3` 兜底，故仅影响 `stats["mode"] == "mq"` 的部署。

---

## 七、建议修复顺序

| 顺序 | 动作 | 理由 |
|---|---|---|
| 1 | `_vote_majority` 改判 `work_item_owner_user_id` + 补移交投票测试 | 唯一阻断级，业务正确性问题 |
| 2 | `new_token_required` 改结构化 detail（方案 A）+ C# 加兜底（方案 B） | 消除无界重投入口，且让契约自洽 |
| 3 | 三条 C# 测试改用真实 Python body + 新增 `new_token_required` 用例 | 让问题 2 的修复被真实覆盖 |
| 4 | 删 `Reasons.WorkFailed` 或与 Python 同步 | 清理假契约 |
| 5 | MQ 路径补 `_burn_poll_cycle` | 需要先确认生产是否真跑 MQ 模式 |
| 6 | pr9 / golden E2E 重写 | 已在 commit 中挂到独立 Story |

---

## 八、实测验证记录

| 验证项 | 命令 / 方法 | 结果 |
|---|---|---|
| 仓库同步 | `git rev-parse HEAD origin/main` | 均为 `5ab136a` ✅ |
| 工作区干净 | `git status --short` | 无输出 ✅ |
| ghost + worker_owned 套件 | `pytest tests/e2e/happy_path/test_worker_work_ghost_rows.py test_worker_owned_work.py -q` | **27 passed** ✅ |
| 完整 happy_path | `pytest tests/e2e/happy_path/ -q` | **35 passed, 2 failed**（与 commit 自述一致）✅ |
| C# 分类器逻辑 | Python 精确复刻 `TryParseDetailReason` + `Classify` | 确认 `new_token_required` → `Conflict` ❌ |
| `work_failed` 存在性 | 全库 `grep -rn "work_failed" src/` | Python 侧 0 命中 → 死常量 ⚪ |
| import-time SessionLocal | `grep -rn "import SessionLocal" src/backend-fastapi/` | 仅 `judge.py`（函数内，正确）+ 注释 ✅ |

> 未执行：`dotnet test`（本机 NuGet targets 报 `Value cannot be null. (Parameter 'path1')`），故 C# 分类器改用 Python 精确复刻验证 —— 逻辑等价，结论有效。
