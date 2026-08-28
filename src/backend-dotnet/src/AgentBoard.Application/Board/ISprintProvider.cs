// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;

namespace AgentBoard.Application.Board;

/// <summary>Sprint CRUD + lifecycle. Mirrors FastAPI sprints router.</summary>
public interface ISprintProvider : IProvider
{
    Task<IReadOnlyList<SprintDto>> ListSprintsAsync(int projectId, CancellationToken ct = default);
    Task<SprintDto?> GetSprintAsync(int id, CancellationToken ct = default);
    Task<SprintDto> CreateSprintAsync(int projectId, CreateSprintRequest request, CancellationToken ct = default);
    Task<SprintDto?> UpdateSprintAsync(int id, UpdateSprintRequest request, CancellationToken ct = default);
    Task<bool> DeleteSprintAsync(int id, CancellationToken ct = default);
    Task<SprintDto?> ActivateSprintAsync(int id, CancellationToken ct = default);
    Task<SprintDto?> CompleteSprintAsync(int id, CancellationToken ct = default);
}
