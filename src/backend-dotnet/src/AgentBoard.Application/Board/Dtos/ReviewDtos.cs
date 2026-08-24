// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Review statistics for a project. Mirrors FastAPI review-stats endpoint.</summary>
public sealed record ReviewStatsDto(
    string ReviewMode,
    int ReviewQuorum,
    ReviewStatsAggregate Stories,
    ReviewStatsAggregate Tasks,
    double AverageRounds,
    double RejectRate,
    int TimeoutPending,
    IReadOnlyList<ReviewReviewerWorkload> ByReviewer,
    IReadOnlyList<ReviewVoteProgress> Votes);

/// <summary>Aggregate counts for a work-item type (stories or tasks).</summary>
public sealed record ReviewStatsAggregate(int Total, int Approved, int Rejected, int Pending, int Blocked);

/// <summary>Per-reviewer workload summary.</summary>
public sealed record ReviewReviewerWorkload(int UserId, string? Username, int Reviewed, int Approve, int Reject);

/// <summary>Vote progress for a single in-review item.</summary>
public sealed record ReviewVoteProgress(int Id, string Title, int Approve, int Reject, int Cast, int Quorum, string Status);

/// <summary>Request body for POST /api/review-stats/reassign-timeout.</summary>
public sealed record ReassignTimeoutRequest(int? TimeoutMinutes, int? MaxPerRun);
