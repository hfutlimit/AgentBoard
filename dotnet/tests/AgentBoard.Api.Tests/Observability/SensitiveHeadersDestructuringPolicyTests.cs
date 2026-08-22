// SPDX-License-Identifier: MIT
using System.Net.Http;
using AgentBoard.Api.Observability;
using FluentAssertions;
using Serilog.Core;
using Serilog.Events;
using Xunit;

namespace AgentBoard.Api.Tests.Observability;

/// <summary>Verifies the Serilog destructuring policy masks secret header
/// values when an HttpRequestMessage / HttpHeaders bag is logged (#313).</summary>
public class SensitiveHeadersDestructuringPolicyTests
{
    private sealed class StubValueFactory : ILogEventPropertyValueFactory
    {
        public LogEventPropertyValue CreatePropertyValue(object? value, bool destructureObjects = false)
            => new ScalarValue(value?.ToString());
    }

    [Fact]
    public void Scrubs_Sensitive_Header_Values_On_Logged_Request()
    {
        var req = new HttpRequestMessage(HttpMethod.Get, "http://x/y");
        req.Headers.TryAddWithoutValidation("Authorization", "Bearer secret");
        req.Headers.TryAddWithoutValidation("X-Api-Key", "abc123");
        req.Headers.TryAddWithoutValidation("X-Custom", "visible");

        var policy = new SensitiveHeadersDestructuringPolicy();
        var ok = policy.TryDestructure(req, new StubValueFactory(), out var result);

        ok.Should().BeTrue();
        var requestStruct = result.Should().BeOfType<StructureValue>().Subject;
        var headers = requestStruct.Properties
            .First(p => p.Name == "headers").Value
            .Should().BeOfType<StructureValue>().Subject;

        headers.Properties.First(p => p.Name == "Authorization").Value
            .Should().BeOfType<ScalarValue>().Which.Value.Should().Be("***");
        headers.Properties.First(p => p.Name == "X-Api-Key").Value
            .Should().BeOfType<ScalarValue>().Which.Value.Should().Be("***");
        headers.Properties.First(p => p.Name == "X-Custom").Value
            .Should().BeOfType<ScalarValue>().Which.Value.Should().Be("visible");
    }

    [Fact]
    public void Scrubs_Bare_HttpHeaders_Bag()
    {
        var headers = new HttpRequestMessage().Headers;
        headers.TryAddWithoutValidation("Cookie", "session=abc");

        var policy = new SensitiveHeadersDestructuringPolicy();
        var ok = policy.TryDestructure(headers, new StubValueFactory(), out var result);

        ok.Should().BeTrue();
        var structValue = result.Should().BeOfType<StructureValue>().Subject;
        structValue.Properties.First(p => p.Name == "Cookie").Value
            .Should().BeOfType<ScalarValue>().Which.Value.Should().Be("***");
    }

    [Fact]
    public void Ignores_Non_Http_Types()
    {
        var policy = new SensitiveHeadersDestructuringPolicy();
        var ok = policy.TryDestructure("just a string", new StubValueFactory(), out _);

        ok.Should().BeFalse();
    }
}
