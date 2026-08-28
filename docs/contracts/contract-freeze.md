# Contract freeze — AgentBoard REST API

> Tracks `openspec/changes/dual-stack-bff-restructure/design.md` §3. The
> rules below are enforced by `.github/workflows/dotnet-contract-check.yml`.

## TL;DR

FastAPI is the **single source of truth** for the AgentBoard public REST
contract. The .NET WebAPI must be 1:1 with it; every contract change
flows FastAPI → committed snapshot → regenerated C# client.

```
[FastAPI /openapi.json]  →  [dotnet/contracts/openapi-v3.json]
                              ↓ sha256 pin
                              ↓
                            [NSwag]  →  [dotnet/src/AgentBoard.Api/Clients/AgentBoardFastApiClient.cs]
                              ↓
                            [dotnet build]  →  [dotnet test]
                              ↓
                            [CI gate]
```

## What's frozen

These attributes are pinned; any change is a breaking contract change
that requires an RFC under `openspec/changes/<id>/`.

| Attribute | Rule | Source |
|---|---|---|
| URL path | Exact match (case, hyphens, version) | `dotnet/contracts/openapi-v3.json` paths |
| HTTP method | Exact match | `paths.<path>.<method>` |
| Path / query parameter names | Exact match | OpenAPI `parameters[]` |
| Request body schema | JSON keys / types / required 1:1 | OpenAPI `requestBody.content` |
| Response body schema | Same | OpenAPI `responses` |
| HTTP status codes | Business meaning 1:1 | OpenAPI `responses.<code>` |
| Error format | `{"detail": "..."}` | FastAPI default; **never** switch to ProblemDetails |
| Bearer token | `Authorization: Bearer v1.<payload>.<sig>` | `openspec/.../design.md` §7 |
| API Key format | `abk_<digest>` | Same |

**Known small breakage** (documented, not a violation):
- `WebSocket /ws/agents` → `SignalR /hubs/agents` lands in stage 2
  (S2-7). Frontend is updated in lockstep; only this one signal channel
  is affected.

## Workflow

### 1. Pull a fresh snapshot (local development)

Make sure FastAPI is running (`uvicorn agentboard.api:app --port 8000`
or `docker compose up -d api`), then:

```powershell
pwsh scripts/sync-openapi.ps1
# Writes dotnet/contracts/openapi-v3.json + openapi-v3.sha256
```

### 2. Sanity-check the diff

```powershell
git diff dotnet/contracts/openapi-v3.json
```

If the diff matches your intent, commit. If it contains a surprise
(additions you didn't make), pause and investigate before committing.

### 3. Regenerate the C# client

```powershell
pwsh scripts/generate-fastapi-client.ps1
# Writes dotnet/src/AgentBoard.Api/Clients/AgentBoardFastApiClient.cs
```

### 4. Commit snapshot + client together

```powershell
git add dotnet/contracts/ dotnet/src/AgentBoard.Api/Clients/
git commit -m "chore(contracts): refresh OpenAPI snapshot + client"
```

The CI workflow fails the build if the committed client file doesn't
match a freshly-generated one — so step 3 is non-optional.

### 5. Live drift check (CI-enforced)

The `dotnet-contract-check.yml` `live-drift` job boots the real FastAPI
stack via `docker compose up -d api` and runs the drift check against the
live `/openapi.json`. It is **fail-closed**:

- FastAPI unreachable → exit 2 → job red.
- Unapproved drift → exit 1 → job red.
- Match / fully-approved drift → exit 0.

A JSON report (`drift-report.json`, semantic summary + full path-level
diff) is uploaded as the `contract-drift-report` artifact on every run.

Local equivalent (FastAPI must be running on `AGENTBOARD_FASTAPI_URL`):

```powershell
python scripts/schema-drift-check.py --check-live --report drift-report.json
```

### 6. Approving an intentional breaking change

If a contract change is deliberate and already reviewed via an RFC, you
may land it without the `live-drift` job going red by committing an
**approved-drift** JSON alongside the refreshed snapshot **in the same PR**:

```json
// .github/contract-approved-drift.json
{
  "added":   ["paths.$.x.new_field", "paths.$.new_resource.get"],
  "removed": ["paths.$.old_resource.get"],
  "changed": ["$.components.schemas.Example.properties.mode.default"]
}
```

Then the `live-drift` step passes `--approved-drift .github/contract-approved-drift.json`;
any path diff **not** listed there still fails the gate. After the PR
merges, delete the file (the snapshot now matches and no approval is
needed). The hash check (`--check-live` off) never needs this file.

## Changing the contract

A "contract change" is any modification to:

- A request or response schema
- A URL path or HTTP method
- A query / path / header parameter
- The error envelope shape
- The Bearer token format

Process:

1. Open an RFC under `openspec/changes/<id>/breaking-contract-change.md`
   describing the impact, the migration plan, and a deprecation
   window (minimum two weeks).
2. Update FastAPI **and** the .NET implementation in lock-step, behind
   a feature flag if you can't ship both at once.
3. Bump the contract version (see `design.md` §3.3).
4. Re-run `sync-openapi.ps1` and `generate-fastapi-client.ps1`, then
   commit the regenerated artifacts.
5. Coordinate the rollout with the Angular frontend / external SDK
   consumers before removing the old field.

**Additive** changes (new optional fields, new endpoints) are also
considered contract changes — they still require a sync + regen + PR
review, but the RFC can be lighter weight (no deprecation window).

## Why this matters

- The .NET WebAPI exists to absorb external load and provide strong
  types for SDK consumers. If the .NET and FastAPI contracts drift,
  the SDK breaks for downstream users in ways the FastAPI-only
  `pytest` suite cannot catch.
- The 1:1 contract is the **only** mechanism that lets the dual-stack
  run side-by-side during the cutover (stage 2's grayscale).
- The committed `.sha256` is the cheapest possible drift alarm: any
  change to `openapi-v3.json` invalidates it, forcing a conscious
  review.
