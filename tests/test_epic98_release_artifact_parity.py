"""Epic 98 P0 — 发布产物一致性护栏（Release Artifact Parity Guard）。

背景
----
`dist/` 下的 Windows/IIS 部署包是**提交进 Git 的构建产物**，只有人工重跑
`scripts/package_windows.py` 时才会刷新。于是出现过一类彻底无声的事故：

* Epic 97 修好了 `mcp_server.py` 的 15 处 `_api` NameError，
  但 `dist/agentboard-{webapi,mcp}` 里仍是旧文件 → 按 dist 部署的生产环境
  **把已经修好的 bug 原封不动装了回去**。
* Epic 96 新增的 `agentboard/domains/proposals` 整个包、以及配套的 Alembic
  迁移 `h4i5j6k7l8m9_add_proposals.py`，在发布产物里**根本不存在**
  → 生产上连提案相关的表都建不出来。
* 更隐蔽的是 zip 比目录还旧：有人改过 `dist/<pkg>/` 目录却没重新压包，
  而真正被拿去部署的恰恰是 zip。

源码测试全绿、CI 全绿，但交付物是坏的——因为没有任何测试看过交付物。

本模块把「发布产物陈旧」变成确定性失败，四层校验：

1. `test_python_packages_match_source`  —— 目录奇偶校验：源码树里的每个文件都必须
   在包内存在且逐字节相等（缺失 / 多余 / 内容不符分别报错）。
2. `test_zip_matches_package_dir`       —— zip 与目录奇偶校验：防「重建了目录但 zip 陈旧」。
3. `test_known_p0_regressions_absent_from_artifacts` —— 针对已发生过的两起事故钉死回归。
4. `test_no_undefined_global_calls_in_packaged_mcp_server` —— 对 **产物里的** mcp_server.py
   跑 Epic 97 的 AST 未定义调用检查（纵深防御：即使有人手改 dist 也拦得住）。

另有 `test_check_mode_detects_tampering` 自证护栏有效性：故意篡改产物副本后
`--check` 必须非零退出。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic98_release_artifact_parity.py -q

修复方式（用例失败时）：
    python scripts/package_windows.py
"""
import ast
import builtins
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_SCRIPT = _ROOT / "scripts" / "package_windows.py"
_FIX_HINT = "→ 修复：python scripts/package_windows.py"


