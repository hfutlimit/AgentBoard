// SPDX-License-Identifier: MIT
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Workflow.Durable;
using Microsoft.Extensions.Configuration;

namespace AgentBoard.Infrastructure.Scheduling;

/// <summary>
/// Selects a runnable AgentInstance for a durable workflow stage by calling the
/// FastAPI <c>POST /api/durable/agent-select</c> endpoint over HTTP with the
/// internal service credential.
/// </summary>
/// <remarks>
/// This used to run the ownership/heartbeat/capability/provider joins against
/// the local SQLite shadow database. That shadow holds no business rows in this
/// deployment (and the .NET BFF has no live MySQL provider — Pomelo ships no
/// EF Core 10 release), so eligibility had to be read from the single source of
/// truth, FastAPI + MariaDB. The authoritative selection policy now lives in
/// <c>agentboard/features/scheduling/router.py::durable_agent_select</c>; this
/// class is a thin, fail-closed transport that shapes the request and parses the
/// response back into <see cref="AgentSelection"/>.
/// <para>
/// Fail-closed contract: a missing/placeholder credential, a non-2xx response, a
/// <c>{"selection": null}</c> body, or any parse error all yield <c>null</c>, so
/// the orchestrator keeps the todo deferred rather than dispatching to an
/// unverified executor. The endpoint enforces auth even over 127.0.0.1.
/// </para>
/// </remarks>
public sealed class DatabaseAgentSelector : IAgentSelector
{
    private static readonly JsonSerializerOptions WriteOptions = new(JsonSerializerDefaults.Web);
    private readonly IHttpClientFactory _clients;
    private readonly IConfiguration _configuration;

    public DatabaseAgentSelector(
        IHttpClientFactory clients,
        IConfiguration configuration)
    {
        _clients = clients;
        _configuration = configuration;
    }

    public AgentSelection? Select(AgentSelectionRequest request)
    {
        var token = _configuration["AgentBoard:FastApi:InternalToken"];
        if (string.IsNullOrWhiteSpace(token)
            || string.Equals(token, "REPLACE_WITH_INTERNAL_SERVICE_TOKEN", StringComparison.Ordinal))
        {
            // No internal credential configured → cannot authenticate the read
            // upstream. Fail closed instead of dispatching blind.
            return null;
        }

        try
        {
            return SelectAsync(request, token).GetAwaiter().GetResult();
        }
        catch (Exception)
        {
            // Transport/parse failures never surface to the run — a null keeps
            // the stage deferred and observable, matching the old selector's
            // "no candidates" behavior.
            return null;
        }
    }

    private async Task<AgentSelection?> SelectAsync(
        AgentSelectionRequest request, string token)
    {
        var payload = BuildRequestPayload(request);

        var client = _clients.CreateClient("AgentBoardFastApi");
        using var message = new HttpRequestMessage(
            HttpMethod.Post, "api/durable/agent-select")
        {
            Content = new StringContent(
                payload.ToJsonString(WriteOptions), Encoding.UTF8, "application/json"),
        };
        message.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);

        using var response = await client.SendAsync(message);
        if (!response.IsSuccessStatusCode)
        {
            return null;
        }

        using var doc = JsonDocument.Parse(
            await response.Content.ReadAsStringAsync());
        return ParseSelection(doc.RootElement);
    }

    private static JsonNode BuildRequestPayload(AgentSelectionRequest request)
    {
        var capabilities = new JsonArray();
        foreach (var requirement in request.RequiredCapabilities)
        {
            capabilities.Add(new JsonObject
            {
                ["name"] = requirement.Name,
                ["minimum_level"] = requirement.MinimumLevel,
            });
        }

        var exclude = new JsonArray();
        foreach (var excluded in request.ExcludedAgentIds)
        {
            exclude.Add(excluded);
        }

        return new JsonObject
        {
            ["project_id"] = request.ProjectId,
            ["owner_user_id"] = request.OwnerUserId,
            ["capabilities"] = capabilities,
            ["exclude"] = exclude,
        };
    }

    private static AgentSelection? ParseSelection(JsonElement root)
    {
        if (!root.TryGetProperty("selection", out var selection)
            || selection.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        var workerId = GetString(selection, "worker_id");
        var agentId = GetString(selection, "agent_id");
        if (string.IsNullOrWhiteSpace(workerId) || string.IsNullOrWhiteSpace(agentId))
        {
            return null;
        }

        var capabilities = new List<string>();
        if (selection.TryGetProperty("capabilities", out var capArray)
            && capArray.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in capArray.EnumerateArray())
            {
                if (item.ValueKind == JsonValueKind.String)
                {
                    capabilities.Add(item.GetString()!);
                }
            }
        }

        var providerId = GetString(selection, "provider_id");
        return new AgentSelection(workerId, agentId, capabilities, providerId);
    }

    private static string? GetString(JsonElement element, string property)
    {
        if (!element.TryGetProperty(property, out var value) || value.ValueKind == JsonValueKind.Null)
            return null;
        return value.ValueKind == JsonValueKind.String ? value.GetString() : value.ToString();
    }
}

