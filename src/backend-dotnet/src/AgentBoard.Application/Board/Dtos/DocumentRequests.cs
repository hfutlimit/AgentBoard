// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Request body for <c>POST /api/documents</c>. All fields optional except validation in provider.</summary>
public sealed record CreateDocumentRequest(
    int? ProjectId,
    string? Title,
    string? Content,
    string? Type,
    int? AuthorId,
    int? EpicId,
    int? StoryId,
    int? FolderId);

/// <summary>Request body for <c>PATCH /api/documents/{id}</c>. All fields optional; null = leave unchanged.</summary>
public sealed record UpdateDocumentRequest(
    string? Title,
    string? Content,
    string? Type,
    int? FolderId,
    int? EpicId,
    int? StoryId);

/// <summary>Request body for <c>PATCH /api/folders/{id}</c>. All fields optional.</summary>
public sealed record UpdateFolderRequest(
    string? Name,
    int? ParentId,
    bool? MoveToRoot);

/// <summary>Request body for saving a new document revision.</summary>
public sealed record SaveRevisionRequest(
    string? Content,
    string? ChangeNote,
    string? Author);

/// <summary>Request body for restoring an older document revision.</summary>
public sealed record RestoreRevisionRequest(
    string? ChangeNote,
    string? Author);
