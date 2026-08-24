// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Abstractions;

public interface IProjectAccessService
{
    Task RequireProjectReadAsync(int projectId, CancellationToken ct = default);
    Task RequireProjectWriteAsync(int projectId, CancellationToken ct = default);
    Task RequireProjectOwnerAsync(int projectId, CancellationToken ct = default);
    Task RequireEpicReadAsync(int epicId, CancellationToken ct = default);
    Task RequireEpicWriteAsync(int epicId, CancellationToken ct = default);
    Task RequireStoryReadAsync(int storyId, CancellationToken ct = default);
    Task RequireStoryWriteAsync(int storyId, CancellationToken ct = default);
    Task RequireTaskReadAsync(int taskId, CancellationToken ct = default);
    Task RequireTaskWriteAsync(int taskId, CancellationToken ct = default);
    Task RequireCommentReadAsync(int commentId, CancellationToken ct = default);
    Task RequireCommentWriteAsync(int commentId, CancellationToken ct = default);
    Task RequireAttachmentReadAsync(int attachmentId, CancellationToken ct = default);
    Task RequireAttachmentWriteAsync(int attachmentId, CancellationToken ct = default);
    Task RequireMemberManagementAsync(int projectId, CancellationToken ct = default);

    /// <summary>
    /// Returns null for an administrator (all projects are visible), an empty
    /// set for an anonymous caller, or the caller's member project ids.
    /// </summary>
    Task<IReadOnlySet<int>?> GetAccessibleProjectIdsAsync(CancellationToken ct = default);
    Task<bool> IsCurrentUserAdminAsync(CancellationToken ct = default);
}
