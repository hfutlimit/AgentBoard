// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;

namespace AgentBoard.Application.Board;

/// <summary>
/// Document domain provider. Mirrors FastAPI documents/document-comments/document-folders routers.
/// </summary>
public interface IDocumentProvider : IProvider
{
    // ---- Documents ----
    Task<(IReadOnlyList<DocumentDto> Items, int Total)> ListDocumentsAsync(
        int? projectId, string? type, string? status, string? q, int? folderId,
        int limit, int offset, CancellationToken ct);

    Task<DocumentDto?> GetDocumentAsync(int id, CancellationToken ct);

    Task<DocumentDto> CreateDocumentAsync(
        CreateDocumentRequest request, int projectId, CancellationToken ct);

    Task<DocumentDto?> UpdateDocumentAsync(
        int id, UpdateDocumentRequest request, CancellationToken ct);

    Task<bool> DeleteDocumentAsync(int id, CancellationToken ct);

    Task<DocumentDto?> SetDocumentStatusAsync(int id, string? status, CancellationToken ct);

    // ---- Document Comments ----
    Task<int> CountDocumentCommentsAsync(int documentId, CancellationToken ct);

    Task<(IReadOnlyList<DocumentCommentDto> Items, int Total)> ListDocumentCommentsAsync(
        int documentId, CancellationToken ct);

    Task<DocumentCommentDto> CreateDocumentCommentAsync(
        int documentId, string? author, string? content, CancellationToken ct);

    Task<DocumentCommentDto?> UpdateDocumentCommentAsync(
        int commentId, string? content, string author, CancellationToken ct);

    Task<bool> DeleteDocumentCommentAsync(int commentId, CancellationToken ct);

    // ---- Document Revisions ----
    Task<(IReadOnlyList<DocumentRevisionDto> Items, int Total)> ListRevisionsAsync(
        int documentId, int limit, int offset, CancellationToken ct);

    Task<DocumentRevisionDto?> GetRevisionAsync(int documentId, int revisionNumber, CancellationToken ct);

    Task<DocumentRevisionDto> SaveRevisionAsync(
        int documentId, SaveRevisionRequest request, CancellationToken ct);

    Task<DocumentRevisionDto> RestoreRevisionAsync(
        int documentId, int revisionNumber, RestoreRevisionRequest request, CancellationToken ct);

    // ---- Document Folders ----
    Task<(IReadOnlyList<DocumentFolderDto> Items, int Total)> ListFoldersAsync(
        int? projectId, int? parentId, CancellationToken ct);

    Task<DocumentFolderDto> CreateFolderAsync(
        int projectId, int? parentId, string? name, CancellationToken ct);

    Task<DocumentFolderDto?> UpdateFolderAsync(
        int id, UpdateFolderRequest request, CancellationToken ct);

    Task<bool> DeleteFolderAsync(int id, CancellationToken ct);
}