def _load_packager():
    """直接以模块方式加载打包脚本，复用它的清单定义（保证测试与构建同源）。"""
    spec = importlib.util.spec_from_file_location("package_windows", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pkg_mod = _load_packager()

#: 纯 Python 服务包——内容完全由源码决定，可做严格奇偶校验。
#: （web 包内容取决于前端是否重新构建，交给 --check 人工核对，不在此强制。）
PYTHON_PACKAGES = list(pkg_mod.PYTHON_PACKAGES)


@pytest.mark.parametrize("pkg_name", PYTHON_PACKAGES)
def test_python_packages_match_source(pkg_name):
    """发布包目录必须与源码逐字节一致——不多、不少、不旧。"""
    spec = pkg_mod.package_specs()[pkg_name]
    pkg_dir = Path(pkg_mod.DIST) / pkg_name
    assert pkg_dir.is_dir(), f"发布产物目录不存在：dist/{pkg_name}  {_FIX_HINT}"

    expected = pkg_mod.expected_manifest(spec)
    actual = pkg_mod.actual_files(str(pkg_dir))

    missing = sorted(set(expected) - set(actual))
    assert not missing, (
        f"{pkg_name} 缺少 {len(missing)} 个源码文件（打包后新增的模块没进产物，"
        f"生产会直接 ImportError / 缺表）：\n  " + "\n  ".join(missing) + f"\n{_FIX_HINT}"
    )

    extra = sorted(set(actual) - set(expected))
    assert not extra, (
        f"{pkg_name} 残留 {len(extra)} 个源码中已删除的文件：\n  "
        + "\n  ".join(extra) + f"\n{_FIX_HINT}"
    )

    stale = [
        rel for rel in sorted(set(expected) & set(actual))
        if Path(expected[rel]).read_bytes() != Path(actual[rel]).read_bytes()
    ]
    assert not stale, (
        f"{pkg_name} 有 {len(stale)} 个文件内容落后于源码（源码已修但产物仍是旧版，"
        f"部署即回滚 bug）：\n  " + "\n  ".join(stale) + f"\n{_FIX_HINT}"
    )


@pytest.mark.parametrize("pkg_name", PYTHON_PACKAGES)
def test_zip_matches_package_dir(pkg_name):
    """真正被拿去部署的是 zip，它必须与目录严格同步。"""
    pkg_dir = Path(pkg_mod.DIST) / pkg_name
    zip_path = Path(pkg_mod.DIST) / f"{pkg_name}.zip"
    assert zip_path.is_file(), f"缺少发布包 dist/{pkg_name}.zip  {_FIX_HINT}"

    disk = pkg_mod.actual_files(str(pkg_dir))
    problems = pkg_mod.check_zip(pkg_name, str(zip_path), disk)
    assert not problems, (
        f"{pkg_name}.zip 与目录不同步（改了目录却没重新压包，部署拿到的是旧 zip）：\n  "
        + "\n  ".join(problems) + f"\n{_FIX_HINT}"
    )


@pytest.mark.parametrize("pkg_name", PYTHON_PACKAGES)
def test_known_p0_regressions_absent_from_artifacts(pkg_name):
    """针对已真实发生过的两起事故钉死，不依赖上面的通用比对。"""
    zip_path = Path(pkg_mod.DIST) / f"{pkg_name}.zip"
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())

        # 事故一：Epic 97 的 _api NameError 修复没进产物
        packaged = z.read("agentboard/mcp_server.py")
        assert packaged.count(b"_api(") == 0, (
            f"{pkg_name}.zip 内的 mcp_server.py 仍含 {packaged.count(b'_api(')} 处已废弃的 "
            f"`_api(` 调用 —— Epic 97 的修复没有进入发布产物，部署后 15 个 MCP 工具会抛 "
            f"NameError。{_FIX_HINT}"
        )
        assert packaged == (_ROOT / "agentboard" / "mcp_server.py").read_bytes(), (
            f"{pkg_name}.zip 内的 mcp_server.py 与源码不一致。{_FIX_HINT}"
        )

        # 事故二：Epic 96 的 proposals 包与迁移整个缺失
        assert "agentboard/domains/proposals/__init__.py" in names, (
            f"{pkg_name}.zip 缺少 domains/proposals 包 —— 生产会 ImportError。{_FIX_HINT}"
        )
        migrations = [n for n in names if n.startswith("migrations/versions/")]
        assert any("add_proposals" in n for n in migrations), (
            f"{pkg_name}.zip 缺少 proposals 的 Alembic 迁移 —— 生产建不出提案相关的表。"
            f"{_FIX_HINT}"
        )


# ===================== 纵深防御：对产物副本复用 Epic 97 的 AST 检查 =====================

def _collect_local_bindings(fn_node: ast.AST) -> set[str]:
    """收集函数体内所有会产生局部名字绑定的标识符（与 Epic 97 护栏同逻辑）。"""
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


