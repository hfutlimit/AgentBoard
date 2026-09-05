// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Workflow.Durable;
using Xunit;

namespace AgentBoard.Domain.Tests.Durable;

public sealed class TaskStatusProjectionTests
{
    [Fact]
    public void Retry_and_live_claim_cannot_be_overtaken_by_later_statuses_of_the_same_task()
    {
        var now = DateTimeOffset.UtcNow;
        var outbox = new TaskStatusProjectionOutbox(() => now);
        // Reverse lexical ids and equal timestamps must not reverse enqueue order.
        outbox.Enqueue("z-first", "run-1", 42, "in_progress", null, "start");
        outbox.Enqueue("a-last", "run-1", 42, "done", "completed", "finish");
        Assert.Equal("z-first", outbox.BeginNext(TimeSpan.FromMinutes(1))!.ProjectionId);
        Assert.Null(outbox.BeginNext(TimeSpan.FromMinutes(1)));
        outbox.Retry("z-first", "offline", TimeSpan.FromSeconds(5));
        outbox.Enqueue("other", "run-2", 43, "in_progress", null, "start");
        Assert.Equal("other", outbox.BeginNext(TimeSpan.FromMinutes(1))!.ProjectionId);
        outbox.Complete("other");
        Assert.Null(outbox.BeginNext(TimeSpan.FromMinutes(1)));
        now = now.AddSeconds(5);
        Assert.Equal("z-first", outbox.BeginNext(TimeSpan.FromMinutes(1))!.ProjectionId);
        outbox.Complete("z-first");
        Assert.Equal("a-last", outbox.BeginNext(TimeSpan.FromMinutes(1))!.ProjectionId);
    }

    [Fact]
    public void Claim_expiry_and_retry_preserve_a_restart_safe_projection()
    {
        var now = new DateTimeOffset(2026, 9, 5, 0, 0, 0, TimeSpan.Zero);
        var outbox = new TaskStatusProjectionOutbox(() => now);
        outbox.Enqueue("p-1", "run-1", 42, "in_progress", null, "started");

        var first = Assert.IsType<TaskStatusProjection>(outbox.BeginNext(TimeSpan.FromMinutes(1)));
        Assert.Equal(1, first.Attempts);
        Assert.Null(outbox.BeginNext(TimeSpan.FromMinutes(1)));

        now = now.AddMinutes(2);
        var recovered = Assert.IsType<TaskStatusProjection>(outbox.BeginNext(TimeSpan.FromMinutes(1)));
        Assert.Equal(2, recovered.Attempts);
        outbox.Retry(recovered.ProjectionId, "FastAPI unavailable", TimeSpan.FromSeconds(5));

        Assert.Single(outbox.Capture());
        Assert.Null(outbox.BeginNext(TimeSpan.FromMinutes(1)));
        now = now.AddSeconds(5);
        var due = Assert.IsType<TaskStatusProjection>(outbox.BeginNext(TimeSpan.FromMinutes(1)));
        Assert.Equal(3, due.Attempts);
        Assert.Equal(TaskStatusProjectionState.Completed, outbox.Complete(due.ProjectionId).State);
    }
}
