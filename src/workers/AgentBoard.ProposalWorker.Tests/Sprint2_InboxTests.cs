using AgentBoard.ProposalWorker;
using AgentBoard.ProposalWorker.Execution;
using AgentBoard.ProposalWorker.Tests.Fixtures;
using Xunit;

namespace AgentBoard.ProposalWorker.Tests;

/// <summary>
/// Sprint 2: Inbox + idempotency + poison-message self-verification.
/// Per-test TempDb → no cross-test UNIQUE conflicts.
/// </summary>
public sealed class Sprint2_InboxTests : IDisposable
{
    private readonly TempDbFixture _fx = new();
    private long _nextId = 2000;

    public void Dispose() => _fx.Dispose();

    private ExecutionRequest Req(string agent = "test") => new(
        ExecutionKey: $"proposal:{_nextId++}:0:{agent}",
        WorkloadType: "proposal",
        WorkloadId: _nextId - 1,
        AgentType: agent,
        Round: 0,
        Source: "test",
        PayloadJson: "{}");

    // -------------------------------------------------------------------------
    // Idempotency — same execution_key 10x → 1 row
    // -------------------------------------------------------------------------

    [Fact]
    public async Task TryEnqueueAsync_is_idempotent_for_same_execution_key()
    {
        var request = Req();
        var results = new List<(long InboxId, bool IsNew)>();
        for (var i = 0; i < 10; i++)
            results.Add(await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None));

