"""Static layout contracts for the large-screen workspace redesign.

These tests intentionally inspect source CSS only.  They do not require a
frontend build or a running browser, so they can guard the responsive geometry
before a visual regression reaches the application bundle.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP_CSS = ROOT / "src/frontend/src/app/app-features.css"
HOME_CSS = ROOT / "src/frontend/src/app/home-shell/home-shell.css"
ROUTE_CSS = ROOT / "src/frontend/src/app/project-workspace-route/project-workspace-route.css"
SHELL_CSS = ROOT / "src/frontend/src/app/project-workspace-shell/project-workspace-shell.css"


def _block(css: str, selector: str) -> str:
    """Return a simple CSS rule body, keeping assertions readable."""
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css, re.S)
    assert match, f"missing CSS selector: {selector}"
    return match.group(1)


def _media(css: str, width: int) -> str:
    """Return the first min-width media block for a breakpoint."""
    match = re.search(
        rf"@media\s*\(min-width:\s*{width}px\)\s*\{{(.*?)\n\}}",
        css,
        re.S,
    )
    assert match, f"missing min-width media rule: {width}px"
    return match.group(1)


def test_home_shell_large_screen_geometry_contract():
    css = HOME_CSS.read_text(encoding="utf-8")

    assert re.search(r"\.home-main-v7\s*\{[^}]*\bmax-width\s*:\s*none\s*;", css, re.S)
    assert re.search(r"\bwidth\s*:\s*min\(1370px\s*,\s*100%\)", _block(css, ".home-content-v7"))

    # Keep explicit 1600/2200 tiers so 1440 is the baseline rather than the
    # only tuned viewport and ultra-wide screens do not fall back to a narrow
    # fixed canvas.
    assert re.search(r"\.home-content-v7[^}]*width\s*:\s*min\(1600px\s*,\s*100%\)", _media(css, 1600), re.S)
    assert re.search(r"\.home-content-v7[^}]*width\s*:\s*min\(1840px\s*,\s*100%\)", _media(css, 2200), re.S)


def test_app_shell_modes_remove_outer_padding_and_width_cap():
    css = APP_CSS.read_text(encoding="utf-8")

    for mode in ("home-shell-mode", "project-workspace-mode"):
        body = _block(css, f"main#app.{mode}")
        assert re.search(r"\bpadding\s*:\s*0\s*;", body), mode
        child = _block(css, f"main#app.{mode} > *")
        assert re.search(r"\bmax-width\s*:\s*none\s*;", child), mode


def test_workspace_route_content_width_contract():
    css = ROUTE_CSS.read_text(encoding="utf-8")

    assert re.search(r"\bwidth\s*:\s*min\(1500px\s*,\s*100%\)", _block(css, ".project-workspace-route-content"))
    wide = _media(css, 1800)
    assert ".project-workspace-route-content" in wide
    assert re.search(r"width\s*:\s*min\(1680px\s*,\s*100%\)", wide)


def test_project_shell_sidebar_width_tiers_contract():
    css = SHELL_CSS.read_text(encoding="utf-8")

    base = _block(css, ".project-workspace-shell")
    assert re.search(r"grid-template-columns\s*:\s*218px\s+minmax\(0,\s*1fr\)", base)

    wide = _media(css, 1800)
    assert re.search(r"grid-template-columns\s*:\s*236px\s+minmax\(0,\s*1fr\)", wide)

    # The narrow contract is expressed by the max-width rule and is checked
    # directly to avoid coupling this test to the order of media declarations.
    assert re.search(
        r"@media\s*\(max-width:\s*1160px\)\s*\{[^}]*\.project-workspace-shell[^}]*"
        r"grid-template-columns\s*:\s*72px\s+minmax\(0,\s*1fr\)",
        css,
        re.S,
    ), "missing 72px narrow project sidebar tier"
