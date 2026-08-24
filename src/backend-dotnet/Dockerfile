# syntax=docker/dockerfile:1.7
#
# AgentBoard.Api — multi-stage build for the .NET 10 WebAPI.
#
# Build:  docker build -f src/backend-dotnet/Dockerfile -t agentboard-api-dotnet .
# Run:    docker run --rm -p 18099:8080 -e AGENTBOARD_ENV=development agentboard-api-dotnet
#
# Build context: repository root (the src/backend-dotnet/ folder is reached via the
# relative path in the `COPY` instructions). The `.dockerignore.dotnet`
# file at the repository root excludes Python / node_modules artefacts.

# ---------- Stage 1: restore (cached independently) ----------
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS restore
WORKDIR /src

# Copy the solution and project files first so NuGet restore can be cached
# independently of source code changes.
COPY src/backend-dotnet/AgentBoard.slnx src/backend-dotnet/AgentBoard.slnx
COPY src/backend-dotnet/Directory.Build.props src/backend-dotnet/Directory.Build.props
COPY src/backend-dotnet/global.json src/backend-dotnet/global.json
COPY src/backend-dotnet/src/AgentBoard.Api/AgentBoard.Api.csproj src/backend-dotnet/src/AgentBoard.Api/AgentBoard.Api.csproj
COPY src/backend-dotnet/src/AgentBoard.Application/AgentBoard.Application.csproj src/backend-dotnet/src/AgentBoard.Application/AgentBoard.Application.csproj
COPY src/backend-dotnet/src/AgentBoard.Domain/AgentBoard.Domain.csproj src/backend-dotnet/src/AgentBoard.Domain/AgentBoard.Domain.csproj
COPY src/backend-dotnet/src/AgentBoard.Infrastructure/AgentBoard.Infrastructure.csproj src/backend-dotnet/src/AgentBoard.Infrastructure/AgentBoard.Infrastructure.csproj
COPY src/backend-dotnet/tests/AgentBoard.Api.Tests/AgentBoard.Api.Tests.csproj src/backend-dotnet/tests/AgentBoard.Api.Tests/AgentBoard.Api.Tests.csproj
COPY src/backend-dotnet/tests/AgentBoard.Infrastructure.Tests/AgentBoard.Infrastructure.Tests.csproj src/backend-dotnet/tests/AgentBoard.Infrastructure.Tests/AgentBoard.Infrastructure.Tests.csproj

RUN dotnet restore src/backend-dotnet/AgentBoard.slnx

# ---------- Stage 2: build ----------
FROM restore AS build
WORKDIR /src

COPY src/backend-dotnet/ src/backend-dotnet/

RUN dotnet publish src/backend-dotnet/src/AgentBoard.Api/AgentBoard.Api.csproj \
    --configuration Release \
    --output /app/publish \
    --no-restore \
    /p:UseAppHost=false

# ---------- Stage 3: runtime ----------
FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS runtime
WORKDIR /app

# Non-root user for runtime hardening.
RUN groupadd --system --gid 1001 agentboard \
    && useradd --system --uid 1001 --gid agentboard agentboard \
    && mkdir -p /app/Logs \
    && chown -R agentboard:agentboard /app

COPY --from=build --chown=agentboard:agentboard /app/publish /app

USER agentboard

ENV ASPNETCORE_ENVIRONMENT=Production \
    AGENTBOARD_ENV=production \
    AGENTBOARD_DOTNET_PORT=8080 \
    DOTNET_RUNNING_IN_CONTAINER=true \
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=true

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:8080/api/health || exit 1

ENTRYPOINT ["dotnet", "AgentBoard.Api.dll"]
