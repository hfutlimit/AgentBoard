// SPDX-License-Identifier: MIT
using AgentBoard.Api.Observability;
using FluentAssertions;
using Xunit;

namespace AgentBoard.Api.Tests.Observability;

/// <summary>Verifies the sensitive-data redaction helpers used by both the
/// Serilog policy and callers logging connection strings (#313).</summary>
public class SensitiveDataScrubberTests
{
    [Fact]
    public void Redacts_Uri_Style_ConnectionString()
    {
        SensitiveDataScrubber
            .RedactConnectionString("mysql+pymysql://agentboard:SECRET@db:3306/agentboard")
            .Should().Be("mysql+pymysql://agentboard:***@db:3306/agentboard");
    }

    [Fact]
    public void Redacts_KeyValue_ConnectionString()
    {
        SensitiveDataScrubber
            .RedactConnectionString("Data Source=srv;User Id=u;Password=TOPSEC;")
            .Should().Be("Data Source=srv;User Id=u;Password=***;");
    }

    [Fact]
    public void Leaves_Non_Sensitive_Input_Unchanged()
    {
        const string input = "Server=srv;Database=db;";
        SensitiveDataScrubber.RedactConnectionString(input).Should().Be(input);
    }

    [Fact]
    public void Redacts_Sensitive_Header_Values()
    {
        SensitiveDataScrubber.RedactHeaderValue("Authorization", "Bearer t").Should().Be("***");
        SensitiveDataScrubber.RedactHeaderValue("X-Api-Key", "k").Should().Be("***");
        SensitiveDataScrubber.RedactHeaderValue("X-Custom", "v").Should().Be("v");
    }

    [Fact]
    public void Detects_Sensitive_Headers()
    {
        SensitiveDataScrubber.IsSensitiveHeader("Cookie").Should().BeTrue();
        SensitiveDataScrubber.IsSensitiveHeader("X-Other").Should().BeFalse();
    }
}
