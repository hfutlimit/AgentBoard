// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Abstractions;

/// <summary>
/// Repository contracts for the Stage 2 scheduling aggregates. Lives in
/// its own file (instead of being appended to the existing read-only
/// repository file) so the existing read-only surface is untouched —
/// module 4 ships its own contracts and the root session can choose to
/// merge them at integration time.
///
/// Module 2 (Agents) will add domain-specific query helpers (e.g. by
/// <c>AgentRegistryId</c>, by status + schedule). Keep this file thin:
/// just the <see cref="IRepository{T}"/> surface that the controller
/// actually calls. Module 2 is expected to extend with projection
/// methods once its entity surface stabilises.
/// </summary>
public interface IAgentScheduleRepository : IRepository<AgentSchedule> { }

/// <summary>Repository for <see cref="AgentRun"/>. The default
/// <see cref="IRepository{T}"/> surface is enough for the Stage 2
/// controller; module 2 may add <c>ListByScheduleAsync(int scheduleId, status?, ...)</c>
/// when its review-timeout scan needs a streaming query.</summary>
public interface IAgentRunRepository : IRepository<AgentRun> { }
