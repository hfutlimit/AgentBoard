// SPDX-License-Identifier: MIT
namespace AgentBoard.Node.SoakHarness;

/// <summary>
/// Soak harness configuration. Parsed from CLI args.
/// All durations are in seconds internally; CLI accepts "30s" / "5m" / "1h" / "1d".
/// </summary>
public sealed record SoakOptions
{
    public required TimeSpan Duration { get; init; }
    public required int TargetThroughputPerSec { get; init; }
    public required int ChannelCapacity { get; init; }
    public required string DatabasePath { get; init; }
    public required TimeSpan SampleInterval { get; init; }
    public required string ReportPath { get; init; }

    public static SoakOptions Parse(string[] args)
    {
        TimeSpan duration = TimeSpan.FromHours(1);
        int throughput = 50;
        int capacity = 100;
        string db = Path.Combine("tmp", $"soak-{DateTimeOffset.UtcNow:yyyyMMdd-HHmmss}.db");
        TimeSpan sample = TimeSpan.FromSeconds(30);
        string report = Path.Combine("tmp", $"soak-report-{DateTimeOffset.UtcNow:yyyyMMdd-HHmmss}.json");

        for (int i = 0; i < args.Length; i++)
        {
            string Next() => i + 1 < args.Length ? args[++i] : throw new ArgumentException($"missing value for {args[i - 1]}");
            switch (args[i])
            {
                case "--duration": duration = ParseDuration(Next()); break;
                case "--throughput": throughput = int.Parse(Next()); break;
                case "--concurrency": capacity = int.Parse(Next()); break;
                case "--db": db = Next(); break;
                case "--sample": sample = ParseDuration(Next()); break;
                case "--report": report = Next(); break;
                case "-h":
                case "--help":
                    PrintHelp();
                    Environment.Exit(0);
                    break;
                default:
                    throw new ArgumentException($"unknown arg: {args[i]}");
            }
        }

        return new SoakOptions
        {
            Duration = duration,
            TargetThroughputPerSec = throughput,
            ChannelCapacity = capacity,
            DatabasePath = db,
            SampleInterval = sample,
            ReportPath = report,
        };
    }

    public override string ToString() =>
        $"Duration={Duration}, Throughput={TargetThroughputPerSec} req/s, Capacity={ChannelCapacity}, " +
        $"DB={DatabasePath}, Sample={SampleInterval}, Report={ReportPath}";

    private static TimeSpan ParseDuration(string s)
    {
        s = s.Trim().ToLowerInvariant();
        if (s.EndsWith("ms") && int.TryParse(s[..^2], out var ms)) return TimeSpan.FromMilliseconds(ms);
        if (s.EndsWith("s") && int.TryParse(s[..^1], out var sec)) return TimeSpan.FromSeconds(sec);
        if (s.EndsWith("m") && int.TryParse(s[..^1], out var min)) return TimeSpan.FromMinutes(min);
        if (s.EndsWith("h") && int.TryParse(s[..^1], out var hr)) return TimeSpan.FromHours(hr);
        if (s.EndsWith("d") && int.TryParse(s[..^1], out var day)) return TimeSpan.FromDays(day);
        if (int.TryParse(s, out var bare)) return TimeSpan.FromSeconds(bare);
        throw new ArgumentException($"cannot parse duration: {s}");
    }

    private static void PrintHelp()
    {
        Console.WriteLine("""
            Soak harness for AgentBoard ProposalWorker.
            Drives a fake producer against the in-process ExecutionDispatcher +
            Channel + InboxStore stack and samples metrics for the configured
            duration. Default 1h at 50 req/s.

              --duration 1h|30m|300s|1d   how long to run (default 1h)
              --throughput 50             target enqueue rate (req/s)
              --concurrency 100           bounded channel capacity
              --db path                   SQLite database path
              --sample 30s                metrics sample interval
              --report path               JSON report output path
            """);
    }
}
