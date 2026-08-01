# Enterprise UI capability

## Requirement: Consistent enterprise workspace

The Web application SHALL use a consistent enterprise visual system across dashboard, project, work-item, administration, settings and Proposal routes.

#### Scenario: Desktop workspace

- **WHEN** a user opens the application on a desktop viewport
- **THEN** the application presents a persistent workspace navigation rail, product header and fluid data canvas with consistent controls and surfaces

#### Scenario: Mobile workspace

- **WHEN** the viewport is 800 px wide or narrower
- **THEN** navigation is off-canvas and closed by default, content becomes single-column and the top bar remains usable without wrapping

## Requirement: Current Angular assets

The Web host SHALL serve the current Angular production build when it is present and SHALL use the legacy static directory only as a fallback.
