# Change: Enterprise UI redesign

## Why

The existing interface feels closer to a personal productivity tool than an enterprise project workspace. Visual hierarchy, information density, navigation structure and responsive behavior need a consistent product-level system.

## What Changes

- Replace the purple consumer-style visual language with a restrained blue/slate enterprise palette.
- Introduce a dark workspace sidebar, clearer product header and denser content canvas.
- Standardize cards, KPI panels, forms, tabs, tables, Kanban columns, dialogs and status treatments.
- Align the Proposal workbench with the rest of the application.
- Improve narrow-screen behavior by closing navigation by default and simplifying the top bar.
- Make the Web service prefer the current Angular build output over the legacy static fallback.

## Impact

- Global visual treatment changes across every Angular route.
- No REST API, data model or domain behavior changes.
- Angular component-style budget increases to match the existing single-component architecture.
