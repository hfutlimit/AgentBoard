# EF Core migrations

EF Core migrations live under `Infrastructure/Persistence/Migrations/` once
S0-2 lands. They are generated but **never auto-applied to production**.

## Workflow

1. **Generate locally**

   ```powershell
   dotnet ef migrations add <Name> \
     --project src/AgentBoard.Infrastructure \
     --startup-project src/AgentBoard.Api
   ```

2. **Export to SQL for review**

   ```powershell
   dotnet ef migrations script \
     --project src/AgentBoard.Infrastructure \
     --startup-project src/AgentBoard.Api \
     --output migrations/sql/<timestamp>_<Name>.sql
   ```

3. **Hand to the Alembic operator**

   The Python operator reviews the SQL and applies it via Alembic so the
`src/backend-dotnet/` migrations stay in lock-step with the FastAPI schema. **Do
   not** let EF Core auto-apply migrations to a shared database.

4. **CI guard**

   `dotnet ef migrations has-pending-model-changes` must return no diff
   against the committed SQL files. If it does, regenerate and re-export.
