"""Regression checks for the text/background pairs fixed by Story 320."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STYLES = (ROOT / "frontend/src/styles.css").read_text(encoding="utf-8")


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_luminance(foreground), _luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _variables(selector: str) -> dict[str, str]:
    match = re.search(rf"{re.escape(selector)}\s*\{{(.*?)\n\}}", STYLES, re.DOTALL)
    assert match, f"missing CSS block: {selector}"
    return dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})", match.group(1)))


def test_light_theme_text_tokens_meet_wcag_aa() -> None:
    tokens = _variables(":root")
    pairs = [
        (tokens["--ink-muted"], "#ffffff"),
        (tokens["--color-text-tertiary"], "#ffffff"),
        (tokens["--sidebar-label-text"], tokens["--navy"]),
        ("#166534", "#e7f6ec"),
        (tokens["--info"], tokens["--info-soft"]),
        (tokens["--danger"], tokens["--danger-soft"]),
        (tokens["--priority-high-text"], tokens["--priority-high-bg"]),
        (tokens["--priority-low-text"], tokens["--priority-low-bg"]),
        (tokens["--priority-lowest-text"], tokens["--priority-lowest-bg"]),
    ]
    assert all(_contrast(foreground, background) >= 4.5 for foreground, background in pairs)


def test_dark_theme_and_component_overrides_meet_wcag_aa() -> None:
    tokens = _variables('[data-theme="dark"]')
    pairs = [
        (tokens["--ink-muted"], tokens["--canvas"]),
        (tokens["--sidebar-label-text"], tokens["--navy"]),
        (tokens["--success"], tokens["--success-soft"]),
        (tokens["--danger"], tokens["--danger-soft"]),
        (tokens["--info"], tokens["--info-soft"]),
        (tokens["--violet"], tokens["--violet-soft"]),
        ("#93b7ff", "#131c2c"),
        ("#93b7ff", "#142d55"),
        ("#6ee7b7", "#064e3b"),
    ]
    assert all(_contrast(foreground, background) >= 4.5 for foreground, background in pairs)

    home_css = (ROOT / "frontend/src/app/home-shell/home-shell.css").read_text(encoding="utf-8")
    overview_css = (ROOT / "frontend/src/app/overview-tab/overview-tab.css").read_text(encoding="utf-8")
    app_css = (ROOT / "frontend/src/app/app.css").read_text(encoding="utf-8")
    assert "[data-theme='dark'] .hs-tab-button.active" in home_css
    assert "[data-theme='dark'] .overview-epic-tag.done" in overview_css
    assert "[data-theme='dark'] .settings-nav-item.active" in app_css
