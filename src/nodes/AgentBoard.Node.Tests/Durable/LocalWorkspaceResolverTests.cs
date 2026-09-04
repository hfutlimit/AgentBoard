// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Node.Durable;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.Node.Tests.Durable;

public sealed class LocalWorkspaceResolverTests : IDisposable
{
    private readonly string _root = Path.Combine(
        Path.GetTempPath(), $"agentboard-workspaces-{Guid.NewGuid():N}");

    [Fact]
    public void Resolves_each_server_identity_to_its_node_local_path()
    {
        var first = Directory.CreateDirectory(Path.Combine(_root, "first")).FullName;
        var second = Directory.CreateDirectory(Path.Combine(_root, "second")).FullName;
        var resolver = new ConfiguredLocalWorkspaceResolver(Options.Create(new DurableExecutionOptions
        {
            Workspaces =
            [
                new() { ProjectId = "project-1", WorkspaceId = "main", LocalPath = first },
                new() { ProjectId = "project-2", WorkspaceId = "main", LocalPath = second },
            ],
        }));

        Assert.Equal(first, resolver.Resolve(new WorkspaceReference("project-1", "main", "commit-a")));
        Assert.Equal(second, resolver.Resolve(new WorkspaceReference("project-2", "main", "commit-b")));
        Assert.Throws<LocalWorkspaceResolutionException>(() =>
            resolver.Resolve(new WorkspaceReference("project-3", "main", "commit-c")));
    }

    [Fact]
    public void Rejects_a_binding_to_a_missing_local_directory_at_startup()
    {
        var missing = Path.Combine(_root, "missing");

        Assert.Throws<DirectoryNotFoundException>(() =>
            new ConfiguredLocalWorkspaceResolver(Options.Create(new DurableExecutionOptions
            {
                Workspaces =
                [
                    new() { ProjectId = "project-1", WorkspaceId = "main", LocalPath = missing },
                ],
            })));
    }

    public void Dispose()
    {
        if (Directory.Exists(_root)) Directory.Delete(_root, recursive: true);
    }
}
