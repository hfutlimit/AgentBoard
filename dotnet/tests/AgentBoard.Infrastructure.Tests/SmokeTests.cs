// SPDX-License-Identifier: MIT
namespace AgentBoard.Infrastructure.Tests;

/// <summary>Smoke test — confirms the runner is alive. Real coverage lives in
/// <c>Persistence/*</c> and <c>Performance/*</c>.</summary>
public sealed class SmokeTests
{
    [Fact]
    public void Runner_Discovers_Tests() => Assert.True(true);
}
