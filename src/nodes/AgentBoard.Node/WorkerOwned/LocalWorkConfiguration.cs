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
        ValidateConfiguration();
    }

    public void ValidateConfiguration()
    {
        if (Projects is null || Agents is null) throw new InvalidOperationException("Projects and Agents are required");
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
            if (agent.Runtime is null || agent.WorkKinds is null
                || agent.Prompts is null || agent.PrePrompt is null || agent.PostPrompt is null)
                throw new InvalidOperationException("Agent configuration fields cannot be null");
            if (string.IsNullOrWhiteSpace(agent.Id) || string.IsNullOrWhiteSpace(agent.Runtime.Command)
                || agent.Provider is not ("codex" or "workbuddy" or "minimax")
                || agent.WorkKinds.Length == 0
                || agent.WorkKinds.Any(k => !WorkerWorkKinds.All.Contains(k, StringComparer.Ordinal)))
                throw new InvalidOperationException($"Agent '{agent.Id}' needs a supported provider and explicit work kinds; projects are Worker-wide");
            if (agent.Runtime.TimeoutMinutes is < 1 or > 1440
                || agent.Runtime.Arguments is null || agent.Runtime.Arguments.Any(a => a is null)
                || agent.PrePrompt.Length > 20000 || agent.PostPrompt.Length > 20000
                || agent.Prompts.Any(p => !WorkerWorkKinds.All.Contains(p.Key, StringComparer.Ordinal)
                    || p.Value is null || p.Value.Pre is null || p.Value.Post is null
                    || p.Value.Pre.Length > 20000 || p.Value.Post.Length > 20000))
                throw new InvalidOperationException("Invalid timeout or work-kind prompts (maximum 20000 characters per prompt)");
        }
    }

    [System.Text.Json.Serialization.JsonIgnore]
    public IEnumerable<LocalAgentProfile> EnabledAgents => Agents.Where(a => a.Enabled);

    public IEnumerable<(int ProjectId, string Kind)> Subscriptions() => EnabledAgents
        .SelectMany(a => Projects.SelectMany(p => a.WorkKinds.Select(k => (p.ProjectId, k)))).Distinct();

    public IEnumerable<LocalAgentProfile> Candidates(int project, string kind) => EnabledAgents
        .Where(a => Projects.Any(p => p.ProjectId == project) && a.WorkKinds.Contains(kind, StringComparer.Ordinal));
}

public sealed class LocalProject
{
    public int ProjectId { get; set; }
    public string LocalPath { get; set; } = "";
}

public sealed class LocalAgentProfile
{
    public bool Enabled { get; set; } = true;
    public string Id { get; set; } = "";
    public string Provider { get; set; } = "";
    public string[] WorkKinds { get; set; } = [];
    // Legacy input retained for source compatibility, never used for eligibility.
    [System.Text.Json.Serialization.JsonIgnore]
    public int[] ProjectIds { get; set; } = [];
    public AgentOptions Runtime { get; set; } = new();
    public string PrePrompt { get; set; } = "";
    public string PostPrompt { get; set; } = "";
    public Dictionary<string, WorkPromptPair> Prompts { get; set; } = new(StringComparer.Ordinal);
}

public sealed class WorkPromptPair
{
    public string Pre { get; set; } = "";
    public string Post { get; set; } = "";
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
