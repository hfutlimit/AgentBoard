# Alembic 重复 merge migration 踩坑（2026-09-14）

`git pull --rebase` 期间本地脏 working tree 里的 alembic merge migration
会跟 remote 已有的等价 merge migration 形成 multiple heads，需要立刻识别并 revert。

供未来 AgentBoard / alembic 项目的开发者和 reviewer 参考。

---

## 1. 现象：本地脏的 merge migration 在 rebase 后变重复

**规则 → 在 AgentBoard 这种多人协作的 alembic 项目里，本地 working tree 里**不要**长期留
alembic merge migration（empty upgrade/downgrade）—— 任何一次 `git pull --rebase` 都可能让
它跟 remote 上同时期别人提交的等价 merge 撞上，alembic 立刻变 multiple heads。**

**证据/原因**（2026-09-14 AgentBoard）：

1. 9-12 `f414e84 fix(ops): merge alembic heads + register workflow_run tests in CI` 在
   remote 上提交了 `n2o3p4q5r6s7_merge_workflow_runs_and_ghost_cleanup_heads.py`，合并
   `d9e0f1a2b3c4` (Story 434 ghost cleanup) + `m5n6o7p8q9r0` (Workflow Run slice 1)。
2. 同时间本地 working tree 有一份未跟踪的
   `51483e66a514_merge_ghost_work_cleanup_with_workflow_.py`（同一对 parents 的等价 merge），
   DB 端已经 `alembic upgrade head` 跑到 51483e66a514。
3. 9-14 准备 `git push` 之前没注意这一点，写了 `git add` + `git commit` 把
   `51483e66a514` 推上 remote。
4. remote 收到 push 后变 multiple heads：
   ```
   $ alembic heads
   Heads: ['51483e66a514', 'n2o3p4q5r6s7']
   MultipleHeads: Multiple head revisions are present
   ```
5. 修法：`git revert --no-edit 6249f7b` 把 `51483e66a514` 整文件删掉（保留 remote 的
   `n2o3p4q5r6s7` 作为唯一 head），revert commit 推到 remote，alembic 恢复 single head。

**反模式**：
- working tree 长期留 merge migration 不 commit（"先 dirty，跑 DB 验完再 commit"）
- rebase 之后不立即 `python -c "from alembic.script import ScriptDirectory; ..."` 验 heads
- 直接 force push（污染其他人 reference）

**正确做法**：

```powershell
# Step 1: 写 merge migration 后**立刻** commit + push（不留在 dirty tree）
git add src/backend-fastapi/migrations/versions/<revision>_merge_*.py
git commit -m "chore(alembic): merge <parent_a> + <parent_b> into single head"
git push origin main

# Step 2: rebase 之前先把脏的 merge migration 暂存到 stash
git stash -u --include-untracked -- src/backend-fastapi/migrations/versions/<revision>_merge_*.py
git pull --rebase origin main
# 验 remote 是否已存在等价 merge（按 parents 对比）
git stash pop  # 如果没有等价 → pop；如果有等价 → drop

# Step 3: rebase 后**必跑** alembic heads 验
cd src/backend-fastapi
python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; \
  cfg = Config('alembic.ini'); \
  sd = ScriptDirectory.from_config(cfg); \
  print('Heads:', list(sd.get_heads()))"
# 期望：Heads: ['<single>']  多个就是撞了
```

**附加：等价 merge 的识别法**：
- 比对 `down_revision`（parents 列表）
- 两个 merge 都引用相同 `(parent_a, parent_b)` 即等价，保留修改日期更新的那个
- 优先保留 remote 上的（它有配套 CI 修复或测试 fix 概率更高）

---

## 2. 教训：rebase 之后永远先验 alembic single head 再 push

**规则 → `git pull --rebase` 之后，push 之前必跑 `alembic heads` 看是否多 head；
多 head 就 revert 自己的 merge 而不是 force push。**

**证据/原因**：
- `git push` 不会自动检测 alembic 多 head（git 只看文本 diff）
- `f414e84` 的 commit message 写 "alembic heads is now exactly [n2o3p4q5r5s6]"
  实际是 `n2o3p4q5r6s7`（typo）但不影响功能
- alembic `MultipleHeads` 只在 `alembic upgrade head` / `alembic current` / `alembic heads`
  运行时才暴露，纯 git 操作看不到
- `git revert <commit>` 是最干净的解法（保留完整 audit trail），优于 `git reset --hard`

**附加：识别等价 merge 的最简方法**

```python
# 在 repo 根或 src/backend-fastapi 跑
from alembic.config import Config
from alembic.script import ScriptDirectory
cfg = Config("alembic.ini")
sd = ScriptDirectory.from_config(cfg)
for rev in sd.get_heads():
    script = sd.get_revision(rev)
    print(rev, "down=", script.down_revision)
# 输出如果两个 rev 的 down_revision 相同 → 等价 merge
```

**适用**：所有用 alembic + 多人协作的项目（不只是 AgentBoard）。任何时候 working tree 出现
未提交的 alembic merge migration，**第一动作 = 立刻 commit + push**，不要等 DB 跑过再推。
</content>
</invoke>