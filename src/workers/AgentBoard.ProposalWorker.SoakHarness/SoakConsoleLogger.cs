// SPDX-License-Identifier: MIT
using Microsoft.Extensions.Logging;

namespace AgentBoard.ProposalWorker.SoakHarness;

/// <summary>
/// Console logger that only emits warning+ events (so the soak
/// metrics line is not drowned by info spam). Helps debug
/// dispatcher / coordinator behaviour without changing the
/// production code.
/// </summary>
public sealed class SoakConsoleLogger<T> : ILogger<T>
{
    public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
    public bool IsEnabled(LogLevel logLevel) => logLevel >= LogLevel.Warning;

    public void Log<TState>(LogLevel logLevel, EventId eventId, TState state,
        Exception? exception, Func<TState, Exception?, string> formatter)
    {
        if (!IsEnabled(logLevel)) return;
        var msg = formatter(state, exception);
        Console.Error.WriteLine($"[soak:{typeof(T).Name}] {logLevel}: {msg}");
        if (exception != null)
            Console.Error.WriteLine($"[soak:{typeof(T).Name}]   {exception}");
    }
}