        Assert.Single(results, r => r.IsNew);
        Assert.Equal(9, results.Count(r => !r.IsNew));
        Assert.Single(results.Select(r => r.InboxId).Distinct());
    }

    [Fact]
    public async Task TryEnqueueAsync_different_keys_each_get_new_row()
    {
        var r1 = Req();
        var r2 = Req();
        var r3 = Req();
        var (id1, n1) = await _fx.Inbox.TryEnqueueAsync(r1, CancellationToken.None);
        var (id2, n2) = await _fx.Inbox.TryEnqueueAsync(r2, CancellationToken.None);
        var (id3, n3) = await _fx.Inbox.TryEnqueueAsync(r3, CancellationToken.None);

        Assert.True(n1); Assert.True(n2); Assert.True(n3);
        Assert.NotEqual(id1, id2);
        Assert.NotEqual(id2, id3);
    }

    // -------------------------------------------------------------------------
    // Claim — pending → dispatching is CAS
    // -------------------------------------------------------------------------

    [Fact]
    public async Task TryClaimAsync_only_one_concurrent_caller_wins()
    {
        var request = Req();
        var (inboxId, _) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);

        var t1 = _fx.Inbox.TryClaimAsync(inboxId, CancellationToken.None);
        var t2 = _fx.Inbox.TryClaimAsync(inboxId, CancellationToken.None);
        var t3 = _fx.Inbox.TryClaimAsync(inboxId, CancellationToken.None);
        var results = await Task.WhenAll(t1, t2, t3);

        Assert.Single(results, r => r == InboxStore.TryClaimOutcome.Claimed);
        Assert.Equal(2, results.Count(r => r == InboxStore.TryClaimOutcome.AlreadyClaimed));
    }

    [Fact]
    public async Task TryClaimAsync_returns_false_when_already_dispatching()
    {
        var request = Req();
        var (inboxId, _) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        Assert.Equal(InboxStore.TryClaimOutcome.Claimed, await _fx.Inbox.TryClaimAsync(inboxId, CancellationToken.None));
        Assert.Equal(InboxStore.TryClaimOutcome.AlreadyClaimed, await _fx.Inbox.TryClaimAsync(inboxId, CancellationToken.None));
    }

    // -------------------------------------------------------------------------
    // Startup reset — dispatching → pending on boot
    // -------------------------------------------------------------------------

    [Fact]
    public async Task ResetStuckDispatchingAsync_resets_stuck_rows()
    {
        var request = Req();
        var (inboxId, _) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        await _fx.Inbox.TryClaimAsync(inboxId, CancellationToken.None);

        Assert.Equal("dispatching", (await _fx.Inbox.GetAsync(inboxId, CancellationToken.None))!.Status);

        var n = await _fx.Inbox.ResetStuckDispatchingAsync(CancellationToken.None);
        Assert.Equal(1, n);

        Assert.Equal("pending", (await _fx.Inbox.GetAsync(inboxId, CancellationToken.None))!.Status);
    }

    [Fact]
    public async Task ResetStuckDispatchingAsync_does_not_touch_completed()
    {
        var request = Req();
        var (inboxId, _) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        await _fx.Inbox.TryClaimAsync(inboxId, CancellationToken.None);
        await _fx.Inbox.MarkCompletedAsync(inboxId, CancellationToken.None);

        var n = await _fx.Inbox.ResetStuckDispatchingAsync(CancellationToken.None);
        Assert.Equal(0, n);

        Assert.Equal("completed", (await _fx.Inbox.GetAsync(inboxId, CancellationToken.None))!.Status);
    }

    // -------------------------------------------------------------------------
    // Poison message — invalid payload → InvalidDataException
    // -------------------------------------------------------------------------

    [Theory]
    [InlineData("{}")]                          // missing proposal_id
    [InlineData("{\"proposal_id\":0}")]         // zero proposal_id
    [InlineData("{\"proposal_id\":-1}")]        // negative proposal_id
    public void ProposalMessage_Parse_rejects_invalid_payload(string raw)
    {
        Assert.Throws<InvalidDataException>(() => ProposalMessage.Parse(System.Text.Encoding.UTF8.GetBytes(raw)));
    }

    [Fact]
    public void ProposalMessage_Parse_rejects_malformed_json()
    {
        // Malformed JSON is rejected at parse time (System.Text.Json).
        // The InvalidDataException wrapper in production would catch this
        // upstream; here we just assert *some* exception is raised.
        Assert.ThrowsAny<Exception>(() => ProposalMessage.Parse(System.Text.Encoding.UTF8.GetBytes("not json at all")));
    }

    [Fact]
    public void ProposalMessage_Parse_accepts_legacy_payload_without_agent_type()
    {
        var msg = ProposalMessage.Parse(System.Text.Encoding.UTF8.GetBytes(
            "{\"proposal_id\":42,\"round\":3,\"reason\":\"r\",\"ts\":\"t\"}"));
        Assert.Equal(42, msg.ProposalId);
        Assert.Equal(3, msg.Round);
        Assert.Null(msg.AgentType);
    }

    [Fact]
    public void ProposalMessage_Parse_includes_agent_type_when_present()
    {
        var msg = ProposalMessage.Parse(System.Text.Encoding.UTF8.GetBytes(
            "{\"proposal_id\":42,\"round\":0,\"reason\":\"r\",\"ts\":\"t\",\"agent_type\":\"codex\"}"));
        Assert.Equal("codex", msg.AgentType);
    }

    // -------------------------------------------------------------------------
    // Inbox state machine
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Inbox_lifecycle_pending_to_completed()
    {
        var request = Req();
        var (inboxId, isNew) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        Assert.True(isNew);

        Assert.Equal(InboxStore.TryClaimOutcome.Claimed, await _fx.Inbox.TryClaimAsync(inboxId, CancellationToken.None));
        Assert.Equal("dispatching", (await _fx.Inbox.GetAsync(inboxId, CancellationToken.None))!.Status);

        await _fx.Inbox.MarkCompletedAsync(inboxId, CancellationToken.None);
        Assert.Equal("completed", (await _fx.Inbox.GetAsync(inboxId, CancellationToken.None))!.Status);
    }

    [Fact]
    public async Task Inbox_attempt_counter_increments_on_each_claim()
    {
        var request = Req();
        var (inboxId, _) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        Assert.Equal(1, (await _fx.Inbox.GetAsync(inboxId, CancellationToken.None))!.Attempt);

        await _fx.Inbox.TryClaimAsync(inboxId, CancellationToken.None);
        Assert.Equal(2, (await _fx.Inbox.GetAsync(inboxId, CancellationToken.None))!.Attempt);
    }

    // -------------------------------------------------------------------------
    // 2026-09-02 P0-3 round-11: partial UNIQUE index lets a re-dispatched
    // execution_key re-enter the inbox once the previous attempt reached a
    // terminal state. Before this change the legacy full-column UNIQUE
    // index permanently blocked retry of any execution_key that had ever
    // reached 'completed' / 'failed' / 'cancelled', so a one-shot failure
    // (e.g. adapter crash before the proposal advanced) stranded the
    // proposal forever. See InboxStore.cs for the index migration.
    // -------------------------------------------------------------------------

    [Fact]
    public async Task TryEnqueueAsync_allows_new_attempt_after_completed()
    {
        var request = Req();
        var (firstId, isNew) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        Assert.True(isNew);
        await _fx.Inbox.TryClaimAsync(firstId, CancellationToken.None);
        await _fx.Inbox.MarkCompletedAsync(firstId, CancellationToken.None);

        // Same execution_key after the prior attempt completed must produce
        // a brand-new pending row, not be silently dropped as a duplicate.
        var (secondId, secondIsNew) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        Assert.True(secondIsNew);
        Assert.NotEqual(firstId, secondId);
        Assert.Equal("pending", (await _fx.Inbox.GetAsync(secondId, CancellationToken.None))!.Status);
    }

    [Fact]
    public async Task TryEnqueueAsync_allows_new_attempt_after_failed()
    {
        var request = Req();
        var (firstId, _) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        await _fx.Inbox.TryClaimAsync(firstId, CancellationToken.None);
        await _fx.Inbox.MarkFailedAsync(firstId, "boom", CancellationToken.None);

        var (secondId, secondIsNew) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        Assert.True(secondIsNew);
        Assert.NotEqual(firstId, secondId);
        Assert.Equal("pending", (await _fx.Inbox.GetAsync(secondId, CancellationToken.None))!.Status);
    }

    [Fact]
    public async Task TryEnqueueAsync_still_dedupes_while_pending()
    {
        // The partial index keeps idempotency for the common case: a
        // Rabbit redelivery of the same message before the worker has
        // finished the first attempt must still be a no-op.
        var request = Req();
        var (firstId, isNew) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        Assert.True(isNew);
        var (secondId, secondIsNew) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        Assert.False(secondIsNew);
        Assert.Equal(firstId, secondId);
    }

    [Fact]
    public async Task TryEnqueueAsync_still_dedupes_while_dispatching()
    {
        // In-flight attempts also must not double-enqueue, otherwise two
        // dispatchers could race the same execution. This protects the
        // CAS guarantee that TryClaimAsync relies on.
        var request = Req();
        var (firstId, _) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        await _fx.Inbox.TryClaimAsync(firstId, CancellationToken.None);

        var (secondId, secondIsNew) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        Assert.False(secondIsNew);
        Assert.Equal(firstId, secondId);
    }

    [Fact]
    public async Task TryEnqueueAsync_keeps_history_but_does_not_block_pending_dispatch()
    {
        // After a failed attempt the dispatcher (which picks
        // status='pending' ORDER BY id ASC) must be able to run the new
        // attempt even though the legacy failed row is still in the
        // table. Verify by claiming the new row and confirming we can
        // also list it via ListPendingAsync.
        var request = Req();
        var (firstId, _) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        await _fx.Inbox.TryClaimAsync(firstId, CancellationToken.None);
        await _fx.Inbox.MarkFailedAsync(firstId, "boom", CancellationToken.None);

        var (secondId, secondIsNew) = await _fx.Inbox.TryEnqueueAsync(request, CancellationToken.None);
        Assert.True(secondIsNew);

        // Dispatcher picks pending rows. The legacy failed row must not
        // shadow the new pending row.
        var pending = await _fx.Inbox.ListPendingAsync(CancellationToken.None);
        Assert.Contains(pending, r => r.InboxId == secondId);
        Assert.DoesNotContain(pending, r => r.InboxId == firstId);
    }
}
