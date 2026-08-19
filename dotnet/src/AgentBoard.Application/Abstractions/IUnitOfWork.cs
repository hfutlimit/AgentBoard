// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Abstractions;

/// <summary>
/// Transaction boundary. The default implementation piggybacks on EF Core's
/// implicit transaction (one SaveChangesAsync == one transaction), but a
/// multi-statement unit of work is still a useful seam for the API layer:
/// the Provider can start a transaction once, run several Service calls,
/// and commit/rollback as a single unit.
/// </summary>
public interface IUnitOfWork
{
    Task<int> SaveChangesAsync(CancellationToken ct = default);
}
