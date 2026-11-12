namespace AgentBoard.Node.WorkerOwned;

public sealed record CreateLocalAgentRequest(string Id, string Provider, string Model, string Revision);

public static class LocalAgentCatalog
{
    public static string[] Models(string provider) => provider switch
    {
        "codex" => ["gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna"],
        "workbuddy" => ["hy4-preview", "glm-5.3-flash"],
        "cursor" => ["cursor-grok-4.6-high", "composer-2.5"],
        // Display names accepted by `qoderclicn --model`. Operator-specific BYOK
        // provider ids are deliberately excluded; the portal degrades to
        // "select a model" when a saved value is no longer offered.
        "qoder" =>
        [
            "Auto", "Fast",
            "Qwen3.8-Max", "Qwen3.8-Flash",
            "DeepSeek-V4-Pro", "DeepSeek-Flash",
            "GLM-5.3", "GLM-5.3-Flash",
            "Kimi-K3.1-Pro", "Kimi-K2.8-Preview",
            "Step-5-Preview", "MiniMax-M3.1-Flash",
        ],
        _ => [],
    };

    public static LocalAgentProfile Create(CreateLocalAgentRequest request)
    {
        var id = request.Id?.Trim() ?? "";
        if (id.Length is < 1 or > 100 || id.Any(char.IsControl))
            throw new ArgumentException("Agent ID must contain 1-100 characters without control characters");
        if (!Models(request.Provider).Contains(request.Model, StringComparer.Ordinal))
            throw new ArgumentException("Select a supported model for this provider");
        return new LocalAgentProfile
        {
            Id = id, Provider = request.Provider, Enabled = false, WorkKinds = [],
            Runtime = new()
            {
                Command = request.Provider switch
                {
                    "workbuddy" => "codebuddy",
                    "cursor" => "agent",
                    "qoder" => "qoderclicn",
                    _ => request.Provider,
                },
                Model = request.Model,
                Arguments = request.Provider switch
                {
                    "codex" => ["exec", "--json"],
                    "workbuddy" => ["-p", "-y", "--output-format", "text"],
                    "cursor" => ["-p", "--force", "--trust", "--approve-mcps", "--output-format", "json"],
                    "qoder" => [.. Agents.QoderAdapter.HeadlessArguments],
                    _ => ["--print"],
                },
                TimeoutMinutes = 30, MaxCapturedOutputChars = 100000,
            },
        };
    }

    // The model dropdown is authoritative, including for adapters which otherwise
    // only read --model from Arguments. Never mutate the saved profile here.
    public static string[] ModelArguments(string[] arguments, string model)
    {
        if (string.IsNullOrWhiteSpace(model)) return arguments.ToArray();
        var result = new List<string>();
        for (var i = 0; i < arguments.Length; i++)
        {
            if (arguments[i] is "--model" or "-m") { i++; continue; }
            if (arguments[i].StartsWith("--model=", StringComparison.Ordinal)) continue;
            result.Add(arguments[i]);
        }
        result.AddRange(["--model", model]);
        return result.ToArray();
    }
}
