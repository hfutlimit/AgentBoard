// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using Microsoft.Extensions.Options;

namespace AgentBoard.Node.Durable;

/// <summary>
/// Resolves a Server-owned workspace identity to a Node-local repository path.
/// A missing binding fails closed; falling back to an unrelated global working
/// directory could execute one project's command inside another repository.
/// </summary>
public interface ILocalWorkspaceResolver
{
    string Resolve(WorkspaceReference workspace);
}

public sealed class LocalWorkspaceResolutionException : InvalidOperationException
{
    public LocalWorkspaceResolutionException(string message) : base(message) { }
}

public sealed class ConfiguredLocalWorkspaceResolver : ILocalWorkspaceResolver
{
    private readonly IReadOnlyDictionary<(string ProjectId, string WorkspaceId), string> _paths;

    public ConfiguredLocalWorkspaceResolver(IOptions<DurableExecutionOptions> options)
    {
        ArgumentNullException.ThrowIfNull(options);
        var paths = new Dictionary<(string, string), string>();
        foreach (var mapping in options.Value.Workspaces ?? Array.Empty<LocalWorkspaceMappingOptions>())
        {
            ArgumentException.ThrowIfNullOrWhiteSpace(mapping.ProjectId);
            ArgumentException.ThrowIfNullOrWhiteSpace(mapping.WorkspaceId);
            ArgumentException.ThrowIfNullOrWhiteSpace(mapping.LocalPath);
            var key = (mapping.ProjectId, mapping.WorkspaceId);
            var fullPath = Path.GetFullPath(mapping.LocalPath);
            if (!Directory.Exists(fullPath))
            {
                throw new DirectoryNotFoundException(
                    $"local workspace '{mapping.ProjectId}/{mapping.WorkspaceId}' does not exist");
            }
            if (!paths.TryAdd(key, fullPath))
            {
                throw new InvalidOperationException(
                    $"duplicate local workspace binding '{mapping.ProjectId}/{mapping.WorkspaceId}'");
            }
        }
        _paths = paths;
    }

    public string Resolve(WorkspaceReference workspace)
    {
        ArgumentNullException.ThrowIfNull(workspace);
        if (!_paths.TryGetValue((workspace.ProjectId, workspace.WorkspaceId), out var path))
        {
            throw new LocalWorkspaceResolutionException(
                $"no local workspace binding for '{workspace.ProjectId}/{workspace.WorkspaceId}'");
        }
        return path;
    }
}

/// <summary>Small explicit resolver used by isolated tests and embedded hosts.</summary>
public sealed class SingleLocalWorkspaceResolver : ILocalWorkspaceResolver
{
    private readonly WorkspaceReference _workspace;
    private readonly string _path;

    public SingleLocalWorkspaceResolver(WorkspaceReference workspace, string path)
    {
        _workspace = workspace;
        _path = Path.GetFullPath(path);
    }

    public string Resolve(WorkspaceReference workspace)
    {
        if (!string.Equals(workspace.ProjectId, _workspace.ProjectId, StringComparison.Ordinal)
            || !string.Equals(workspace.WorkspaceId, _workspace.WorkspaceId, StringComparison.Ordinal))
        {
            throw new LocalWorkspaceResolutionException(
                $"no local workspace binding for '{workspace.ProjectId}/{workspace.WorkspaceId}'");
        }
        return _path;
    }
}
