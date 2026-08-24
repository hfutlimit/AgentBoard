// SPDX-License-Identifier: MIT
using System.Diagnostics;
using AgentBoard.Api.Observability;
using FluentAssertions;
using Xunit;

namespace AgentBoard.Api.Tests.Observability;

/// <summary>Verifies the OpenTelemetry processor drops sensitive span
/// attributes before export (#313 masking gate, OTel surface).</summary>
public class SensitiveAttributeProcessorTests
{
    [Fact]
    public void Drops_Sensitive_Span_Attributes_But_Keeps_Others()
    {
        var activity = new Activity("test");
        activity.Start();
        try
        {
            activity.SetTag("Authorization", "Bearer x");
            activity.SetTag("Cookie", "sid=1");
            activity.SetTag("method", "GET");

            var processor = new SensitiveAttributeProcessor();
            processor.OnEnd(activity);

            activity.GetTagItem("Authorization").Should().BeNull();
            activity.GetTagItem("Cookie").Should().BeNull();
            activity.GetTagItem("method").Should().Be("GET");
        }
        finally
        {
            activity.Stop();
        }
    }
}
