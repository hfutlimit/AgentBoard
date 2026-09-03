// SPDX-License-Identifier: MIT
namespace AgentBoard.Node.Execution;

/// <summary>
/// A pending inbox row that the Dispatcher is about to claim and
/// run. Returned by <see cref="InboxStore.GetOldestPendingFlightsAsync"/>
/// in DB-first order (<c>id ASC</c>) so the dispatcher's oldest-first
/// selection is the same as the DB's own order.
///
/// In the round-7 architecture the bounded
/// <see cref="ExecutionChannel"/> no longer carries this record —
/// it carries a <see cref="WakeSignal"/> sentinel. The Dispatcher
/// pulls <see cref="InFlightExecution"/> rows directly from the DB
/// inbox on every wake, so this type is the "next flight" record
/// rather than an in-memory channel payload.
/// </summary>
public sealed record InFlightExecution(ExecutionRequest Request, long InboxId);
