// SPDX-License-Identifier: MIT
using System.Text.RegularExpressions;

namespace AgentBoard.Api.Observability;

/// <summary>
/// Central registry of sensitive identifiers plus pure helpers to redact
/// them. Used by:
///   - <see cref="SensitiveHeadersDestructuringPolicy"/> (Serilog) so logged
///     HttpRequestMessage / HttpHeaders never reveal secret header values.
///   - <see cref="SensitiveAttributeProcessor"/> (OpenTelemetry) so sensitive
///     span attributes are dropped before export.
///   - Callers that need to log a connection string or token safely.
///
/// Masking failure condition (the #313 gate): any Authorization / API key /
/// cookie / connection-string value reaching console, file, or an OTel
/// attribute is a hard fail. These helpers are the single choke point that
/// guarantees that does not happen.
/// </summary>
public static partial class SensitiveDataScrubber
{
    /// <summary>Header names whose VALUES must be masked in any log output.</summary>
    public static readonly HashSet<string> SensitiveHeaderNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "Authorization",
        "Proxy-Authorization",
        "Api-Key",
        "X-Api-Key",
        "X-Api-Token",
        "X-Auth-Token",
        "X-Csrf-Token",
        "Cookie",
        "Set-Cookie",
    };

    /// <summary>Span-attribute keys whose VALUES must be dropped before export.</summary>
    public static readonly HashSet<string> SensitiveAttributeKeys = new(StringComparer.OrdinalIgnoreCase)
    {
        "Authorization",
        "Proxy-Authorization",
        "ApiKey",
        "Api-Key",
        "X-Api-Key",
        "Cookie",
        "Set-Cookie",
        "X-Csrf-Token",
        "X-Auth-Token",
        "Token",
        "Password",
        "Pwd",
    };

    public static bool IsSensitiveHeader(string name) =>
        SensitiveHeaderNames.Contains(name);

    /// <summary>Returns <c>"***"</c> for sensitive headers, the original value otherwise.</summary>
    public static string RedactHeaderValue(string name, string value) =>
        IsSensitiveHeader(name) ? "***" : value;

    [GeneratedRegex(@"(?i)(password|pwd)=([^;]+)", RegexOptions.NonBacktracking)]
    private static partial Regex KeyValuePassword();

    [GeneratedRegex(@"(?i)([a-z]+://[^:/?#]+:)([^@]+)(@)", RegexOptions.NonBacktracking)]
    private static partial Regex UriPassword();

    /// <summary>
    /// Masks credentials inside a connection string, e.g.
    ///   mysql+pymysql://agentboard:SECRET@db:3306/agentboard
    ///     -> mysql+pymysql://agentboard:***@db:3306/agentboard
    ///   Data Source=x;User Id=y;Password=SECRET; -> ...Password=***;
    /// Returns the input unchanged when no credential pattern is found.
    /// </summary>
    public static string RedactConnectionString(string input)
    {
        if (string.IsNullOrWhiteSpace(input))
        {
            return input;
        }

        var redacted = UriPassword().Replace(input, "$1***$3");
        redacted = KeyValuePassword().Replace(redacted, "$1=***");
        return redacted;
    }
}
