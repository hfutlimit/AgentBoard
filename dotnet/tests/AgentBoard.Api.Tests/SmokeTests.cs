// SPDX-License-Identifier: MIT
//
// Stage 0 smoke test — confirms the xUnit project compiles and the test
// runner can discover tests. Real unit / integration / contract tests
// arrive in S0-2 (Repository Pattern) and S0-3 (layered skeleton).

namespace AgentBoard.Api.Tests;

public sealed class SmokeTests
{
    [Fact]
    public void Smoke_RunnerDiscoversTests()
    {
        // Sanity assertion: the runner is alive and the test is reachable.
        Assert.True(true);
    }
}
