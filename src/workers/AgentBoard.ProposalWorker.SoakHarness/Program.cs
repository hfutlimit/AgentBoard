// SPDX-License-Identifier: MIT
using System.Diagnostics;
using AgentBoard.ProposalWorker.SoakHarness;

// Entry point. Parses CLI args and runs a single soak iteration.
//
// Usage:
//   dotnet run --project src/workers/AgentBoard.ProposalWorker.SoakHarness -- \
//       --duration 1h --throughput 50 --concurrency 100 --db tmp/soak.db

var opts = SoakOptions.Parse(args);
Console.WriteLine($"[soak] opts: {opts}");

var driver = new SoakDriver(opts);
using var cts = new CancellationTokenSource();

// Wire SIGINT/SIGTERM-equivalent (Ctrl+C in console).
Console.CancelKeyPress += (_, e) =>
{
    Console.Error.WriteLine("[soak] Ctrl+C received; stopping after current window");
    e.Cancel = true;
    cts.Cancel();
};

var runTask = driver.RunAsync(cts.Token);
try
{
    await runTask;
}
catch (OperationCanceledException)
{
    Console.Error.WriteLine("[soak] cancelled");
}
catch (Exception ex)
{
    Console.Error.WriteLine($"[soak] FATAL: {ex}");
    return 1;
}

Console.WriteLine("[soak] completed cleanly");
return 0;
