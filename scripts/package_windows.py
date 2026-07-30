#!/usr/bin/env python3
"""打包 AgentBoard 为三个 Windows 部署单元：
  - agentboard-webapi.zip  : REST API（WebAPI 服务）
  - agentboard-mcp.zip     : MCP Streamable-HTTP 服务
  - agentboard-web.zip     : Angular 静态前端（IIS 托管）
依赖 scripts/deploy/ 下的运行时脚本与 web.config。

用法：
    python scripts/package_windows.py            # 重新构建全部产物
    python scripts/package_windows.py --check    # 只校验产物是否与源码一致（不写盘）

`--check` 存在的原因（Epic 98 P0）
---------------------------------
dist/ 是**提交进 Git 的构建产物**，只有人工重跑本脚本时才会更新。历史上
Epic 97 修好了 `mcp_server.py` 的 `_api` NameError、Epic 96 新增了
`domains/proposals` 整个包，但都没有重新打包——于是仓库里的发布产物长期停留在
旧快照，谁按 dist/ 部署 Windows/IIS，谁就把「已经修好的 bug」重新装回生产。

`--check` 把这种「源码已修 / 产物仍旧」变成确定性的非零退出码，
配合 tests/test_epic98_release_artifact_parity.py 在 CI 中拦截。
"""
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
DEPLOY = os.path.join(ROOT, "scripts", "deploy")

# Web 静态来源：优先用新构建产物，回退到已拷贝的 static 目录
WEB_BUILD = os.path.join(ROOT, "frontend", "dist", "frontend", "browser")
WEB_STATIC_FALLBACK = os.path.join(ROOT, "agentboard", "web", "static")

# 不进入发布产物的目录/后缀（构建缓存，随平台与 Python 版本变化，不具备可比性）
EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo")


def log(msg):
    print("[package]", msg)


# ============================ 包清单（build 与 check 共用同一份事实） ============================

def web_source_dir():
    """与 build 完全一致的前端产物来源解析逻辑。"""
    return WEB_BUILD if os.path.isdir(WEB_BUILD) else WEB_STATIC_FALLBACK


#: name -> {trees: [(源目录绝对路径, 包内相对目录)], files: [(源文件, 包内相对目录)]}
def package_specs():
    common_py = {
        "trees": [
            (os.path.join(ROOT, "agentboard"), "agentboard"),
            (os.path.join(ROOT, "migrations"), "migrations"),
        ],
        "files": [
            (os.path.join(ROOT, "alembic.ini"), ""),
            (os.path.join(ROOT, "requirements.txt"), ""),
        ],
    }
    return {
        "agentboard-webapi": {
            "trees": list(common_py["trees"]),
            "files": common_py["files"] + [
                (os.path.join(DEPLOY, f), "") for f in
                ["run-webapi.ps1", "install-service.ps1", "make-mcp-token.py",
                 "env.webapi.example", "README.webapi.md"]
            ],
        },
        "agentboard-mcp": {
            "trees": list(common_py["trees"]),
            "files": common_py["files"] + [
                (os.path.join(DEPLOY, f), "") for f in
                ["run-mcp.ps1", "install-service.ps1", "make-mcp-token.py",
                 "env.mcp.example", "README.mcp.md"]
            ],
        },
        "agentboard-web": {
            "trees": [(web_source_dir(), "")],
            "files": [
                (os.path.join(DEPLOY, f), "") for f in
                ["web.config", "configure-api-url.ps1", "README.web.md"]
            ],
        },
    }


#: 纯 Python 服务包（parity 护栏的强校验对象；web 包内容取决于前端是否重新构建，单独处理）
PYTHON_PACKAGES = ("agentboard-webapi", "agentboard-mcp")


def iter_tree_files(src_dir):
    """产出 (源文件绝对路径, 相对 src_dir 的 posix 相对路径)，跳过构建缓存。"""
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in sorted(files):
            if fn.endswith(EXCLUDE_SUFFIXES):
                continue
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, src_dir).replace(os.sep, "/")
            yield fp, rel


def expected_manifest(spec):
    """包内相对路径(posix) -> 源文件绝对路径。这就是「这个包应该长什么样」。"""
    manifest = {}
    for src_dir, dest_prefix in spec["trees"]:
        if not os.path.isdir(src_dir):
            continue
        for fp, rel in iter_tree_files(src_dir):
            dest = f"{dest_prefix}/{rel}" if dest_prefix else rel
            manifest[dest] = fp
    for src_file, dest_prefix in spec["files"]:
        if not os.path.isfile(src_file):
            continue
        base = os.path.basename(src_file)
        dest = f"{dest_prefix}/{base}" if dest_prefix else base
        manifest[dest] = src_file
    return manifest


