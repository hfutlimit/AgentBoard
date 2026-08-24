// SPDX-License-Identifier: MIT
using System.Diagnostics;

namespace AgentBoard.Api.Observability;

/// <summary>
/// The single <see cref="ActivitySource"/> for hand-rolled spans inside
/// the BFF. Register it with OpenTelemetry in
/// <see cref="OpenTelemetrySetup"/>; use it in Service / Provider code
/// when an operation is non-trivial (e.g. cross-table aggregation,
/// outbound FastAPI call).
/// </summary>
public static class AgentBoardActivitySource
{
    public const string Name = "AgentBoard.Api";

    public static readonly ActivitySource Instance = new(Name, version: typeof(AgentBoardActivitySource).Assembly.GetName().Version?.ToString() ?? "0.0.0");
}
