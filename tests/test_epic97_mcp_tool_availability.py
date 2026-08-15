"""Epic 97 P0 — MCP 工具可用性修复与回归护栏。

背景
----
`mcp_server.py` 早期的 HTTP 辅助函数 `_api` 被重命名为 `_http` 后，有 15 处调用点
未同步更新。由于 Python 只在**运行时**解析全局名字，这些工具在导入期毫无异常，
只有真正被 Agent 调用时才抛 `NameError: name '_api' is not defined`——
于是 6 大类工具（批量操作 / 增强搜索 / 导入导出 / 审计 / 依赖 / Webhook）
长期静默失效而没有任何测试发现。

本模块建立两层护栏：

1. **静态层**（`test_no_undefined_global_calls_in_mcp_server`）
   用 AST 遍历 `mcp_server.py`，收集所有形如 `foo(...)` 的调用名，
   逐个在「模块命名空间 ∪ 内建 ∪ 函数内局部绑定」中解析。
   任何解析不到的名字直接判定为未定义调用——不需要跑服务、毫秒级，
   能在 CI 里第一时间拦住同类重构遗留。

2. **集成层**（`test_all_repaired_tools_work_against_real_stack`）
   真实拉起 uvicorn 子进程，把 MCP 客户端指向它，
   **逐个真调**本次修复的 15 个工具，断言既不抛 NameError、
   也不返回 404/422（即路径前缀与传参方式都正确）。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic97_mcp_tool_availability.py -q

注意：与 test_epic96_p0_proposals.py 同因，必须用真实 uvicorn 子进程而非
进程内 TestClient（audit_log_middleware 会 await request.body() 造成死锁）。
"""
import ast
import builtins
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 独立临时数据库（与其它测试隔离），子进程通过环境变量继承同一个库
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import mcp_server  # noqa: E402
from agentboard.database import init_db  # noqa: E402

init_db()

_MCP_SOURCE = Path(_ROOT) / "agentboard" / "mcp_server.py"


# ===================== 第 1 层：AST 静态护栏 =====================

def _collect_local_bindings(fn_node: ast.AST) -> set[str]:
    """收集一个函数体内所有会产生局部名字绑定的标识符。

    覆盖：参数（含 posonly/kwonly/*args/**kwargs）、赋值、增量赋值、海象、
    for/with/except 目标、推导式变量、嵌套 def/class、import 别名。
    """
    names: set[str] = set()

    if isinstance(fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = fn_node.args
        for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs]:
            names.add(arg.arg)
        if a.vararg:
            names.add(a.vararg.arg)
        if a.kwarg:
            names.add(a.kwarg.arg)

    def _add_target(t: ast.AST) -> None:
        if isinstance(t, ast.Name):
            names.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                _add_target(e)
        elif isinstance(t, ast.Starred):
            _add_target(t.value)

    for node in ast.walk(fn_node):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                _add_target(t)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _add_target(node.target)
        elif isinstance(node, ast.NamedExpr):
            _add_target(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _add_target(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    _add_target(item.optional_vars)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.comprehension):
            _add_target(node.target)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])

    return names


def test_no_undefined_global_calls_in_mcp_server():
    """mcp_server.py 中每一处 `foo(...)` 的 foo 都必须能解析到定义。

    这是本次 `_api` 事故的直接护栏：`_api` 既不在模块命名空间、
    也不是内建、也不是任何函数的局部变量 → 必然被本用例捕获。
    """
    tree = ast.parse(_MCP_SOURCE.read_text(encoding="utf-8"))

    module_ns = set(vars(mcp_server)) | set(dir(builtins))

    violations: list[str] = []

    def _check_scope(scope_node: ast.AST, local_names: set[str]) -> None:
        for node in ast.walk(scope_node):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name):
                continue  # 属性调用 obj.method() 交给集成层覆盖
            name = node.func.id
            if name in local_names or name in module_ns:
                continue
            violations.append(f"{_MCP_SOURCE.name}:{node.lineno} 调用了未定义的 `{name}(...)`")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_scope(node, _collect_local_bindings(node))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _check_scope(sub, _collect_local_bindings(sub) | {node.name})
        else:
            _check_scope(node, set())

    assert not violations, (
        "mcp_server.py 存在未定义的函数调用（多半是重构改名后漏改调用点）：\n  "
        + "\n  ".join(violations)
    )


def test_no_legacy_api_helper_references():
    """显式钉死 `_api` 这个已废弃的旧辅助函数名，防止回滚式复发。"""
    src = _MCP_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_api"
    ]
    assert not bad, f"仍存在已废弃的 _api(...) 调用，行号：{bad}（应改用 _http 并补 /api 前缀）"


