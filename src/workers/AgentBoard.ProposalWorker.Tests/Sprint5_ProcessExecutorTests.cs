using AgentBoard.ProposalWorker.Process;
using Xunit;

namespace AgentBoard.ProposalWorker.Tests;

/// <summary>
/// Sprint 5: ProcessExecutor self-verification. Spawns real Windows processes
/// (cmd / powershell) — no mocks, no DI. Each test owns its own spec and
/// CancellationTokenSource so they can run in any order.
///
/// Total wall clock &lt; 30s; mostly bounded by the timeout test (~3-5s).
/// </summary>
public sealed class Sprint5_ProcessExecutorTests
{
    private readonly ProcessExecutor _exec = new();

    // -------------------------------------------------------------------------
    // 1. Timeout — long-running process killed at timeout boundary
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Timeout_kills_long_running_process()
    {
        // ping localhost: each default echo waits ~1s for reply before
        // moving to the next. 30 pings ≈ 30s. Killed at 2s timeout.
        // We use the absolute path because Sprint 5's env-isolation fix
        // clears the child env (including PATH), so the bare "ping" name
        // wouldn't resolve.
        var spec = new ProcessSpec
        {
            Executable = @"C:\Windows\System32\ping.exe",
            Arguments = new[] { "-n", "30", "127.0.0.1" },
            Timeout = TimeSpan.FromSeconds(2),
            KillGrace = TimeSpan.FromSeconds(2),
        };
        var sw = System.Diagnostics.Stopwatch.StartNew();
        var result = await _exec.ExecuteAsync(spec, CancellationToken.None);
        sw.Stop();

        Assert.True(result.TimedOut, $"expected TimedOut=true; exit={result.ExitCode} stderr={result.StderrTail}");
        Assert.True(sw.Elapsed < TimeSpan.FromSeconds(15), $"elapsed {sw.Elapsed} should be < 15s");
    }

    // -------------------------------------------------------------------------
    // 2. Cancel — caller cancels via CancellationToken
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Cancel_via_caller_token_kills_process()
    {
        var cts = new CancellationTokenSource();
        cts.CancelAfter(TimeSpan.FromMilliseconds(800));

        var spec = new ProcessSpec
        {
            Executable = @"C:\Windows\System32\ping.exe",
            Arguments = new[] { "-n", "30", "127.0.0.1" },
            // Generous timeout so it doesn't fire; cancellation is the trigger.
            Timeout = TimeSpan.FromSeconds(30),
        };
        var sw = System.Diagnostics.Stopwatch.StartNew();
        var result = await _exec.ExecuteAsync(spec, cts.Token);
        sw.Stop();

        Assert.True(result.Cancelled, $"expected Cancelled=true; exit={result.ExitCode} stderr={result.StderrTail}");
        Assert.False(result.TimedOut, "cancelled should not be reported as timeout");
        Assert.True(sw.Elapsed < TimeSpan.FromSeconds(15), $"elapsed {sw.Elapsed} should be < 15s");
    }

    // -------------------------------------------------------------------------
    // 3. Buffer tail — large output limited to MaxOutputBytes
    // -------------------------------------------------------------------------

    [Fact]
    public async Task OutputTail_is_limited_to_MaxOutputBytes()
    {
        // 5000 lines * 40 chars ≈ 200KB + newlines. Comfortably exceeds 100KB.
        var payload = new string('A', 40);
        var spec = new ProcessSpec
        {
            Executable = "cmd",
            Arguments = new[] { "/c", $"for /L %i in (1,1,5000) do @echo {payload}" },
            MaxOutputBytes = 100 * 1024,
        };
        var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

        Assert.True(result.ExitCode == 0, $"cmd exit {result.ExitCode}, stderr: {result.StderrTail}");
        Assert.True(result.OutputTail.Length <= 100 * 1024, $"OutputTail length {result.OutputTail.Length}");
    }

