// SPDX-License-Identifier: MIT
using System.Security.Claims;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Domain.Common;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;

namespace AgentBoard.Api.Realtime;

/// <summary>
/// SignalR hub for proposal real-time notifications.
///
/// P1-5: previously this hub used <c>Clients.All</c> to broadcast
/// <see cref="QuestionRaisedEvent"/>, which let every connected browser
/// receive metadata for proposals they could not see. The hub now exposes
/// explicit <c>JoinProject</c> / <c>LeaveProject</c> methods so each
/// connection only listens to the projects it is allowed to read. The
/// server validates project membership (or admin) at join time via
/// <see cref="IProjectAccessService"/>; <see cref="DomainException"/>s
/// surface as a 4xx close-frame rather than silently joining the group.
/// </summary>
[Authorize]
public sealed class ProposalHub : Hub
{
    public const string QuestionRaisedEvent = "ProposalQuestionRaised";
    public const string ProjectGroupPrefix = "project:";

    private readonly IProjectAccessService _access;
    private readonly ILogger<ProposalHub> _log;

    public ProposalHub(IProjectAccessService access, ILogger<ProposalHub> log)
    {
        _access = access ?? throw new ArgumentNullException(nameof(access));
        _log = log ?? throw new ArgumentNullException(nameof(log));
    }

    /// <summary>Join the <c>project:{id}</c> SignalR group for a single project.</summary>
    public async Task JoinProject(int projectId)
    {
        // DomainExceptionFilter / the access service will surface a 403 close
        // frame if the caller is not a member; we still re-check here so the
        // hub returns a meaningful error string for hub-level diagnostics.
        try
        {
            await _access.RequireProjectReadAsync(projectId, Context.ConnectionAborted);
        }
        catch (Exception ex)
        {
            _log.LogInformation("ProposalHub.JoinProject rejected for {User} on project {ProjectId}: {Reason}",
                CallerUid(), projectId, ex.Message);
            throw;
        }
        await Groups.AddToGroupAsync(Context.ConnectionId, ProjectGroup(projectId));
    }

    /// <summary>Leave the <c>project:{id}</c> group on navigation away.</summary>
    public async Task LeaveProject(int projectId) =>
        await Groups.RemoveFromGroupAsync(Context.ConnectionId, ProjectGroup(projectId));

    public static string ProjectGroup(int projectId) => $"{ProjectGroupPrefix}{projectId}";

    private string? CallerUid() => Context.User?.FindFirstValue("uid");
}

public sealed record ProposalQuestionRaisedNotification(
    int ProposalId,
    int ProjectId,
    int Round,
    string Workflow,
    string Event);
