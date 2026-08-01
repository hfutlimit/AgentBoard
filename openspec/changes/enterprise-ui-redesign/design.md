# Design

## Visual direction

The interface uses a navy navigation rail, white data surfaces, cool gray page backgrounds and blue as the primary action color. Borders and spacing carry hierarchy; gradients and large shadows are limited to the dashboard hero.

## Layout

- Desktop: 64 px product header, 272 px sticky workspace navigation and a fluid content canvas capped at 1480 px.
- Tablet: narrower navigation and two-column KPI layout.
- Mobile: off-canvas navigation closed by default, single-column content and a reduced one-line top bar.

## Components

Controls use 6 px radii, cards use 9 px radii and low-elevation borders. KPI cards use a narrow semantic accent rather than colored surfaces. Kanban columns use neutral backgrounds so work item states remain the visual focus.

## Delivery

The Docker image already produces Angular assets under `frontend/dist/frontend/browser`. The Web host selects that directory when present, while retaining `agentboard/web/static` for legacy/native fallback environments.
