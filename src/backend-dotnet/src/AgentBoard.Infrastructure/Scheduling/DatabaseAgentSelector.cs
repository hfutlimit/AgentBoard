// SPDX-License-Identifier: MIT
using System.Text.Json;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Workflow.Durable;
using AgentBoard.Infrastructure.Persistence;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace AgentBoard.Infrastructure.Scheduling;

/// <summary>
/// Selects a runnable AgentInstance using the existing AgentBoard ownership,
/// project authorization, heartbeat, capability, and self-review constraints.
/// Roles are intentionally not an authorization gate; workload eligibility is
/// dynamic and capability-based.
/// </summary>
public sealed class DatabaseAgentSelector : IAgentSelector
{
    private static readonly TimeSpan HeartbeatTtl = TimeSpan.FromMinutes(5);
    private readonly IServiceScopeFactory _scopes;

    public DatabaseAgentSelector(IServiceScopeFactory scopes) => _scopes = scopes;

    public AgentSelection? Select(AgentSelectionRequest request)
    {
        using var scope = _scopes.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var cutoff = DateTime.UtcNow - HeartbeatTtl;

        if (!db.ProjectMembers.AsNoTracking().Any(member =>
                member.ProjectId == request.ProjectId && member.UserId == request.OwnerUserId))
        {
            return null;
        }

        var rows = (
            from agent in db.Agents.AsNoTracking()
            join instance in db.AgentInstances.AsNoTracking() on agent.AgentId equals instance.AgentId
            join worker in db.Workers.AsNoTracking() on instance.WorkerId equals worker.WorkerId
            join mapping in db.WorkerProjectMappings.AsNoTracking()
                on new { instance.WorkerId, request.ProjectId }
                equals new { mapping.WorkerId, mapping.ProjectId }
            where agent.Enabled
                  && agent.Online
                  && agent.UserId == request.OwnerUserId
                  && instance.Enabled
                  && instance.Online
                  && instance.LastHeartbeat != null
                  && instance.LastHeartbeat >= cutoff
                  && worker.Status == "active"
                  && worker.LastHeartbeat != null
                  && worker.LastHeartbeat >= cutoff
                  && mapping.Enabled
            orderby agent.Id, instance.WorkerId
            select new { Agent = agent, Instance = instance })
            .ToList();

        foreach (var row in rows)
        {
            if (request.ExcludedAgentIds.Contains(row.Agent.AgentId)) continue;

            IReadOnlyDictionary<string, double> capabilities;
            try
            {
                capabilities = AgentCapabilityJson.ParseProfile(row.Agent.Capabilities);
            }
            catch (InvalidValueException)
            {
                continue; // malformed profiles fail closed
            }

            if (request.RequiredCapabilities.Any(required =>
                    !capabilities.TryGetValue(required.Name, out var level)
                    || level < required.MinimumLevel))
            {
                continue;
            }

            var providerId = ResolveProviderId(row.Agent.Roles, row.Instance.ExecutorType);
            if (providerId is null) continue;

            return new AgentSelection(
                row.Instance.WorkerId,
                row.Agent.AgentId,
                capabilities.Keys.OrderBy(name => name, StringComparer.OrdinalIgnoreCase).ToList(),
                providerId);
        }

        return null;
    }

    private static string? ResolveProviderId(string rolesJson, string? executorType)
    {
        var direct = executorType?.Trim().ToLowerInvariant();
        if (!string.IsNullOrWhiteSpace(direct)) return direct;

        try
        {
            using var roles = JsonDocument.Parse(string.IsNullOrWhiteSpace(rolesJson) ? "[]" : rolesJson);
            if (roles.RootElement.ValueKind != JsonValueKind.Array) return null;
            var supported = new HashSet<string>(
                new[] { "codex", "workbuddy", "minimax", "qwen", "fake", "scenario" },
                StringComparer.OrdinalIgnoreCase);
            return roles.RootElement.EnumerateArray()
                .Where(role => role.ValueKind == JsonValueKind.String)
                .Select(role => role.GetString()?.Trim().ToLowerInvariant())
                .FirstOrDefault(role => role is not null && supported.Contains(role));
        }
        catch (JsonException)
        {
            return null;
        }
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
