// SPDX-License-Identifier: MIT
namespace AgentBoard.Api.Observability;

/// <summary>
/// Serilog destructuring policy that guarantees secret header values never
/// reach the console / file sinks. Whenever an <see cref="System.Net.Http.HttpRequestMessage"/>
/// or <see cref="System.Net.Http.Headers.HttpHeaders"/> (request or response
/// headers) is logged, its sensitive header values (Authorization, Api-Key,
/// Cookie, ...) are replaced with <c>"***"</c>.
///
/// This is the structured-logging half of the #313 masking gate. The free-text
/// half is <see cref="SensitiveDataScrubber.RedactConnectionString"/>, which
/// callers use before logging any connection string.
///
/// NOTE: Serilog core types are referenced fully-qualified on purpose. A bare
/// `using Serilog;` resolves `Serilog` to a namespace that lacks
/// IDestructuringPolicy in this project's reference closure; the fully-qualified
/// form resolves through the Serilog 4.2.0 assembly unambiguously.
/// </summary>
public sealed class SensitiveHeadersDestructuringPolicy : Serilog.Core.IDestructuringPolicy
{
    public bool TryDestructure(
        object value,
        Serilog.Core.ILogEventPropertyValueFactory propertyValueFactory,
        out Serilog.Events.LogEventPropertyValue result)
    {
        result = null;

        if (value is System.Net.Http.Headers.HttpHeaders headers)
        {
            result = BuildHeadersValue(headers);
            return true;
        }

        if (value is System.Net.Http.HttpRequestMessage request)
        {
            var props = new System.Collections.Generic.List<Serilog.Events.LogEventProperty>
            {
                new Serilog.Events.LogEventProperty("method", new Serilog.Events.ScalarValue(request.Method?.Method)),
                new Serilog.Events.LogEventProperty("requestUri", new Serilog.Events.ScalarValue(request.RequestUri?.ToString())),
            };
            if (request.Headers is not null)
            {
                props.Add(new Serilog.Events.LogEventProperty("headers", BuildHeadersValue(request.Headers)));
            }

            result = new Serilog.Events.StructureValue(props);
            return true;
        }

        return false;
    }

    private static Serilog.Events.StructureValue BuildHeadersValue(System.Net.Http.Headers.HttpHeaders headers)
    {
        var props = new System.Collections.Generic.List<Serilog.Events.LogEventProperty>();
        foreach (var header in headers)
        {
            var masked = SensitiveDataScrubber.IsSensitiveHeader(header.Key)
                ? new Serilog.Events.ScalarValue("***")
                : new Serilog.Events.ScalarValue(string.Join(", ", header.Value));
            props.Add(new Serilog.Events.LogEventProperty(header.Key, masked));
        }

        return new Serilog.Events.StructureValue(props);
    }
}
