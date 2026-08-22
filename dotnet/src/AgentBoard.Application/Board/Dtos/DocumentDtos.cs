// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

public sealed record DocumentDto(
    int Id,
    int ProjectId,
    int? EpicId,
    int? StoryId,
    int? FolderId,
    string Title,
    string Content,
    string Type,
    string Status,
    int? AuthorId,
    int CurrentRevisionId,
    int CurrentRevisionNumber,
    DateTime CreatedAt,
    DateTime UpdatedAt);

public sealed record DocumentCreateRequest(
    int? ProjectId,
    int? EpicId,
    int? StoryId,
    int? FolderId,
    string? Title,
    string? Content,
    string? Type,
    int? AuthorId);

public sealed record DocumentPatchRequest(
    string? Title,
    string? Content,
    string? Type,
    int? FolderId,
    int? EpicId,
    int? StoryId);

public sealed record DocumentStatusRequest(string? Status);

public sealed record DocumentRevisionDto(
    int Id,
    int DocumentId,
    int RevisionNumber,
    int? AuthorId,
    string Author,
    string Content,
    string? ChangeNote,
    DateTime CreatedAt);

public sealed record RevisionSaveRequest(string? Content, string? ChangeNote, string? Author);

public sealed record RevisionRestoreRequest(string? ChangeNote, string? Author);

public sealed record DocumentCommentDto(
    int Id,
    int DocumentId,
    int? AuthorId,
    string Author,
    string Content,
    DateTime CreatedAt,
    DateTime UpdatedAt);

public sealed record DocumentCommentCreateRequest(string? Author, string? Content);

public sealed record DocumentCommentUpdateRequest(string? Content);

public sealed record DocumentFolderDto(
    int Id,
    int ProjectId,
    int? ParentId,
    string Name,
    DateTime CreatedAt,
    DateTime UpdatedAt);

public sealed record DocumentFolderCreateRequest(int? ProjectId, int? ParentId, string? Name);

public sealed record DocumentFolderPatchRequest(string? Name, int? ParentId, bool? MoveToRoot);
