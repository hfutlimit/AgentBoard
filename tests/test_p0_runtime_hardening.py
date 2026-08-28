"""Regression tests for the P0 runtime-hardening fixes (2026-08-28).

Runtime bugs found in a static + dynamic review of ``main``:

1. ``tests/test_smoke.py`` was passing ``in_progress → done`` directly,
   which became illegal after Story 265 (state machine now requires
   ``in_progress → in_review → done``). The smoke test had been silently
   failing for two days because CI does not run it.

2. SSE replay used the router module's global ``SessionLocal`` after closing
   the request session. When independently isolated test modules were collected
   together, the replay generator could therefore read a different database.
   Replay/live sessions now reuse the request session's database bind.

3. The independent MCP process had no production startup validation.  Its
   process boundary now requires both a strong secret and transport auth,
   without coupling REST API startup to MCP-only configuration.

This file pins the focused runtime fixes in place so they cannot silently
regress.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest


# ---------------------------------------------------------------------------
# (1) smoke test follows the new state-machine path
# ---------------------------------------------------------------------------

def test_smoke_uses_in_review_path_not_illegal_direct_done():
    """The smoke test must not attempt the removed ``in_progress → done``
    transition. It must go through ``in_review`` instead.

    This is a *textual* guard: we grep the file so that if anyone
    re-introduces the direct path, this test fails before the
    (slow) smoke test ever runs.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    smoke = os.path.join(repo_root, "tests", "test_smoke.py")
    with open(smoke, "r", encoding="utf-8") as f:
        src = f.read()

    # The illegal pair is only present if someone re-introduces
    # ``status: "in_progress"`` followed by ``status: "done"`` in the
    # status-flow block. We assert at least one in_review step between
    # them.
    assert '"status": "in_review"' in src, (
        "smoke test must transition through in_review before done"
    )
    # Direct put(done) right after put(in_progress) without an
    # intervening in_review is the regression we are guarding against.
    forbidden_pair = ('"status": "in_progress"', '"status": "done"')
    last_in_progress = src.rfind(forbidden_pair[0])
    last_done = src.rfind(forbidden_pair[1])
    if last_in_progress != -1 and last_done != -1 and last_done > last_in_progress:
        # Both appear, but check whether an in_review sits between them
        # anywhere in the same status-flow block (best-effort: scan the
        # 600 chars after the in_progress line).
        window = src[last_in_progress:last_done]
        assert '"status": "in_review"' in window, (
            "smoke test transitions in_progress → done without going "
            "through in_review (Story 265 removed the direct edge)"
        )


def test_run_authorization_files_merged_run_does_not_fail():
    """Regression for the cross-file engine leak: running
    ``test_run_authorization.py`` and ``test_run_read_authorization.py``
    in the same pytest process must not fail because of a stale
    ``engine`` / ``SessionLocal`` bound to the previous test file's DB.

    Spawns a subprocess so it does not interfere with this test session's
    own engine state.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env["AGENTBOARD_ENV"] = "development"
    env["AGENTBOARD_REQUIRE_AUTH"] = "0"
    src = os.path.join(repo_root, "src", "backend-fastapi")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "tests/test_run_authorization.py",
            "tests/test_run_read_authorization.py",
            "-q", "--tb=line", "-p", "no:cacheprovider",
        ],
        cwd=repo_root, env=env,
        capture_output=True, text=True, timeout=180,
    )
    output = (proc.stdout or "") + (proc.stderr or "")

    if proc.returncode != 0:
        raise AssertionError(
            "merged run of test_run_authorization.py + "
            "test_run_read_authorization.py failed for an unexpected reason:\n"
            + output[-2000:]
        )


# ---------------------------------------------------------------------------
# (3) the independent MCP process fail-fasts on unauthenticated production
# ---------------------------------------------------------------------------

def _call_validate_with_env(
    monkeypatch, env: dict[str, str], validator: str = "api",
) -> None:
    """Reload the security module under a controlled env so the
    function reads the new env vars at call time. ``monkeypatch`` is
    used so other tests are not affected.
    """
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    # Re-import the module so the module-level os.getenv calls pick up
    # the new env. validate_runtime_security() reads env at call time so
    # a fresh import also re-evaluates the default-value constant
    # _DEV_INSECURE_DEFAULTS, which is fine — the function does not
    # depend on a captured default.
    import agentboard.core.infrastructure.auth as auth_mod
    importlib.reload(auth_mod)
    if validator == "mcp":
        return auth_mod.validate_mcp_runtime_security()
    return auth_mod.validate_runtime_security()


def test_production_mcp_fails_fast_when_auth_is_off(monkeypatch):
    """In production, MCP_REQUIRE_AUTH=0 must raise RuntimeError.

    The MCP HTTP transport on :8001 with ``auth=None`` exposes ~100
    write tools anonymously; production must not boot under that.
    """
    with pytest.raises(RuntimeError) as exc_info:
        _call_validate_with_env(
            monkeypatch,
            {
                "AGENTBOARD_ENV": "production",
                "AGENTBOARD_SECRET": "x" * 64,  # 32+ bytes, satisfies the SECRET check
                "AGENTBOARD_MCP_REQUIRE_AUTH": "0",
            },
            validator="mcp",
        )
    # The error must mention MCP so an operator sees what to fix.
    assert "MCP" in str(exc_info.value)


def test_production_mcp_passes_when_auth_is_on(monkeypatch):
    # Should not raise.
    _call_validate_with_env(
        monkeypatch,
        {
            "AGENTBOARD_ENV": "production",
            "AGENTBOARD_SECRET": "x" * 64,
            "AGENTBOARD_MCP_REQUIRE_AUTH": "1",
        },
        validator="mcp",
    )


def test_production_api_does_not_require_mcp_process_config(monkeypatch):
    """REST API startup must not depend on a separate MCP container's flag."""
    _call_validate_with_env(
        monkeypatch,
        {
            "AGENTBOARD_ENV": "production",
            "AGENTBOARD_SECRET": "x" * 64,
            "AGENTBOARD_REQUIRE_AUTH": "1",
            "AGENTBOARD_CORS_ORIGINS": "https://app.example.com",
            "AGENTBOARD_MCP_REQUIRE_AUTH": "0",
        },
    )


def test_mcp_server_import_invokes_production_security_boundary():
    """The validator must be wired into the real independent MCP entrypoint."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env.update({
        "AGENTBOARD_ENV": "production",
        "AGENTBOARD_SECRET": "x" * 64,
        "AGENTBOARD_MCP_REQUIRE_AUTH": "0",
    })
    src = os.path.join(repo_root, "src", "backend-fastapi")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", "import agentboard.mcp_server"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode != 0
    assert "AGENTBOARD_MCP_REQUIRE_AUTH=1" in output


def test_development_mcp_only_warns_when_auth_is_off(monkeypatch):
    """In development, MCP_REQUIRE_AUTH=0 must NOT raise. The existing
    dev-mode contract is "warn, do not block".
    """
    # Should not raise.
    _call_validate_with_env(
        monkeypatch,
        {
            "AGENTBOARD_ENV": "development",
            "AGENTBOARD_MCP_REQUIRE_AUTH": "0",
        },
        validator="mcp",
    )