def actual_files(pkg_dir):
    """包目录里实际存在的文件：包内相对路径(posix) -> 绝对路径。"""
    if not os.path.isdir(pkg_dir):
        return {}
    return {rel: fp for fp, rel in iter_tree_files(pkg_dir)}


# ============================ 构建 ============================

def rmtree(p):
    if os.path.isdir(p):
        shutil.rmtree(p)


def build_pkg(name, spec):
    d = os.path.join(DIST, name)
    rmtree(d)
    manifest = expected_manifest(spec)
    for dest, src in sorted(manifest.items()):
        target = os.path.join(d, dest.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
    log(f"{name}: 复制 {len(manifest)} 个文件")
    zip_dir(d, os.path.join(DIST, f"{name}.zip"))


def zip_dir(folder, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for fp, rel in iter_tree_files(folder):
            z.write(fp, rel)
    log(f"已生成 {os.path.relpath(zip_path, ROOT)}  ({os.path.getsize(zip_path)//1024} KB)")


# ============================ 校验（--check） ============================

def check_pkg(name, spec):
    """比对期望清单与实际产物（目录 + zip）。返回人类可读的差异列表。"""
    problems = []
    pkg_dir = os.path.join(DIST, name)
    expected = expected_manifest(spec)

    if not os.path.isdir(pkg_dir):
        return [f"{name}: 产物目录不存在（dist/{name}），需重新打包"]

    actual = actual_files(pkg_dir)

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    for rel in missing:
        problems.append(f"{name}: 缺少文件 {rel}（源码里有，产物里没有）")
    for rel in extra:
        problems.append(f"{name}: 多余文件 {rel}（源码里已删除，产物里残留）")

    for rel in sorted(set(expected) & set(actual)):
        with open(expected[rel], "rb") as f1, open(actual[rel], "rb") as f2:
            if f1.read() != f2.read():
                problems.append(f"{name}: 内容不一致 {rel}（源码已变更但产物未重新打包）")

    # zip 与目录必须同步——防「重建了目录但 zip 还是旧的」
    zip_path = os.path.join(DIST, f"{name}.zip")
    if not os.path.isfile(zip_path):
        problems.append(f"{name}: 缺少 dist/{name}.zip")
    else:
        problems.extend(check_zip(name, zip_path, actual))

    return problems


def check_zip(name, zip_path, disk_files):
    """zip 内条目必须与包目录逐字节一致。"""
    problems = []
    with zipfile.ZipFile(zip_path) as z:
        names = {n for n in z.namelist() if not n.endswith("/")}
        for rel in sorted(set(disk_files) - names):
            problems.append(f"{name}.zip: 缺少条目 {rel}")
        for rel in sorted(names - set(disk_files)):
            problems.append(f"{name}.zip: 多余条目 {rel}")
        for rel in sorted(names & set(disk_files)):
            with open(disk_files[rel], "rb") as f:
                if z.read(rel) != f.read():
                    problems.append(f"{name}.zip: 条目内容与目录不一致 {rel}")
    return problems


def run_check(only_python=False):
    specs = package_specs()
    names = PYTHON_PACKAGES if only_python else tuple(specs)
    all_problems = []
    for name in names:
        problems = check_pkg(name, specs[name])
        if problems:
            log(f"✗ {name}: {len(problems)} 处不一致")
            all_problems.extend(problems)
        else:
            log(f"✓ {name}: 与源码一致")
    return all_problems


# ============================ 入口 ============================

def main():
    argv = sys.argv[1:]
    if "--check" in argv:
        problems = run_check(only_python="--python-only" in argv)
        if problems:
            print()
            log("发布产物与源码不一致，请重新执行 `python scripts/package_windows.py`：")
            for p in problems:
                print("   -", p)
            print()
            log(f"共 {len(problems)} 处差异。")
            sys.exit(1)
        log("全部发布产物与源码一致。")
        return

    if not os.path.isdir(web_source_dir()):
        log("ERROR: 未找到前端构建产物，请先执行 npm run build。")
        sys.exit(1)

    rmtree(DIST)
    os.makedirs(DIST, exist_ok=True)
    for name, spec in package_specs().items():
        log(f"打包 {name} ...")
        build_pkg(name, spec)
    log("完成。产物位于 dist/")


if __name__ == "__main__":
    main()
