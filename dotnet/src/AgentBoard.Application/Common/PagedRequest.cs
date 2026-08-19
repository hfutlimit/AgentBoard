// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Common;

/// <summary>
/// Query-string contract for list endpoints. Mirrors the FastAPI pagination
/// knobs (page, page_size) so the .NET 1:1 contract test in S0-5 passes.
/// </summary>
public sealed record PagedRequest(int Page = 1, int PageSize = 20)
{
    public const int DefaultPageSize = 20;
    public const int MaxPageSize = 200;

    public int Page { get; init; } = Page < 1 ? 1 : Page;
    public int PageSize { get; init; } =
        PageSize switch
        {
            < 1 => DefaultPageSize,
            > MaxPageSize => MaxPageSize,
            _ => PageSize,
        };

    public int Skip => (Page - 1) * PageSize;
    public int Take => PageSize;
}
