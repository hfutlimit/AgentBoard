// SPDX-License-Identifier: MIT
namespace AgentBoard.Domain.Common.Enums;

/// <summary>
/// Work item status, shared by tasks, bugs, stories and epics.
/// The integer values are the ones exposed in the REST contract;
/// the status machine (see Tasks/StateMachine) validates transitions.
/// </summary>
public enum Status
{
    Backlog = 1,
    Todo = 2,
    InProgress = 3,
    InReview = 4,
    Verifying = 5,
    Done = 6,
}