def _module_level_names(tree: ast.Module) -> set[str]:
    """纯静态地取模块顶层定义的名字（不 import，避免对产物副本产生副作用）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _undefined_calls(source: str, label: str) -> list[str]:
    tree = ast.parse(source)
    module_ns = _module_level_names(tree) | set(dir(builtins))
    violations: list[str] = []

    def _check_scope(scope_node: ast.AST, local_names: set[str]) -> None:
        for node in ast.walk(scope_node):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name = node.func.id
            if name in local_names or name in module_ns:
                continue
            violations.append(f"{label}:{node.lineno} 调用了未定义的 `{name}(...)`")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_scope(node, _collect_local_bindings(node))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _check_scope(sub, _collect_local_bindings(sub) | {node.name})
        else:
            _check_scope(node, set())
    return violations


@pytest.mark.parametrize("pkg_name", PYTHON_PACKAGES)
def test_no_undefined_global_calls_in_packaged_mcp_server(pkg_name):
    """产物里的 mcp_server.py 同样不许有未定义调用（纵深防御，覆盖手改 dist 的情况）。"""
    zip_path = Path(pkg_mod.DIST) / f"{pkg_name}.zip"
    with zipfile.ZipFile(zip_path) as z:
        source = z.read("agentboard/mcp_server.py").decode("utf-8")
    violations = _undefined_calls(source, f"{pkg_name}.zip::mcp_server.py")
    assert not violations, (
        "发布产物中的 mcp_server.py 存在未定义函数调用（重构改名漏改调用点）：\n  "
        + "\n  ".join(violations)
    )


# ===================== 自证：护栏在产物被篡改时必须真的失败 =====================

def test_check_mode_detects_tampering(tmp_path):
    """把产物复制到临时目录并故意做旧，`--check` 必须非零退出并指出该文件。

    没有这个用例，上面所有断言都可能因为「比对逻辑写错了」而永远为真。
    """
    sandbox = tmp_path / "repo"
    # 只复制校验所需的最小集合：脚本 + 源码 + 产物
    (sandbox / "scripts").mkdir(parents=True)
    shutil.copy2(_SCRIPT, sandbox / "scripts" / "package_windows.py")
    shutil.copytree(_ROOT / "scripts" / "deploy", sandbox / "scripts" / "deploy")
    shutil.copytree(_ROOT / "agentboard", sandbox / "agentboard",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(_ROOT / "migrations", sandbox / "migrations",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for f in ("alembic.ini", "requirements.txt"):
        shutil.copy2(_ROOT / f, sandbox / f)
    pkg = "agentboard-mcp"
    (sandbox / "dist").mkdir()
    shutil.copytree(Path(pkg_mod.DIST) / pkg, sandbox / "dist" / pkg,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(Path(pkg_mod.DIST) / f"{pkg}.zip", sandbox / "dist" / f"{pkg}.zip")

    script = sandbox / "scripts" / "package_windows.py"

    def run_check():
        return subprocess.run(
            [sys.executable, str(script), "--check", "--python-only"],
            cwd=str(sandbox), capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    # 前置：干净副本应当通过（agentboard-webapi 未复制，故只查 mcp 包）
    baseline = run_check()
    assert "✓ agentboard-mcp" in (baseline.stdout or ""), (
        f"干净副本本应通过校验，实际输出：\n{baseline.stdout}\n{baseline.stderr}"
    )

    # 篡改 1：把产物里的 mcp_server.py 退回带 bug 的旧写法
    victim = sandbox / "dist" / pkg / "agentboard" / "mcp_server.py"
    victim.write_text(
        victim.read_text(encoding="utf-8").replace("_http(", "_api(", 1),
        encoding="utf-8",
    )
    r = run_check()
    assert r.returncode != 0, "产物被改旧后 --check 仍返回 0，护栏形同虚设"
    assert "mcp_server.py" in r.stdout, f"未指出被篡改的文件：\n{r.stdout}"

    # 篡改 2：删掉一个源码里存在的文件（模拟 proposals 包缺失）
    victim.write_text((sandbox / "agentboard" / "mcp_server.py").read_text(encoding="utf-8"),
                      encoding="utf-8")
    dropped = sandbox / "dist" / pkg / "agentboard" / "domains" / "proposals" / "models.py"
    dropped.unlink()
    r = run_check()
    assert r.returncode != 0, "产物缺文件后 --check 仍返回 0"
    assert "缺少文件" in r.stdout and "proposals" in r.stdout, (
        f"未正确报告缺失文件：\n{r.stdout}"
    )


def test_check_mode_passes_on_current_artifacts():
    """当前仓库状态下 `--check --python-only` 必须退出 0（与上面的用例互为正反面）。"""
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check", "--python-only"],
        cwd=str(_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, (
        "发布产物与源码不一致：\n" + (r.stdout or "") + (r.stderr or "") + f"\n{_FIX_HINT}"
    )
