// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;

namespace AgentBoard.Application.Board;

/// <summary>
/// Read-only query surface for the board domain. Mirrors the FastAPI GET
/// routers 1:1 so the S0-5 contract-freeze tests pass unchanged. All reads
/// go through the FastAPI-owned tables via the read-only repositories.
/// </summary>
public interface IBoardProvider : IProvider
{
    Task<IReadOnlyList<ProjectDto>> ListProjectsAsync(CancellationToken ct = default);
    Task<ProjectDto?> GetProjectAsync(int id, CancellationToken ct = default);

    Task<IReadOnlyList<EpicDto>> ListEpicsAsync(int? projectId, CancellationToken ct = default);
    Task<EpicDto?> GetEpicAsync(int id, CancellationToken ct = default);

    Task<IReadOnlyList<StoryDto>> ListStoriesAsync(int? epicId, CancellationToken ct = default);
    Task<StoryDto?> GetStoryAsync(int id, CancellationToken ct = default);

    Task<IReadOnlyList<TaskItemDto>> ListTasksAsync(int? projectId, int? storyId, CancellationToken ct = default);
    Task<TaskItemDto?> GetTaskAsync(int id, CancellationToken ct = default);

    Task<IReadOnlyList<CommentDto>> ListCommentsAsync(
        int? taskId, int? storyId, int? epicId, CancellationToken ct = default);
    Task<CommentDto?> GetCommentAsync(int id, CancellationToken ct = default);
}
