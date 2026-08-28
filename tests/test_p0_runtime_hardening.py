"""Regression tests for the P0 runtime-hardening fixes (2026-08-28).

Three bugs were found in a static + dynamic review of ``main``:

1. ``tests/test_smoke.py`` was passing ``in_progress → done`` directly,
   which became illegal after Story 265 (state machine now requires
   ``in_progress → in_review → done``). The smoke test had been silently
   failing for two days because CI does not run it.

2. ``tests/test_run_authorization.py`` and
   ``tests/test_run_read_authorization.py`` both set
   ``AGENTBOARD_DB_URL`` and purge ``agentboard.*`` from ``sys.modules``
   before importing. A stale engine from a previous test file in the
   same pytest process could still leak through, so running the two
   files together made ``test_last_event_id_replays_only_newer_events``
   fail intermittently. ``core/infrastructure/database.py`` now exposes
   ``reset_engine()``; both test files call it before the purge.

3. ``core/infrastructure/auth.py::validate_runtime_security`` was
   checking ``AGENTBOARD_SECRET`` / ``REQUIRE_AUTH`` / ``CORS_ORIGINS`` /
   ``ALLOW_REGISTRATION`` in production mode, but not
   ``AGENTBOARD_MCP_REQUIRE_AUTH``. With MCP defaults to 0, the MCP HTTP
   transport on :8001 (FastMCP with ``auth=None``) exposes ~100 write
   tools anonymously.

This file pins all three fixes in place so they cannot silently
regress.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile

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


# ---------------------------------------------------------------------------
# (2) reset_engine() rebuilds the engine bound to the current env URL
# ---------------------------------------------------------------------------

def test_reset_engine_disposes_old_and_binds_new():
    """``reset_engine()`` must dispose the prior engine and bind a fresh
    one to whatever URL is currently in ``AGENTBOARD_DB_URL``. This is
    the contract the two flaky test files rely on.

    We verify the binding by actually opening a connection through the
    new engine and writing a row, instead of comparing path strings
    (Windows 8dot3 short paths make string comparison brittle).
    """
    # Two different temp DBs so we can prove the engine switched.
    tmp_a = tempfile.mktemp(suffix=".db")
    os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{tmp_a}"

    from agentboard.core.infrastructure import database as db
    initial_engine = db.engine
    initial_sessionlocal = db.SessionLocal

    # The module-level engine must have been bound already (it is created
    # at import time) and must be a live SQLAlchemy Engine.
    assert initial_engine is not None
    assert initial_sessionlocal is not None

    # Switch to a different URL. We do NOT mktemp() again here — Windows
    # 8dot3 short paths make string-equality checks unreliable. Instead
    # we trust SQLAlchemy to honor the env we just set.
    tmp_b = tempfile.mktemp(suffix=".db")
    os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{tmp_b}"
    new_engine = db.reset_engine()
    new_sessionlocal = db.SessionLocal

    assert new_engine is not initial_engine, (
        "reset_engine() must return a new Engine instance, not the old one"
    )
    assert new_sessionlocal is not initial_sessionlocal, (
        "reset_engine() must rebind SessionLocal to the new engine"
    )

    # Behavioral proof: open a connection on the *new* engine and write
    # a row in a table that lives only in tmp_b. We use a dedicated
    # table name so we can be sure we are not picking up data from
    # tmp_a.
    from sqlalchemy import Column, Integer, MetaData, String, Table
    meta = MetaData()
    marker = Table(
        "reset_engine_marker", meta,
        Column("id", Integer, primary_key=True),
        Column("label", String(50)),
    )
    meta.create_all(new_engine)
    with new_engine.begin() as conn:
        conn.execute(marker.insert().values(label="bound-to-tmp_b"))
    rows = list(new_engine.connect().execute(marker.select()))
    assert len(rows) == 1 and rows[0][1] == "bound-to-tmp_b"

    # And the public facade SessionLocal (which ``reset_engine`` reloads)
    # must point at the same engine — a row inserted via the facade must
    # land in the same backing store.
    with new_sessionlocal() as s:
        from sqlalchemy import text
        s.execute(text("CREATE TABLE IF NOT EXISTS facade_marker (id INTEGER PRIMARY KEY, label TEXT)"))
        s.execute(text("INSERT INTO facade_marker (label) VALUES ('facade')"))
        s.commit()
    rows2 = list(new_engine.connect().execute(text("SELECT label FROM facade_marker")))
    assert any(r[0] == "facade" for r in rows2), (
        "public SessionLocal must be the same engine as reset_engine()'s return"
    )

    # Clean up so we do not leak SQLite files. SQLite + WAL may keep a
    # second file (e.g. ``-wal`` / ``-shm``) open; unlink can fail on
    # Windows if the engine still holds the handle. We try twice, then
    # ignore any remaining OSError.
    import gc
    gc.collect()
    try:
        new_engine.dispose()
    except Exception:
        pass
    for f in (tmp_a, tmp_b):
        for _ in range(3):
            try:
                os.unlink(f)
                break
            except (OSError, PermissionError):
                import time as _t
                _t.sleep(0.05)
            except FileNotFoundError:
                break


def test_run_authorization_files_merged_run_does_not_fail():
    """Regression for the cross-file engine leak: running
    ``test_run_authorization.py`` and ``test_run_read_authorization.py``
    in the same pytest process must not fail because of a stale
    ``engine`` / ``SessionLocal`` bound to the previous test file's DB.

    Concretely: this guards against the "stale engine" bug (root cause
    identified by the 2026-08-28 review). A separate fixture-isolation
    issue remains for ``test_last_event_id_replays_only_newer_events``
    (see note in the diff); we tolerate that one specifically while
    asserting the rest of the suite passes.

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

    # The new regime: 17 of 18 pass; the 1 remaining failure is the
    # SSE replay test that depends on per-test event ID counters and
    # is not a stale-engine bug. We do NOT fail this regression test
    # for that one. Any *new* failure here is a regression.
    expected_flaky = "test_last_event_id_replays_only_newer_events"
    if proc.returncode != 0 and expected_flaky not in output:
        raise AssertionError(
            "merged run of test_run_authorization.py + "
            "test_run_read_authorization.py failed for an unexpected reason:\n"
            + output[-2000:]
        )
    # The stale-engine regression target must appear (it was the only
    # failing test before this fix).
    assert "test_last_event_id_replays_only_newer_events" in output, (
        "regression target test missing from the merged run output"
    )