/// <summary>Strict parser shared by workflow intake and Agent selection.</summary>
public static class AgentCapabilityJson
{
    public static IReadOnlyList<AgentCapabilityRequirement> ParseRequirements(string? json)
    {
        using var document = ParseArray(json, "needed_capabilities");
        var result = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase);
        foreach (var item in document.RootElement.EnumerateArray())
        {
            string name;
            double minimum;
            if (item.ValueKind == JsonValueKind.String)
            {
                name = RequiredName(item.GetString(), "needed_capability");
                minimum = 1;
            }
            else if (item.ValueKind == JsonValueKind.Object)
            {
                name = ObjectName(item, "needed_capability");
                minimum = NumberOrDefault(item, "minimum_level", 1, "needed_capability minimum_level");
            }
            else
            {
                throw new InvalidValueException("needed_capabilities entries must be strings or objects");
            }

            if (minimum is < 0 or > 5)
                throw new InvalidValueException("needed_capability minimum_level must be between 0 and 5");
            result[name] = Math.Max(minimum, result.GetValueOrDefault(name));
        }

        return result.Select(pair => new AgentCapabilityRequirement(pair.Key, pair.Value)).ToList();
    }

    public static IReadOnlyDictionary<string, double> ParseProfile(string? json)
    {
        using var document = ParseArray(json, "capabilities");
        var result = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase);
        foreach (var item in document.RootElement.EnumerateArray())
        {
            string name;
            double level;
            if (item.ValueKind == JsonValueKind.String)
            {
                name = RequiredName(item.GetString(), "capability");
                level = 3;
            }
            else if (item.ValueKind == JsonValueKind.Object)
            {
                name = ObjectName(item, "capability");
                level = NumberOrDefault(item, "level", 3, "capability level");
            }
            else
            {
                throw new InvalidValueException("capabilities entries must be strings or objects");
            }

            if (level is < 0 or > 5)
                throw new InvalidValueException("capability level must be between 0 and 5");
            result[name] = Math.Max(level, result.GetValueOrDefault(name));
        }
        return result;
    }

    private static JsonDocument ParseArray(string? json, string field)
    {
        try
        {
            var document = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "[]" : json);
            if (document.RootElement.ValueKind == JsonValueKind.Array) return document;
            document.Dispose();
            throw new InvalidValueException($"{field} must be a JSON array");
        }
        catch (JsonException error)
        {
            throw new InvalidValueException($"{field} must be a valid JSON array: {error.Message}");
        }
    }

    private static string RequiredName(string? value, string field)
    {
        var name = value?.Trim().ToLowerInvariant();
        if (string.IsNullOrWhiteSpace(name)) throw new InvalidValueException($"{field} name is required");
        if (name.Length > 64) throw new InvalidValueException($"{field} name must be at most 64 characters");
        return name;
    }

    private static string ObjectName(JsonElement item, string field)
    {
        if (!item.TryGetProperty("name", out var value) || value.ValueKind != JsonValueKind.String)
            throw new InvalidValueException($"{field} name must be a string");
        return RequiredName(value.GetString(), field);
    }

    private static double NumberOrDefault(
        JsonElement item,
        string propertyName,
        double defaultValue,
        string field)
    {
        if (!item.TryGetProperty(propertyName, out var value)) return defaultValue;
        if (value.ValueKind != JsonValueKind.Number)
            throw new InvalidValueException($"{field} must be a number from 0 to 5");
        return value.GetDouble();
    }
}