    [Fact]
    public async Task OutputTail_preserves_tail_when_truncated()
    {
        // When output is truncated, the LAST bytes are kept (so the JSON
        // decision at the end of agent output is still visible).
        var payload = "TAIL_MARKER_" + new string('A', 30);
        var spec = new ProcessSpec
        {
            Executable = "cmd",
            Arguments = new[] { "/c", $"for /L %i in (1,1,5000) do @echo {payload}" },
            MaxOutputBytes = 4 * 1024,  // tiny limit so we MUST truncate
        };
        var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

        Assert.True(result.OutputTail.Length <= 4 * 1024);
        Assert.Contains("TAIL_MARKER_", result.OutputTail);  // last line preserved
    }

    // -------------------------------------------------------------------------
    // 4. Redaction — secrets in stdout are stripped
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Redaction_replaces_openai_api_key()
    {
        var spec = new ProcessSpec
        {
            Executable = "cmd",
            Arguments = new[] { "/c", "echo OPENAI_API_KEY=sk-12345abcdef" },
        };
        var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

        Assert.NotNull(result.RedactedOutput);
        Assert.DoesNotContain("sk-12345abcdef", result.RedactedOutput);
        Assert.Contains("***REDACTED***", result.RedactedOutput);
    }

    [Fact]
    public async Task Redaction_replaces_anthropic_api_key()
    {
        var spec = new ProcessSpec
        {
            Executable = "cmd",
            Arguments = new[] { "/c", "echo anthropic_api_key: sk-ant-very-long-key" },
        };
        var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

        Assert.DoesNotContain("sk-ant-very-long-key", result.RedactedOutput);
        Assert.Contains("***REDACTED***", result.RedactedOutput);
    }

    [Fact]
    public async Task Redaction_replaces_authorization_bearer()
    {
        var spec = new ProcessSpec
        {
            Executable = "cmd",
            Arguments = new[] { "/c", "echo Authorization: Bearer eyJabcdefghijk" },
        };
        var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

        Assert.DoesNotContain("eyJabcdefghijk", result.RedactedOutput);
        Assert.Contains("***REDACTED***", result.RedactedOutput);
    }

    [Fact]
    public async Task Redaction_does_not_touch_non_secret_text()
    {
        var plainText = "plain text 12345 with no api keys here";
        var spec = new ProcessSpec
        {
            Executable = "cmd",
            Arguments = new[] { "/c", $"echo {plainText}" },
        };
        var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

        Assert.NotNull(result.RedactedOutput);
        Assert.Contains("plain text 12345 with no api keys here", result.RedactedOutput);
    }

    // -------------------------------------------------------------------------
    // 5. Env isolation — parent env vars do NOT leak to child
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Environment_isolation_does_not_inherit_parent_env()
    {
        // Use a GUID-based name to avoid collisions with anything real.
        var envName = $"WORKER_TEST_PARENT_{Guid.NewGuid():N}".Substring(0, 40);
        Environment.SetEnvironmentVariable(envName, "should_not_leak");
        try
        {
            // cmd: echo %FOO% — if FOO is in env, prints its value; otherwise
            // prints the literal "%FOO%". So an isolated child should output
            // the literal form, not the value.
            var spec = new ProcessSpec
            {
                Executable = "cmd",
                Arguments = new[] { "/c", $"echo %{envName}%" },
                Environment = new Dictionary<string, string?>(),
            };
            var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

            Assert.Equal(0, result.ExitCode);
            Assert.DoesNotContain("should_not_leak", result.OutputTail);
            Assert.Contains($"%{envName}%", result.OutputTail);
        }
        finally
        {
            Environment.SetEnvironmentVariable(envName, null);
        }
    }

    [Fact]
    public async Task Environment_specified_vars_are_passed_to_child()
    {
        // Sanity check: if spec.Environment HAS the var, the child DOES see it.
        var envName = $"WORKER_TEST_PASSED_{Guid.NewGuid():N}".Substring(0, 40);
        try
        {
            var spec = new ProcessSpec
            {
                Executable = "cmd",
                Arguments = new[] { "/c", $"echo %{envName}%" },
                Environment = new Dictionary<string, string?> { [envName] = "explicitly_passed" },
            };
            var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

            Assert.Equal(0, result.ExitCode);
            Assert.Contains("explicitly_passed", result.OutputTail);
        }
        finally
        {
            Environment.SetEnvironmentVariable(envName, null);
        }
    }

