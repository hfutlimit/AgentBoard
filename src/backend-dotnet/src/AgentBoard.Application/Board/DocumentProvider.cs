// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Board;

public sealed class DocumentProvider : IDocumentProvider
{
    private readonly IDocumentRepository _docs;
    private readonly IDocumentRevisionRepository _revisions;
    private readonly IDocumentCommentRepository _comments;
    private readonly IDocumentFolderRepository _folders;
    private readonly IUnitOfWork _uow;

    private static readonly HashSet<string> ValidTypes = new() { "memory", "plan", "knowledge", "design" };
    private static readonly HashSet<string> ValidStatuses = new() { "draft", "in_review", "approved", "cancelled" };
    private static readonly Dictionary<string, HashSet<string>> StatusTransitions = new()
    {
        ["draft"] = new() { "in_review" },
        ["in_review"] = new() { "approved", "cancelled", "draft" },
        ["approved"] = new() { "draft" },
        ["cancelled"] = new(),
    };

    public DocumentProvider(
        IDocumentRepository docs, IDocumentRevisionRepository revisions,
        IDocumentCommentRepository comments, IDocumentFolderRepository folders,
        IUnitOfWork uow)
    {
        _docs = docs; _revisions = revisions; _comments = comments;
        _folders = folders; _uow = uow;
    }

    public async Task<(IReadOnlyList<DocumentDto> Items, int Total)> ListDocumentsAsync(
        int? projectId, string? type, string? status, string? q, int? folderId, int limit, int offset, CancellationToken ct)
    {
        var all = await _docs.ListAsync(d =>
            (projectId == null || d.ProjectId == projectId) &&
            (type == null || d.Type == type) &&
            (status == null || d.Status == status) &&
            (folderId == null ? d.FolderId == null : d.FolderId == folderId) &&
            (q == null || d.Title.Contains(q)), ct);
        var total = all.Count;
        var page = all.OrderByDescending(d => d.Id).Skip(offset).Take(limit).ToList();
        return (page.Select(ToDto).ToList(), total);
    }

    public async Task<DocumentDto?> GetDocumentAsync(int id, CancellationToken ct)
    {
        var d = await _docs.GetByIdAsync(id, ct);
        return d is null ? null : ToDto(d);
    }

    public async Task<DocumentDto> CreateDocumentAsync(
        int projectId, string? title, string? content, string? type, int? authorId, int? epicId, int? storyId, int? folderId, CancellationToken ct)
    {
        title = (title ?? string.Empty).Trim();
        if (title.Length == 0 || title.Length > 300)
            throw new InvalidValueException("title must be 1-300 characters");
        type = type ?? "plan";
        if (!ValidTypes.Contains(type))
            throw new InvalidValueException($"type must be one of: {string.Join(", ", ValidTypes)}");

        var now = DateTime.UtcNow;
        var doc = new Document
        {
            ProjectId = projectId, Title = title, Content = content ?? string.Empty,
            Type = type, Status = "draft", AuthorId = authorId,
            EpicId = epicId, StoryId = storyId, FolderId = folderId,
            CurrentRevisionId = 0, CurrentRevisionNumber = 0,
            CreatedAt = now, UpdatedAt = now,
        };
        await _docs.AddAsync(doc, ct);
        await _uow.SaveChangesAsync(ct);

        // Create first revision
        var rev = new DocumentRevision
        {
            DocumentId = doc.Id, RevisionNumber = 1, AuthorId = authorId,
            Author = authorId?.ToString() ?? "system", Content = doc.Content,
            ChangeNote = "Initial version", CreatedAt = now,
        };
        await _revisions.AddAsync(rev, ct);
        doc.CurrentRevisionId = rev.Id;
        doc.CurrentRevisionNumber = 1;
        _docs.Update(doc);
        await _uow.SaveChangesAsync(ct);
        return ToDto(doc);
    }

