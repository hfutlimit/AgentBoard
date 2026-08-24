// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Abstractions;

public interface IProjectAccessService
{
    Task RequireProjectReadAsync(int projectId, CancellationToken ct = default);
    Task RequireProjectWriteAsync(int projectId, CancellationToken ct = default);
    Task RequireProjectOwnerAsync(int projectId, CancellationToken ct = default);
    Task RequireMemberManagementAsync(int projectId, CancellationToken ct = default);

    /// <summary>
    /// Returns null for an administrator (all projects are visible), an empty
    /// set for an anonymous caller, or the caller's member project ids.
    /// </summary>
    Task<IReadOnlySet<int>?> GetAccessibleProjectIdsAsync(CancellationToken ct = default);
    Task<bool> IsCurrentUserAdminAsync(CancellationToken ct = default);
}
