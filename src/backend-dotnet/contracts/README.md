# OpenAPI contract snapshots

This directory is the **single source of truth** for the AgentBoard REST
contract. The .NET BFF is contract-compatible with FastAPI, so:

- **`openapi-v3.json`** — snapshot of `GET /openapi.json` from the running
  FastAPI service. Updated by `scripts/sync-openapi.ps1`.
- **`openapi-v3.sha256`** — SHA-256 hash of the snapshot, used by CI to
  detect drift.

## How to update

```powershell
# 1. Make sure FastAPI is running (uvicorn or docker compose up api)
# 2. Run the sync script
pwsh scripts/sync-openapi.ps1

# 3. Commit the regenerated openapi-v3.json + openapi-v3.sha256
git add src/backend-dotnet/contracts/
git commit -m "chore(contracts): refresh OpenAPI snapshot from FastAPI"
```

## Drift detection

CI runs `scripts/schema-drift-check.py` on every PR. Any unintentional
divergence between the committed snapshot and the live FastAPI `/openapi.json`
fails the pipeline and blocks the merge.

When an intentional contract change is needed:

1. Open a proposal under `openspec/changes/<id>/`.
2. Update FastAPI **and** the .NET implementation in lock-step.
3. Bump the contract version (see `proposal.md`).
4. Re-run `sync-openapi.ps1` and commit the new snapshot.