    public async Task<DocumentDto?> UpdateDocumentAsync(int id, string? title, string? content, string? type, int? folderId, int? epicId, int? storyId, CancellationToken ct)
    {
        var doc = await _docs.GetByIdAsync(id, ct);
        if (doc is null) return null;
        if (title is not null) { title = title.Trim(); if (title.Length > 0) doc.Title = title; }
        if (content is not null) doc.Content = content;
        if (type is not null && ValidTypes.Contains(type)) doc.Type = type;
        if (folderId is not null || epicId is not null || storyId is not null)
        {
            if (folderId.HasValue) doc.FolderId = folderId;
            if (epicId.HasValue) doc.EpicId = epicId;
            if (storyId.HasValue) doc.StoryId = storyId;
        }
        doc.UpdatedAt = DateTime.UtcNow;
        _docs.Update(doc);

        // If content changed, create a new revision
        if (content is not null)
        {
            var rev = new DocumentRevision
            {
                DocumentId = doc.Id, RevisionNumber = doc.CurrentRevisionNumber + 1,
                AuthorId = doc.AuthorId, Author = doc.AuthorId?.ToString() ?? "system",
                Content = content, CreatedAt = DateTime.UtcNow,
            };
            await _revisions.AddAsync(rev, ct);
            doc.CurrentRevisionId = rev.Id;
            doc.CurrentRevisionNumber = rev.RevisionNumber;
        }
        await _uow.SaveChangesAsync(ct);
        return ToDto(doc);
    }

    public async Task<bool> DeleteDocumentAsync(int id, CancellationToken ct)
    {
        var doc = await _docs.GetByIdAsync(id, ct);
        if (doc is null) return false;
        // Cascade: delete revisions, comments
        var revs = await _revisions.ListAsync(r => r.DocumentId == id, ct);
        _revisions.RemoveRange(revs);
        var cmts = await _comments.ListAsync(c => c.DocumentId == id, ct);
        _comments.RemoveRange(cmts);
        _docs.Remove(doc);
        await _uow.SaveChangesAsync(ct);
        return true;
    }

    public async Task<DocumentDto?> SetDocumentStatusAsync(int id, string? status, CancellationToken ct)
    {
        var doc = await _docs.GetByIdAsync(id, ct);
        if (doc is null || status is null) return null;
        status = status.Trim().ToLowerInvariant();
        if (!ValidStatuses.Contains(status))
            throw new InvalidValueException($"status must be one of: {string.Join(", ", ValidStatuses)}");
        if (!StatusTransitions.TryGetValue(doc.Status, out var allowed) || !allowed.Contains(status))
            throw new InvalidValueException($"cannot transition from '{doc.Status}' to '{status}'");
        doc.Status = status;
        doc.UpdatedAt = DateTime.UtcNow;
        _docs.Update(doc);
        await _uow.SaveChangesAsync(ct);
        return ToDto(doc);
    }

    // ---- Comments ----

    public async Task<int> CountDocumentCommentsAsync(int documentId, CancellationToken ct) =>
        (int)await _comments.CountAsync(c => c.DocumentId == documentId, ct);

    public async Task<(IReadOnlyList<DocumentCommentDto> Items, int Total)> ListDocumentCommentsAsync(int documentId, CancellationToken ct)
    {
        var all = await _comments.ListAsync(c => c.DocumentId == documentId, ct);
        return (all.OrderBy(c => c.CreatedAt).Select(ToCommentDto).ToList(), all.Count);
    }

    public async Task<DocumentCommentDto> CreateDocumentCommentAsync(int documentId, string? author, string? content, CancellationToken ct)
    {
        if (await _docs.GetByIdAsync(documentId, ct) is null)
            throw new NotFoundException($"document {documentId} not found");
        author = (author ?? string.Empty).Trim();
        content = (content ?? string.Empty).Trim();
        if (author.Length == 0 || content.Length == 0)
            throw new InvalidValueException("author and content are required");
        var now = DateTime.UtcNow;
        var cmt = new DocumentComment { DocumentId = documentId, Author = author, Content = content, CreatedAt = now, UpdatedAt = now };
        await _comments.AddAsync(cmt, ct);
        await _uow.SaveChangesAsync(ct);
        return ToCommentDto(cmt);
    }

