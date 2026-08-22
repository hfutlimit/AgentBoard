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


def _mix(fg_hex: str, percent: float, bg_hex: str) -> str:
    fg = [int(fg_hex[i:i+2], 16) for i in (1, 3, 5)]
    bg = [int(bg_hex[i:i+2], 16) for i in (1, 3, 5)]
    r = fg[0] * percent + bg[0] * (1 - percent)
    g = fg[1] * percent + bg[1] * (1 - percent)
    b = fg[2] * percent + bg[2] * (1 - percent)
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def _variables(selector: str) -> dict[str, str]:
    match = re.search(rf"{re.escape(selector)}\s*\{{(.*?)\n\}}", STYLES, re.DOTALL)
    assert match, f"missing CSS block: {selector}"
    # Parse all variables, take the last defined (which overrides fallback)
    tokens = {}
    for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", match.group(1)):
        tokens[k] = v.strip()
    return tokens


def resolve_color(tokens: dict[str, str], val: str, surface_hex: str) -> str:
    if val.startswith("var("):
        m = re.search(r"var\((--[\w-]+)\)", val)
        if m and m.group(1) in tokens:
            val = tokens[m.group(1)]

    if re.match(r"^#[0-9a-fA-F]{6}$", val):
        return val

    mix_match = re.search(r"color-mix\(\s*in\s+srgb\s*,\s*(.*?)\s+(\d+)%\s*,\s*transparent\s*\)", val)
    if mix_match:
        base_color = mix_match.group(1).strip()
        if base_color.startswith("var("):
            v_match = re.search(r"var\((--[\w-]+)\)", base_color)
            if v_match and v_match.group(1) in tokens:
                base_color = tokens[v_match.group(1)]
        if re.match(r"^#[0-9a-fA-F]{6}$", base_color):
            percent = float(mix_match.group(2)) / 100.0
            return _mix(base_color, percent, surface_hex)

    return val


def test_light_theme_text_tokens_meet_wcag_aa() -> None:
    tokens = _variables(":root")
    bg = "#ffffff"  # Light theme surface
    pairs = [
        (resolve_color(tokens, tokens["--ink-muted"], bg), bg),
        (resolve_color(tokens, tokens["--color-text-tertiary"], bg), bg),
        (resolve_color(tokens, tokens["--sidebar-label-text"], bg), resolve_color(tokens, tokens["--navy"], bg)),
        ("#166534", "#e7f6ec"),
        (resolve_color(tokens, tokens["--info"], bg), resolve_color(tokens, tokens["--info-soft"], bg)),
        (resolve_color(tokens, tokens["--danger"], bg), resolve_color(tokens, tokens["--danger-soft"], bg)),
        (resolve_color(tokens, tokens["--priority-high-text"], bg), resolve_color(tokens, tokens["--priority-high-bg"], bg)),
        (resolve_color(tokens, tokens["--priority-low-text"], bg), resolve_color(tokens, tokens["--priority-low-bg"], bg)),
        (resolve_color(tokens, tokens["--priority-lowest-text"], bg), resolve_color(tokens, tokens["--priority-lowest-bg"], bg)),
        (resolve_color(tokens, tokens["--success"], bg), resolve_color(tokens, tokens["--success-soft"], bg)),
        (resolve_color(tokens, tokens["--color-warning-text"], bg), resolve_color(tokens, tokens["--color-warning-soft"], bg)),
    ]
    for foreground, background in pairs:
        assert _contrast(foreground, background) >= 4.5, f"Contrast {foreground} on {background} failed"


def test_dark_theme_and_component_overrides_meet_wcag_aa() -> None:
    tokens = _variables('[data-theme="dark"]')
    # Must fallback to :root if dark theme doesn't define it
    root_tokens = _variables(":root")
    merged_tokens = {**root_tokens, **tokens}
    bg = resolve_color(merged_tokens, merged_tokens["--surface"], "#1e293b")

    pairs = [
        (resolve_color(merged_tokens, merged_tokens["--ink-muted"], bg), resolve_color(merged_tokens, merged_tokens["--canvas"], bg)),
        (resolve_color(merged_tokens, merged_tokens["--sidebar-label-text"], bg), resolve_color(merged_tokens, merged_tokens["--navy"], bg)),
        (resolve_color(merged_tokens, merged_tokens["--success"], bg), resolve_color(merged_tokens, merged_tokens["--success-soft"], bg)),
        (resolve_color(merged_tokens, merged_tokens["--danger"], bg), resolve_color(merged_tokens, merged_tokens["--danger-soft"], bg)),
        (resolve_color(merged_tokens, merged_tokens["--info"], bg), resolve_color(merged_tokens, merged_tokens["--info-soft"], bg)),
        (resolve_color(merged_tokens, merged_tokens["--violet"], bg), resolve_color(merged_tokens, merged_tokens["--violet-soft"], bg)),
        ("#93b7ff", "#131c2c"),
        ("#93b7ff", "#142d55"),
        ("#6ee7b7", "#064e3b"),
    ]
    for foreground, background in pairs:
        assert _contrast(foreground, background) >= 4.5, f"Contrast {foreground} on {background} failed"

    home_css = (ROOT / "frontend/src/app/home-shell/home-shell.css").read_text(encoding="utf-8")
    overview_css = (ROOT / "frontend/src/app/overview-tab/overview-tab.css").read_text(encoding="utf-8")
    app_css = (ROOT / "frontend/src/app/app.css").read_text(encoding="utf-8")
    assert "[data-theme='dark'] .hs-tab-button.active" in home_css
    assert "[data-theme='dark'] .overview-epic-tag.done" in overview_css
    assert "[data-theme='dark'] .settings-nav-item.active" in app_css
