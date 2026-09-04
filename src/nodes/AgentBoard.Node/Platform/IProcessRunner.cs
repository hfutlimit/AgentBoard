// SPDX-License-Identifier: MIT
namespace AgentBoard.Node.Platform;

/// <summary>
/// Long-lived process control (v4.3 §2.9), for work that outlives a single
/// "run to completion" call.
/// </summary>
/// <remarks>
/// <para>
/// This deliberately coexists with <c>AgentBoard.Node.Process.IProcessExecutor</c>
/// rather than replacing it. The two answer different questions:
/// <see cref="IProcessExecutor"/> is the batch path — hand it a spec, get back a
/// result with timeout, redaction and a full log file. This is the session path —
/// spawn once, then read incrementally, signal, and wait on demand. M3.4
/// (ProviderSession running PRE → MAIN → POST in one session) and M5 (ACP
/// transports that speak stdio to a long-lived process) need the session shape;
/// the existing one-shot CLI adapters keep the batch shape.
/// </para>
/// <para>
/// What is genuinely platform-specific here is not launching — .NET's
/// <c>Process</c> already abstracts that — but three things it does not:
/// shell wrapping for <c>.cmd</c>/<c>.bat</c>, the absence of SIGTERM on
/// Windows, and how a whole process tree is torn down. That is why the
/// interface has two implementations instead of one.
/// </para>
/// </remarks>
public interface IProcessRunner
{
    Task<IProcessHandle> StartAsync(
        string command,
        IReadOnlyList<string> args,
        ProcessStartOptions options,
        CancellationToken ct);

    /// <summary>
    /// <c>Terminate</c> asks politely and falls back to killing the tree if the
    /// process ignores the request. <c>Kill</c> tears down the tree immediately.
    /// </summary>
    Task KillAsync(IProcessHandle handle, ProcessSignal signal, CancellationToken ct);

    /// <summary>
    /// Bounded read from stdout — returns at most <paramref name="maxBytes"/>
    /// and does not wait for the process to exit, so a long-running Provider
    /// session can be drained incrementally without buffering it whole.
    /// </summary>
    Task<byte[]> ReadOutputAsync(IProcessHandle handle, int maxBytes, CancellationToken ct);

    /// <summary>
    /// Waits up to <paramref name="timeoutMs"/> (or forever when the value is
    /// zero or negative) and reports whether the deadline won.
    /// </summary>
    Task<ProcessExit> WaitAsync(IProcessHandle handle, int timeoutMs, CancellationToken ct);
}
