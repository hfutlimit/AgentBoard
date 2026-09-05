using AgentBoard.Node.WorkerOwned;
using Xunit;

namespace AgentBoard.Node.Tests;

public sealed class LocalAgentCatalogTests
{
    [Theory]
    [InlineData("codex", "gpt-5.6-sol")]
    [InlineData("codex", "gpt-5.6-terra")]
    [InlineData("codex", "gpt-5.6-luna")]
    [InlineData("workbuddy", "hy4-preview")]
    [InlineData("workbuddy", "glm-5.3-flash")]
    [InlineData("minimax", "m3")]
    public void Basic_profile_is_disabled_until_user_selects_work(string provider, string model)
    {
        var agent = LocalAgentCatalog.Create(new(" test-agent ", provider, model, "revision"));
        Assert.Equal("test-agent", agent.Id);
        Assert.Equal(model, agent.Runtime.Model);
        Assert.False(agent.Enabled);
        Assert.Empty(agent.WorkKinds);
        var options = new WorkerOwnedOptions { Agents = [agent] };
        options.ValidateConfiguration();
        Assert.Empty(options.Subscriptions());
        agent.Enabled = true;
        Assert.Throws<InvalidOperationException>(options.ValidateConfiguration);
    }

    [Fact]
    public void Models_are_provider_specific_and_cli_selection_overrides_old_flags()
    {
        Assert.Equal(3, LocalAgentCatalog.Models("codex").Length);
        Assert.Equal(2, LocalAgentCatalog.Models("workbuddy").Length);
        Assert.Single(LocalAgentCatalog.Models("minimax"));
        Assert.Throws<ArgumentException>(() => LocalAgentCatalog.Create(new("a", "workbuddy", "gpt-5.6-sol", "v1")));
        Assert.Throws<ArgumentException>(() => LocalAgentCatalog.Create(new(" ", "codex", "gpt-5.6-sol", "v1")));
        string[] args = ["exec", "--model", "old", "--model=older", "-m", "oldest", "--json"];
        Assert.Equal(["exec", "--json", "--model", "gpt-5.6-sol"], LocalAgentCatalog.ModelArguments(args, "gpt-5.6-sol"));
        Assert.Equal("old", args[2]);
    }

    [Fact]
    public void Create_persists_basic_profile_with_CAS_without_saving_another_editor_draft()
    {
        var directory = Path.Combine(Path.GetTempPath(), "agent-create-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        try
        {
            var store = new LocalConfigurationStore(Path.Combine(directory, "settings.json"), new());
            var initial = store.Read();
            var created = store.AddAgent(new("a", "codex", "gpt-5.6-terra", initial.Revision));
            Assert.Single(store.Load().Agents);
            created.Configuration.Agents[0].PrePrompt = "unsaved";
            var added = store.AddAgent(new("b", "workbuddy", "hy4-preview", created.Revision));
            Assert.Equal(2, added.Configuration.Agents.Length);
            Assert.Empty(added.Configuration.Agents[0].PrePrompt);
            Assert.Throws<ConfigurationConflictException>(() => store.AddAgent(new("c", "minimax", "m3", created.Revision)));
            Assert.Throws<ArgumentException>(() => store.AddAgent(new("a", "codex", "gpt-5.6-sol", added.Revision)));
            Assert.Equal(2, store.Load().Agents.Length);
        }
        finally { Directory.Delete(directory, recursive: true); }
    }
}