    public async Task<DocumentCommentDto?> UpdateDocumentCommentAsync(int commentId, string? content, string author, CancellationToken ct)
    {
        var cmt = await _comments.GetByIdAsync(commentId, ct);
        if (cmt is null) return null;
        if (content is not null) cmt.Content = content;
        cmt.UpdatedAt = DateTime.UtcNow;
        _comments.Update(cmt);
        await _uow.SaveChangesAsync(ct);
        return ToCommentDto(cmt);
    }

    public async Task<bool> DeleteDocumentCommentAsync(int commentId, CancellationToken ct)
    {
        var cmt = await _comments.GetByIdAsync(commentId, ct);
        if (cmt is null) return false;
        _comments.Remove(cmt);
        await _uow.SaveChangesAsync(ct);
        return true;
    }

    // ---- Revisions ----

    public async Task<(IReadOnlyList<DocumentRevisionDto> Items, int Total)> ListRevisionsAsync(int documentId, int limit, int offset, CancellationToken ct)
    {
        var all = await _revisions.ListAsync(r => r.DocumentId == documentId, ct);
        var total = all.Count;
        var page = all.OrderByDescending(r => r.RevisionNumber).Skip(offset).Take(limit).ToList();
        return (page.Select(ToRevisionDto).ToList(), total);
    }

    public async Task<DocumentRevisionDto?> GetRevisionAsync(int documentId, int revisionNumber, CancellationToken ct)
    {
        var rev = (await _revisions.ListAsync(r => r.DocumentId == documentId && r.RevisionNumber == revisionNumber, ct)).FirstOrDefault();
        return rev is null ? null : ToRevisionDto(rev);
    }

    public async Task<DocumentRevisionDto> SaveRevisionAsync(int documentId, string? content, string? changeNote, string? author, CancellationToken ct)
    {
        var doc = await _docs.GetByIdAsync(documentId, ct) ?? throw new NotFoundException($"document {documentId} not found");
        content = content ?? string.Empty;
        var now = DateTime.UtcNow;
        var rev = new DocumentRevision
        {
            DocumentId = documentId, RevisionNumber = doc.CurrentRevisionNumber + 1,
            Author = author ?? "system", Content = content, ChangeNote = changeNote, CreatedAt = now,
        };
        await _revisions.AddAsync(rev, ct);
        doc.CurrentRevisionId = rev.Id;
        doc.CurrentRevisionNumber = rev.RevisionNumber;
        doc.Content = content;
        doc.UpdatedAt = now;
        _docs.Update(doc);
        await _uow.SaveChangesAsync(ct);
        return ToRevisionDto(rev);
    }

    public async Task<DocumentRevisionDto> RestoreRevisionAsync(int documentId, int revisionNumber, string? changeNote, string? author, CancellationToken ct)
    {
        var doc = await _docs.GetByIdAsync(documentId, ct) ?? throw new NotFoundException($"document {documentId} not found");
        var oldRev = (await _revisions.ListAsync(r => r.DocumentId == documentId && r.RevisionNumber == revisionNumber, ct)).FirstOrDefault()
            ?? throw new NotFoundException($"revision {revisionNumber} not found for document {documentId}");
        var now = DateTime.UtcNow;
        var newRev = new DocumentRevision
        {
            DocumentId = documentId, RevisionNumber = doc.CurrentRevisionNumber + 1,
            Author = author ?? "system", Content = oldRev.Content,
            ChangeNote = changeNote ?? $"Restored from revision {revisionNumber}", CreatedAt = now,
        };
        await _revisions.AddAsync(newRev, ct);
        doc.CurrentRevisionId = newRev.Id;
        doc.CurrentRevisionNumber = newRev.RevisionNumber;
        doc.Content = oldRev.Content;
        doc.UpdatedAt = now;
        _docs.Update(doc);
        await _uow.SaveChangesAsync(ct);
        return ToRevisionDto(newRev);
    }