    // -------------------------------------------------------------------------
    // 6. Successful exec — happy path
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Successful_exec_returns_exit_code_zero()
    {
        var spec = new ProcessSpec
        {
            Executable = "cmd",
            Arguments = new[] { "/c", "echo hello world" },
        };
        var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

        Assert.Equal(0, result.ExitCode);
        Assert.Contains("hello world", result.OutputTail);
        Assert.False(result.TimedOut);
        Assert.False(result.Cancelled);
        Assert.True(result.Duration > TimeSpan.Zero);
    }

    // -------------------------------------------------------------------------
    // 7. Empty executable — defensive guard, no throw
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Empty_executable_returns_error_without_throwing()
    {
        var spec = new ProcessSpec { Executable = "" };
        var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

        Assert.Equal(-1, result.ExitCode);
        Assert.Contains("executable not configured", result.StderrTail);
    }

    // -------------------------------------------------------------------------
    // 8. .cmd / .bat wrapper — npm-global installs ship as .cmd wrappers;
    // Process.Start does not resolve them directly (Win32Exception 193), so
    // the executor transparently re-spawns through cmd.exe. The tests below
    // prove the wrapper works and that .exe paths are NOT double-wrapped.
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Cmd_wrapper_runs_a_dot_cmd_script_via_cmd_exe()
    {
        // Build a one-line .cmd script on disk and execute it. The script
        // echoes a sentinel so we can verify the wrapper path executed
        // successfully (exit 0, sentinel present in stdout).
        var scriptPath = Path.Combine(
            Path.GetTempPath(),
            $"worker-cmdwrap-{Guid.NewGuid():N}.cmd");
        await File.WriteAllTextAsync(scriptPath, "@echo CMD_WRAPPER_OK\r\n");
        try
        {
            var spec = new ProcessSpec
            {
                Executable = scriptPath,
                // No /c — the wrapper should add it. This proves the
                // executor split the .cmd out of spec.Arguments and
                // prepended it after `cmd /c`.
                Arguments = Array.Empty<string>(),
                Timeout = TimeSpan.FromSeconds(5),
            };
            var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

            Assert.Equal(0, result.ExitCode);
            Assert.Contains("CMD_WRAPPER_OK", result.OutputTail);
        }
        finally
        {
            try { File.Delete(scriptPath); } catch { /* best-effort */ }
        }
    }

    [Fact]
    public async Task Cmd_wrapper_does_not_wrap_absolute_exe_path()
    {
        // C:\Windows\System32\ping.exe is an .exe, not a wrapper. The
        // executor must NOT prepend cmd /c — otherwise ping would not
        // understand /c as an argument and would fail to start.
        var spec = new ProcessSpec
        {
            Executable = @"C:\Windows\System32\ping.exe",
            Arguments = new[] { "-n", "1", "127.0.0.1" },
            Timeout = TimeSpan.FromSeconds(5),
        };
        var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

        Assert.True(result.ExitCode == 0,
            $"ping should exit 0; got exit={result.ExitCode} stderr={result.StderrTail}");
    }

    // -------------------------------------------------------------------------
    // 9. Streaming bounded buffer — BoundedByteQueue unit tests
    //    The previous ReadToEndAsync buffered the full stdout/stderr in
    //    memory and only truncated post-mortem. A runaway CLI could OOM
    //    the worker. BoundedByteQueue keeps at most MaxBytes at any time,
    //    verified directly here.
    // -------------------------------------------------------------------------

    [Fact]
    public void BoundedByteQueue_under_max_returns_all_data()
    {
        var q = new BoundedByteQueue(maxBytes: 100);
        q.Append("hello"u8);
        q.Append(" "u8);
        q.Append("world"u8);
        Assert.Equal("hello world", q.GetText());
        Assert.Equal(11, q.TotalBytes);
    }