def test_http_helper_callers_use_absolute_api_paths():
    """所有 `_http(method, path)` 的字面量 path 必须以 /api 开头。

    `_http` 直接把 path 拼到 base_url 上，缺少 /api 前缀会静默 404。
    f-string 路径取其首个字面量片段判断。
    """
    tree = ast.parse(_MCP_SOURCE.read_text(encoding="utf-8"))
    bad: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_http"):
            continue
        if len(node.args) < 2:
            continue
        path_node = node.args[1]
        if isinstance(path_node, ast.Constant) and isinstance(path_node.value, str):
            literal = path_node.value
        elif isinstance(path_node, ast.JoinedStr) and path_node.values:
            head = path_node.values[0]
            if not (isinstance(head, ast.Constant) and isinstance(head.value, str)):
                continue
            literal = head.value
        else:
            continue
        if not literal.startswith("/api"):
            bad.append(f"行 {node.lineno}: _http(..., {literal!r}) 缺少 /api 前缀")
    assert not bad, "以下 _http 调用路径缺少 /api 前缀：\n  " + "\n  ".join(bad)


# ===================== 第 2 层：真实栈集成 =====================

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_ready(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(base + "/api/meta", timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"API 服务在 {base} 启动超时")


@pytest.fixture(scope="module")
def stack():
    """真实拉起 API，并把 mcp_server 的 HTTP 客户端指向它。"""
    port = _free_port()
    proc = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    prev_url = mcp_server.API_URL
    prev_token = os.environ.get("AGENTBOARD_MCP_TOKEN")
    try:
        _wait_ready(base)
        c = httpx.Client(base_url=base, timeout=30)
        c.post("/api/auth/register", json={"username": "e97admin", "password": "e97admin123"})
        r = c.post("/api/auth/login", json={"username": "e97admin", "password": "e97admin123"})
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        c.headers.update({"Authorization": f"Bearer {token}"})

        # 让 MCP 工具走这套真实栈
        mcp_server.API_URL = base
        os.environ["AGENTBOARD_MCP_TOKEN"] = token

        r = c.post("/api/projects", json={"name": "Epic97 MCP 修复验证"})
        pid = r.json()["id"]
        r = c.post(f"/api/projects/{pid}/epics", json={"title": "MCP 可用性"})
        eid = r.json()["id"]
        r = c.post(f"/api/epics/{eid}/stories", json={"title": "工具冒烟"})
        sid = r.json()["id"]

        tids = []
        for i in range(3):
            r = c.post(f"/api/stories/{sid}/tasks",
                       json={"project_id": pid, "title": f"冒烟任务 {i}", "type": "dev"})
            assert r.status_code in (200, 201), r.text
            tids.append(r.json()["id"])

        yield {"c": c, "base": base, "project_id": pid, "epic_id": eid,
               "story_id": sid, "task_ids": tids}
        c.close()
    finally:
        mcp_server.API_URL = prev_url
        if prev_token is None:
            os.environ.pop("AGENTBOARD_MCP_TOKEN", None)
        else:
            os.environ["AGENTBOARD_MCP_TOKEN"] = prev_token
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _assert_no_transport_error(label: str, resp) -> None:
    """断言返回里没有 NameError / 路由未命中 / 传参错误的痕迹。"""
    if isinstance(resp, dict) and "error" in resp:
        err = str(resp["error"])
        assert "not defined" not in err, f"{label} 触发 NameError：{err}"
        assert "Not Found" not in err, f"{label} 路径未命中（多半缺 /api 前缀）：{err}"
        assert "Method Not Allowed" not in err, f"{label} 方法不匹配：{err}"
        assert "Field required" not in err, f"{label} 请求体未正确传递：{err}"


def test_all_repaired_tools_work_against_real_stack(stack):
    """逐个真调本次修复的 15 个工具，断言全部可用。"""
    pid, sid = stack["project_id"], stack["story_id"]
    t1, t2, t3 = stack["task_ids"]

    # --- 增强搜索（单值 + 多值 list） ---
    single = mcp_server.search_tasks_enhanced(project_id=pid, status="todo")
    assert isinstance(single, list), f"单值搜索应返回 list，实得 {single!r}"
    assert len(single) >= 3, f"应搜到 3 个 todo 任务，实得 {len(single)}"

    multi = mcp_server.search_tasks_enhanced(
        project_id=pid, status=["todo", "in_progress"], priority=["medium", "high"],
    )
    assert isinstance(multi, list), f"多值搜索应返回 list，实得 {multi!r}"
    assert len(multi) >= 3, "多值 OR 过滤应至少覆盖全部 todo+medium 任务"

    # --- 批量操作 ---
    r = mcp_server.batch_update_task_status([t1, t2], "in_progress")
    _assert_no_transport_error("batch_update_task_status", r)
    assert isinstance(r, dict) and "updated" in r, f"批量改状态返回异常：{r!r}"
    assert len(r["updated"]) == 2 and not r["errors"], f"批量改状态未全部成功：{r!r}"
    assert stack["c"].get(f"/api/tasks/{t1}").json()["status"] == "in_progress", "状态未真正落库"

    r = mcp_server.batch_assign_sprint([t1], None)
    _assert_no_transport_error("batch_assign_sprint", r)
    assert isinstance(r, dict) and "updated" in r, f"批量分配 Sprint 返回异常：{r!r}"

    # --- 导出 ---
    r = mcp_server.export_project_data(pid)
    _assert_no_transport_error("export_project_data", r)
    assert isinstance(r, dict) and "error" not in r, f"项目导出失败：{r!r}"

    r = mcp_server.export_story_data(sid)
    _assert_no_transport_error("export_story_data", r)
    assert isinstance(r, dict) and "error" not in r, f"Story 导出失败：{r!r}"

    # --- 审计日志 ---
    r = mcp_server.list_audit_logs(entity_type="task", limit=5)
    _assert_no_transport_error("list_audit_logs", r)
    assert isinstance(r, dict) and "error" not in r, f"审计日志查询失败：{r!r}"

    # --- 任务依赖（增 → 查 → 删 全链路） ---
    dep = mcp_server.add_task_dependency(t1, t2, "blocks")
    _assert_no_transport_error("add_task_dependency", dep)
    assert isinstance(dep, dict) and "id" in dep, f"新增依赖失败：{dep!r}"

    got = mcp_server.get_task_dependencies(t1)
    _assert_no_transport_error("get_task_dependencies", got)
    assert isinstance(got, dict), f"查询依赖失败：{got!r}"

    r = mcp_server.remove_task_dependency(dep["id"])
    _assert_no_transport_error("remove_task_dependency", r)
    assert isinstance(r, dict) and "error" not in r, f"删除依赖失败：{r!r}"

    # --- 导入 ---
    r = mcp_server.import_tasks(pid, [
        {"title": "导入任务 A", "type": "dev", "priority": "high"},
        {"title": "导入任务 B", "type": "bug", "priority": "low"},
    ])
    _assert_no_transport_error("import_tasks", r)
    assert isinstance(r, dict) and len(r.get("imported", [])) == 2, f"导入未成功：{r!r}"

    # --- Webhook（增 → 查 → 停用 → 删 全链路） ---
    wh = mcp_server.create_webhook(
        name="e97-hook", url="https://example.com/hook",
        project_id=pid, events=["task.created"],
    )
    _assert_no_transport_error("create_webhook", wh)
    assert isinstance(wh, dict) and "id" in wh, f"创建 Webhook 失败：{wh!r}"

    lst = mcp_server.list_webhooks(project_id=pid)
    _assert_no_transport_error("list_webhooks", lst)
    assert isinstance(lst, dict), f"列出 Webhook 失败：{lst!r}"

    r = mcp_server.toggle_webhook(wh["id"], False)
    _assert_no_transport_error("toggle_webhook", r)
    assert isinstance(r, dict) and "error" not in r, f"停用 Webhook 失败：{r!r}"

    r = mcp_server.delete_webhook(wh["id"])
    _assert_no_transport_error("delete_webhook", r)
    assert isinstance(r, dict) and "error" not in r, f"删除 Webhook 失败：{r!r}"

    # --- 批量删除（放最后，避免影响前面用例） ---
    r = mcp_server.batch_delete_tasks([t3])
    _assert_no_transport_error("batch_delete_tasks", r)
    assert isinstance(r, dict) and "deleted" in r or "errors" in r, f"批量删除返回异常：{r!r}"
    assert stack["c"].get(f"/api/tasks/{t3}").status_code == 404, "任务未真正删除"


def test_search_multi_value_filter_actually_ors(stack):
    """多值过滤必须是真 OR，而不是被后端当成单值/忽略。

    修复前的死代码（for + setdefault）根本没往 params 里塞多值，
    本用例通过「多值结果 ⊇ 各单值结果之并」来钉死语义。
    """
    pid = stack["project_id"]

    todo = mcp_server.search_tasks_enhanced(project_id=pid, status="todo")
    in_progress = mcp_server.search_tasks_enhanced(project_id=pid, status="in_progress")
    both = mcp_server.search_tasks_enhanced(project_id=pid, status=["todo", "in_progress"])

    assert isinstance(both, list), f"多值搜索应返回 list，实得 {both!r}"
    ids_union = {t["id"] for t in todo} | {t["id"] for t in in_progress}
    ids_both = {t["id"] for t in both}
    assert ids_union, "前置数据异常：todo/in_progress 均为空"
    assert ids_union <= ids_both, (
        f"多值 OR 过滤结果不完整：单值并集 {sorted(ids_union)} "
        f"未被多值结果 {sorted(ids_both)} 覆盖"
    )