    // ---- Folders ----

    public async Task<(IReadOnlyList<DocumentFolderDto> Items, int Total)> ListFoldersAsync(int? projectId, int? parentId, CancellationToken ct)
    {
        var all = await _folders.ListAsync(f =>
            (projectId == null || f.ProjectId == projectId) &&
            (parentId == null ? f.ParentId == null : f.ParentId == parentId), ct);
        return (all.Select(ToFolderDto).ToList(), all.Count);
    }

    public async Task<DocumentFolderDto> CreateFolderAsync(int projectId, int? parentId, string? name, CancellationToken ct)
    {
        name = (name ?? string.Empty).Trim();
        if (name.Length == 0 || name.Length > 300) throw new InvalidValueException("name must be 1-300 characters");
        var now = DateTime.UtcNow;
        var folder = new DocumentFolder { ProjectId = projectId, ParentId = parentId, Name = name, CreatedAt = now, UpdatedAt = now };
        await _folders.AddAsync(folder, ct);
        await _uow.SaveChangesAsync(ct);
        return ToFolderDto(folder);
    }

    public async Task<DocumentFolderDto?> UpdateFolderAsync(int id, string? name, int? parentId, bool? moveToRoot, CancellationToken ct)
    {
        var f = await _folders.GetByIdAsync(id, ct);
        if (f is null) return null;
        if (name is not null) { name = name.Trim(); if (name.Length > 0) f.Name = name; }
        if (moveToRoot == true) f.ParentId = null;
        else if (parentId.HasValue) f.ParentId = parentId;
        f.UpdatedAt = DateTime.UtcNow;
        _folders.Update(f);
        await _uow.SaveChangesAsync(ct);
        return ToFolderDto(f);
    }

    public async Task<bool> DeleteFolderAsync(int id, CancellationToken ct)
    {
        var f = await _folders.GetByIdAsync(id, ct);
        if (f is null) return false;
        // Move children to parent
        var children = await _folders.ListAsync(c => c.ParentId == id, ct);
        foreach (var c in children) { c.ParentId = f.ParentId; c.UpdatedAt = DateTime.UtcNow; _folders.Update(c); }
        // Move documents to parent
        var docs = await _docs.ListAsync(d => d.FolderId == id, ct);
        foreach (var d in docs) { d.FolderId = f.ParentId; d.UpdatedAt = DateTime.UtcNow; _docs.Update(d); }
        _folders.Remove(f);
        await _uow.SaveChangesAsync(ct);
        return true;
    }

    // ---- Mappers ----

    private static DocumentDto ToDto(Document d) => new(d.Id, d.ProjectId, d.EpicId, d.StoryId, d.FolderId,
        d.Title, d.Content, d.Type, d.Status, d.AuthorId, d.CurrentRevisionId, d.CurrentRevisionNumber, d.CreatedAt, d.UpdatedAt);
    private static DocumentCommentDto ToCommentDto(DocumentComment c) => new(c.Id, c.DocumentId, c.AuthorId, c.Author, c.Content, c.CreatedAt, c.UpdatedAt);
    private static DocumentRevisionDto ToRevisionDto(DocumentRevision r) => new(r.Id, r.DocumentId, r.RevisionNumber, r.AuthorId, r.Author, r.Content, r.ChangeNote, r.CreatedAt);
    private static DocumentFolderDto ToFolderDto(DocumentFolder f) => new(f.Id, f.ProjectId, f.ParentId, f.Name, f.CreatedAt, f.UpdatedAt);
}