    [Fact]
    public void BoundedByteQueue_over_max_keeps_only_last_max_bytes()
    {
        var q = new BoundedByteQueue(maxBytes: 10);
        // 20 chunks of 5 bytes each = 100 bytes total. Only the last
        // 10 bytes should survive.
        for (int i = 0; i < 20; i++)
        {
            var chunk = new byte[5];
            Array.Fill(chunk, (byte)('A' + (i % 26)));
            q.Append(chunk);
        }
        var text = q.GetText();
        Assert.Equal(10, text.Length);
        Assert.Equal(10, q.TotalBytes);
        // The kept tail is the last 10 bytes: chunk 18 ('S'*5) +
        // chunk 19 ('T'*5). ASCII so byte count == char count.
        Assert.Equal("SSSSSTTTTT", text);
    }

    [Fact]
    public void BoundedByteQueue_single_chunk_larger_than_max_truncates_to_tail()
    {
        var q = new BoundedByteQueue(maxBytes: 4);
        q.Append("ABCDEFGHIJ"u8);  // 10 bytes, > 4
        Assert.Equal(4, q.TotalBytes);
        Assert.Equal("GHIJ", q.GetText());
    }

    [Fact]
    public void BoundedByteQueue_zero_max_acts_as_black_hole()
    {
        var q = new BoundedByteQueue(maxBytes: 0);
        q.Append("anything"u8);
        Assert.Equal(0, q.TotalBytes);
        Assert.Equal(string.Empty, q.GetText());
    }

    [Fact]
    public void BoundedByteQueue_negative_max_acts_as_black_hole()
    {
        // Defensive: callers must not pass negatives, but the constructor
        // clamps to 0 to make misuse safe.
        var q = new BoundedByteQueue(maxBytes: -5);
        q.Append("anything"u8);
        Assert.Equal(0, q.TotalBytes);
    }

    [Fact]
    public void BoundedByteQueue_empty_append_is_noop()
    {
        var q = new BoundedByteQueue(maxBytes: 100);
        q.Append(ReadOnlySpan<byte>.Empty);
        Assert.Equal(0, q.TotalBytes);
        Assert.Equal(string.Empty, q.GetText());
    }

    // -------------------------------------------------------------------------
    // 10. Integration: huge output is bounded at runtime, not just at
    //     post-mortem. The previous ReadToEndAsync would have read the
    //     full 10 MB into RAM; the new streaming bounded buffer caps
    //     memory at MaxOutputBytes regardless of CLI total output.
    // -------------------------------------------------------------------------

    [Fact]
    public async Task ProcessExecutor_bounds_memory_for_huge_output()
    {
        // 20,000 lines * 50 chars ≈ 1 MB. With a tiny 4 KB cap, only the
        // last 4 KB of stdout should survive, and the LAST emitted line
        // (TAIL_MARKER_HUGE) must be present. The pre-bounded version
        // would have buffered all 1 MB before truncating; the new
        // streaming version drops bytes as they arrive and never holds
        // more than 4 KB at a time.
        var payload = new string('A', 50);
        var spec = new ProcessSpec
        {
            Executable = "cmd",
            Arguments = new[] { "/c", $"for /L %i in (1,1,20000) do @echo TAIL_MARKER_HUGE_{payload}" },
            MaxOutputBytes = 4 * 1024,
            Timeout = TimeSpan.FromSeconds(30),
        };
        var result = await _exec.ExecuteAsync(spec, CancellationToken.None);

        Assert.True(result.ExitCode == 0, $"cmd exit {result.ExitCode}, stderr: {result.StderrTail}");
        Assert.True(result.OutputTail.Length <= 4 * 1024,
            $"OutputTail length {result.OutputTail.Length} exceeds 4 KB cap (streaming should have bounded it at runtime)");
        Assert.Contains("TAIL_MARKER_HUGE_", result.OutputTail);
    }
}
