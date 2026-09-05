// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Process;
using Microsoft.Extensions.Options;

namespace AgentBoard.Node.WorkerOwned;

public sealed class WorkerOwnedOptions
{
    public bool Enabled { get; set; }
    public int ReconcileSeconds { get; set; } = 5;
    public LocalProject[] Projects { get; set; } = [];
    public LocalAgentProfile[] Agents { get; set; } = [];

    public void Validate()
    {
        if (!Enabled) return;
        if (Projects.Length == 0 || Agents.Length == 0)
            throw new InvalidOperationException("WorkerOwned requires explicit local Projects and Agents");
        if (Projects.Select(p => p.ProjectId).Distinct().Count() != Projects.Length
            || Agents.Select(a => a.Id).Distinct(StringComparer.Ordinal).Count() != Agents.Length)
            throw new InvalidOperationException("Duplicate project or Agent identity");
        foreach (var project in Projects)
            if (project.ProjectId <= 0 || !Path.IsPathFullyQualified(project.LocalPath) || !Directory.Exists(project.LocalPath))
                throw new InvalidOperationException("Project requires an existing absolute local checkout path");
        foreach (var agent in Agents)
        {
            if (string.IsNullOrWhiteSpace(agent.Id) || string.IsNullOrWhiteSpace(agent.Runtime.Command)
                || agent.Provider is not ("codex" or "workbuddy" or "minimax")
                || agent.WorkKinds.Length == 0 || agent.ProjectIds.Length == 0
                || agent.WorkKinds.Any(k => !WorkerWorkKinds.All.Contains(k, StringComparer.Ordinal))
                || agent.ProjectIds.Any(id => Projects.All(p => p.ProjectId != id)))
                throw new InvalidOperationException($"Agent '{agent.Id}' needs a supported provider, explicit work kinds and local projects");
        }
    }

    public IEnumerable<(int ProjectId, string Kind)> Subscriptions() => Agents
        .SelectMany(a => a.ProjectIds.SelectMany(p => a.WorkKinds.Select(k => (p, k)))).Distinct();

    public IEnumerable<LocalAgentProfile> Candidates(int project, string kind) => Agents
        .Where(a => a.ProjectIds.Contains(project) && a.WorkKinds.Contains(kind, StringComparer.Ordinal));
}

public sealed class LocalProject
{
    public int ProjectId { get; set; }
    public string LocalPath { get; set; } = "";
}

public sealed class LocalAgentProfile
{
    public string Id { get; set; } = "";
    public string Provider { get; set; } = "";
    public string[] WorkKinds { get; set; } = [];
    public int[] ProjectIds { get; set; } = [];
    public AgentOptions Runtime { get; set; } = new();
}

/// <summary>Each profile gets independent immutable options, even for the same provider.</summary>
public sealed class LocalAdapterFactory(IProcessExecutor process, ILoggerFactory logs)
{
    public IAgentAdapter Create(LocalAgentProfile profile)
    {
        // Provider does not need the Worker control-plane credential: it
        // returns structured evidence; only the fenced Worker writes state.
        var runtime = System.Text.Json.JsonSerializer.Deserialize<AgentOptions>(
            System.Text.Json.JsonSerializer.Serialize(profile.Runtime))!;
        runtime.AgentBoardToken = "";
        var options = Options.Create(new AgentsOptions
        {
            Codex = runtime, WorkBuddy = runtime, MiniMax = runtime,
        });
        var api = Options.Create(new AgentBoardOptions());
        return profile.Provider switch
        {
            "codex" => new CodexAdapter(process, options, api, logs.CreateLogger<CodexAdapter>()),
            "workbuddy" => new WorkBuddyAdapter(process, options, api, logs.CreateLogger<WorkBuddyAdapter>()),
            "minimax" => new MiniMaxAdapter(process, options, api, logs.CreateLogger<MiniMaxAdapter>()),
            _ => throw new InvalidOperationException("Unsupported local provider"),
        };
    }
}