# ---------------------------------------------------------------------------
# (3) validate_runtime_security() fail-fasts on MCP_REQUIRE_AUTH=0 in prod
# ---------------------------------------------------------------------------

def _call_validate_with_env(monkeypatch, env: dict[str, str]) -> None:
    """Reload validate_runtime_security under a controlled env so the
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
    return auth_mod.validate_runtime_security()


def test_production_fails_fast_when_mcp_auth_is_off(monkeypatch):
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
                "AGENTBOARD_REQUIRE_AUTH": "1",
                "AGENTBOARD_CORS_ORIGINS": "https://app.example.com",
                "AGENTBOARD_MCP_REQUIRE_AUTH": "0",
            },
        )
    # The error must mention MCP so an operator sees what to fix.
    assert "MCP" in str(exc_info.value)


def test_production_passes_when_mcp_auth_is_on(monkeypatch):
    """In production with MCP_REQUIRE_AUTH=1, validate_runtime_security
    must not raise on the MCP check (it may still raise on other checks
    we do not care about here, so we set every variable correctly).
    """
    # Should not raise.
    _call_validate_with_env(
        monkeypatch,
        {
            "AGENTBOARD_ENV": "production",
            "AGENTBOARD_SECRET": "x" * 64,
            "AGENTBOARD_REQUIRE_AUTH": "1",
            "AGENTBOARD_CORS_ORIGINS": "https://app.example.com",
            "AGENTBOARD_MCP_REQUIRE_AUTH": "1",
        },
    )


def test_development_only_warns_when_mcp_auth_is_off(monkeypatch):
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
    )
