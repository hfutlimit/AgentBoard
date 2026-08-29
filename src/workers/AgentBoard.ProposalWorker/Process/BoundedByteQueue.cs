// SPDX-License-Identifier: MIT
using System.Text;

namespace AgentBoard.ProposalWorker.Process;

/// <summary>
/// Bounded FIFO byte buffer that keeps at most <c>MaxBytes</c> of the
/// most recently appended data. Replaces the previous
/// read-all-then-Tail approach in <see cref="ProcessExecutor"/>, which
/// buffered the full process output in memory before truncating to
/// <c>MaxOutputBytes</c> — a runaway CLI could OOM the worker. The
/// 2026-08-29 review called this out as a long-standing debt item.
///
/// Memory bound: at most one "kept tail" of <c>MaxBytes</c> bytes at
/// a time, plus the chunk currently being appended (always &lt;=
/// MaxBytes by design). With <c>MaxBytes = 64 KB</c> the per-stream
/// cost is 64 KB; with the production default of 4 MB it is 4 MB.
/// <see cref="ProcessExecutor"/> instantiates one per stream
/// (stdout + stderr).
///
/// UTF-8 boundary handling: <see cref="GetText"/> decodes the kept
/// bytes as UTF-8. If the kept tail starts mid-UTF-8-sequence
/// (because the dropped prefix ended inside a multi-byte char),
/// the <see cref="Encoding.UTF8"/> decoder substitutes U+FFFD for
/// the invalid bytes — acceptable for log output where a few
/// replacement chars at the seam are better than buffering the
/// whole stream just to keep the boundary clean.
/// </summary>
internal sealed class BoundedByteQueue
{
    private readonly Queue<byte[]> _chunks = new();
    private int _totalBytes;
    private readonly int _maxBytes;

    public BoundedByteQueue(int maxBytes)
    {
        _maxBytes = maxBytes > 0 ? maxBytes : 0;
    }

    /// <summary>Total bytes currently held in the queue (≤ MaxBytes).</summary>
    public int TotalBytes => _totalBytes;

    /// <summary>Configured cap.</summary>
    public int MaxBytes => _maxBytes;

    /// <summary>True iff nothing has been appended or MaxBytes is 0.</summary>
    public bool IsEmpty => _totalBytes == 0;

    public void Append(ReadOnlySpan<byte> data)
    {
        if (_maxBytes == 0 || data.IsEmpty) return;

        if (data.Length >= _maxBytes)
        {
            // Single chunk exceeds the cap: keep only the LAST
            // MaxBytes bytes. The current queue is fully replaced.
            _chunks.Clear();
            _totalBytes = 0;
            var trimmed = data.Slice(data.Length - _maxBytes).ToArray();
            _chunks.Enqueue(trimmed);
            _totalBytes = trimmed.Length;
            return;
        }

        // Single chunk fits: enqueue, then drop oldest chunks until
        // total is back under the cap. The single-chunk-fits branch
        // is the steady-state case for normal CLI output.
        _chunks.Enqueue(data.ToArray());
        _totalBytes += data.Length;
        while (_totalBytes > _maxBytes && _chunks.Count > 1)
        {
            var dropped = _chunks.Dequeue();
            _totalBytes -= dropped.Length;
        }
    }

    /// <summary>
    /// Decode the kept tail as UTF-8. Returns <see cref="string.Empty"/>
    /// if nothing was appended or <c>MaxBytes</c> is 0.
    /// </summary>
    public string GetText()
    {
        if (_totalBytes == 0) return string.Empty;
        var combined = new byte[_totalBytes];
        int offset = 0;
        foreach (var chunk in _chunks)
        {
            Buffer.BlockCopy(chunk, 0, combined, offset, chunk.Length);
            offset += chunk.Length;
        }
        return Encoding.UTF8.GetString(combined);
    }
}
