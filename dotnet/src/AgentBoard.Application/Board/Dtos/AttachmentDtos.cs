// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Attachment metadata. Mirrors FastAPI <c>AttachmentOut</c>.</summary>
public sealed record AttachmentDto(
    int Id,
    int TaskId,
    string Filename,
    string OriginalName,
    int Size,
    string MimeType,
    DateTime CreatedAt);

/// <summary>Attachment info (without TaskId). Used for single attachment lookup.</summary>
public sealed record AttachmentInfoDto(
    int Id,
    string Filename,
    string OriginalName,
    int Size,
    string MimeType,
    DateTime CreatedAt);
