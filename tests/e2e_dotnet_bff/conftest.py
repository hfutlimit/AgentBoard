"""pytest 夹具：针对「新加的后端」.NET BFF（AgentBoard.Api，双栈重构 Stage 0）的 e2e 测试基础设施。

设计目标
--------
- 复用 pytest.ini 的 `e2e` / `slow` marker：默认 `pytest` 不跑本目录（需 `-m e2e`），
  与既有 unit / Playwright e2e 共存且不互相干扰。
- 两种运行模式，由环境变量切换：
  1. 连已有实例（默认）：读 `AGENTBOARD_BFF_URL`（默认 http://127.0.0.1:18099），
     不可达则整目录 **skip**（不报错，便于在只跑前端的 CI 里安全收集）。
  2. 自拉起（E2E_SPINUP=1）：用预构建的 AgentBoard.Api.dll 以临时 SQLite + Development
     环境独立启动 BFF（无需 MariaDB / FastAPI），待 /api/health 就绪后运行用例，结束自动 tear down。
- 所有断言基于 2026-08-22 实测响应，非猜测。

可观测的「新后端」行为（HTTP 边界可测，对应 Story #313 / S0-7 观测性）：
- RequestIdMiddleware：回显 X-Request-Id 到响应头（跨栈关联键）。
- TraceContextMiddleware + W3C：入站 traceparent 被「续接」（同 trace-id、新 span-id），
  并回显到响应头。
- /api/health 形状 {status, database, version, timestamp}（S0-5 契约）。
- /api/meta 枚举值与 FastAPI enums.py 一致（#5 / #311 契约）。
- /openapi/v1.json 暴露 paths（Development；#4 契约冻结的 .NET 侧基线）。
"""
from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest

# tests/e2e_dotnet_bff/conftest.py -> parents[2] == repo root (AgentBoard)
ROOT = Path(__file__).resolve().parents[2]
_BFF_PROJECT = ROOT / "dotnet" / "src" / "AgentBoard.Api"
_DEFAULT_URL = "http://127.0.0.1:18099"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_bff_dll() -> str | None:
    """定位预构建的 AgentBoard.Api.dll（优先 Release，其次 Debug，最后任意）。"""
    base = _BFF_PROJECT / "bin"
    if not base.exists():
        return None
    candidates = [p for p in base.rglob("AgentBoard.Api.dll")]
    if not candidates:
        return None
    for c in candidates:
        if "Release" in c.parts:
            return str(c)
    for c in candidates:
        if "Debug" in c.parts:
            return str(c)
    return str(candidates[0])


def _build_bff() -> None:
    """兜底：若未构建则先 Release 构建（仅 E2E_SPINUP 且 dll 缺失时触发）。"""
    subprocess.run(
        ["dotnet", "build", str(_BFF_PROJECT), "-c", "Release", "--nologo"],
        cwd=str(ROOT), check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _spinup_bff() -> tuple[subprocess.Popen, str, str | None, object]:
    """用预构建 dll 独立拉起 BFF（临时 SQLite + Development）。返回 (proc, base_url, tmp_db, log_file)。"""
    port = _free_port()
    tmp_db = tempfile.mktemp(suffix=".db", prefix="ab-bff-e2e-")
    dll = _find_bff_dll()
    if dll is None:
        _build_bff()
        dll = _find_bff_dll()
    if dll is None:
        raise RuntimeError("未找到 AgentBoard.Api.dll，且构建失败")

    env = os.environ.copy()
    env.update({
        "ASPNETCORE_ENVIRONMENT": "Development",
        "AGENTBOARD_DATABASE__PROVIDER": "sqlite",
        "AGENTBOARD_DATABASE__CONNECTIONSTRING": f"Data Source={tmp_db}",
        "AGENTBOARD_DOTNET_PORT": str(port),
        "AGENTBOARD_SECRET": "e2e-test-secret-0123456789abcdef",
        "AGENTBOARD_REQUIRE_AUTH": "0",
        # 内部 FastAPI 地址在 Development 下未实际使用（当前控制器不调 FastAPI），
        # 给个占位避免解析告警。
        "AGENTBOARD_FASTAPI__INTERNALURL": "http://127.0.0.1:18000",
    })
    log_path = Path(tempfile.mktemp(suffix=".log", prefix="ab-bff-e2e-"))
    log_file = open(log_path, "w", encoding="utf-8")
    # 直接运行预构建 dll：content root 即 dll 目录（含 appsettings.Development.json）。
    proc = subprocess.Popen(
        ["dotnet", dll],
        cwd=str(ROOT), env=env,
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    print(f"[e2e-bff] dotnet {dll} started pid={proc.pid} port={port} log={log_path}")
    return proc, base_url, tmp_db, log_file


def _wait_health(base_url: str, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = httpx.get(base_url + "/api/health", timeout=2)
            if r.status_code == 200:
                return
            last = r.status_code
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__
        time.sleep(1.0)
    raise RuntimeError(f"BFF 在 {base_url} 启动超时（最后状态: {last}）")


@pytest.fixture(scope="session")
def bff_client():
    """返回指向运行中 BFF 的 httpx.Client；不可达时 skip。"""
    if os.environ.get("E2E_SPINUP") == "1":
        proc, base_url, tmp_db, log_file = _spinup_bff()
        # 冷启动最多等 90s；自拉起场景必须给足 dotnet 启动时间。
        health_timeout = 90.0
    else:
        proc, tmp_db, log_file = None, None, None
        base_url = os.environ.get("AGENTBOARD_BFF_URL", _DEFAULT_URL)
        # 连已有实例：仅做存活探测，5s 不够就直接 skip，避免无谓等待。
        health_timeout = 5.0

    try:
        _wait_health(base_url, timeout=health_timeout)
    except Exception as e:  # noqa: BLE001
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
            if log_file is not None:
                log_file.close()
        pytest.skip(
            f"BFF 不可达（{base_url}）。本地可设 E2E_SPINUP=1 自动拉起，"
            f"或将 AGENTBOARD_BFF_URL 指向运行中的实例。原因: {e}"
        )

    client = httpx.Client(base_url=base_url, timeout=20)
    yield client
    client.close()
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        if log_file is not None:
            log_file.close()
        if tmp_db and os.path.exists(tmp_db):
            try:
                os.remove(tmp_db)
            except OSError:
                pass


@pytest.fixture(scope="session")
def bff_base_url(bff_client: httpx.Client) -> str:
    return str(bff_client.base_url).rstrip("/")
