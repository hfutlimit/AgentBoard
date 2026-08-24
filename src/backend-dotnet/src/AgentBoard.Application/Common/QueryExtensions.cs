// SPDX-License-Identifier: MIT
using System.Linq.Expressions;

namespace AgentBoard.Application.Common;

/// <summary>
/// Tiny query-side helpers used by Repository implementations. They are
/// kept in the Application layer so that the Infrastructure layer doesn't
/// need to invent its own — but they operate on <c>IQueryable</c> deliberately
/// because Repositories do need to compose a query before materialisation.
/// </summary>
public static class QueryExtensions
{
    /// <summary>
    /// Applies <paramref name="predicate"/> only when <paramref name="condition"/>
    /// is true; otherwise returns the source unchanged. This avoids the
    /// common <c>(filter == null ? q : q.Where(filter))</c> allocation in
    /// service code.
    /// </summary>
    public static IQueryable<T> WhereIf<T>(
        this IQueryable<T> source,
        bool condition,
        Expression<Func<T, bool>> predicate)
    {
        ArgumentNullException.ThrowIfNull(source);
        ArgumentNullException.ThrowIfNull(predicate);
        return condition ? source.Where(predicate) : source;
    }

    /// <summary>Applies <see cref="PagedRequest"/> skip/take on top of a query.</summary>
    public static IQueryable<T> ApplyPaging<T>(this IQueryable<T> source, PagedRequest page)
    {
        ArgumentNullException.ThrowIfNull(source);
        ArgumentNullException.ThrowIfNull(page);
        return source.Skip(page.Skip).Take(page.Take);
    }
}
