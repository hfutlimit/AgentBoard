// SPDX-License-Identifier: MIT
using AgentBoard.Application.Board.Dtos;

namespace AgentBoard.Application.Board;

public interface IProjectLifecycleService
{
	Task<ProjectDto> CreateAsync(
		CreateProjectRequest request, int? currentUserId,
		CancellationToken ct = default);

	Task<bool> DeleteAsync(int projectId, CancellationToken ct = default);
}
