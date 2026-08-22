// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Request body for <c>POST /api/epics</c>. Mirrors FastAPI <c>EpicCreate</c>.
/// All properties are nullable so validation happens in the provider layer (422).</summary>
public sealed record EpicCreateRequest(
    string? Title,
    string? Description);

/// <summary>Request body for <c>PATCH /api/epics/{id}</c>. Mirrors FastAPI <c>EpicPatch</c>.
/// All fields are optional; a null field means "leave unchanged".</summary>
public sealed record EpicPatchRequest(
    string? Title,
    string? Description,
    string? Status);
