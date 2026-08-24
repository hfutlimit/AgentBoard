// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Abstractions;

public interface IProjectAccessService
{
    Task RequireMemberManagementAsync(int projectId, CancellationToken ct = default);
}
