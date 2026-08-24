using Microsoft.AspNetCore.SignalR;
using Microsoft.AspNetCore.Authorization;

namespace AgentBoard.Api.Realtime;

[Authorize]
public sealed class ProposalHub : Hub
{
    public const string QuestionRaisedEvent = "ProposalQuestionRaised";
}

public sealed record ProposalQuestionRaisedNotification(
    int ProposalId,
    int ProjectId,
    int Round,
    string Workflow,
    string Event);
