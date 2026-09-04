// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// A versioned cross-boundary contract identifier, in the
/// <c>{name}.v{major}[.{minor}]</c> form used by the doc 151 envelopes
/// (<c>command.v1</c>, <c>execution-event.v1</c>, <c>handoff.v1</c>).
/// </summary>
/// <remarks>
/// doc 151 §11 fixes four rules this type exists to enforce mechanically:
/// a minor bump may add backward-compatible fields, a major bump must be
/// explicitly rejected or migrated, a consumer must ignore optional fields it
/// does not know, and a durable record must never be silently reinterpreted by
/// whatever code version happens to be running.
/// </remarks>
public sealed record SchemaVersion(string Name, int Major, int Minor = 0)
{
    /// <summary>
    /// True when <paramref name="other"/> can consume payloads written by this
    /// version: same contract name and same major. A producer may carry a
    /// higher minor than the consumer understands, because the consumer is
    /// required to ignore fields outside its known set.
    /// </summary>
    public bool IsCompatibleWith(SchemaVersion? other) =>
        other is not null
        && string.Equals(Name, other.Name, StringComparison.Ordinal)
        && Major == other.Major;

    public override string ToString() => $"{Name}.v{Major}.{Minor}";

    public static bool TryParse(string? value, out SchemaVersion version)
    {
        version = null!;
        if (string.IsNullOrWhiteSpace(value)) return false;

        var parts = value.Split('.', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length < 2) return false;

        int major;
        var minor = 0;
        int nameLength;

        if (TryParseMajor(parts[^1], out major))
        {
            // "command.v1" — no explicit minor.
            nameLength = parts.Length - 1;
        }
        else if (parts.Length >= 3
                 && int.TryParse(parts[^1], out minor)
                 && minor >= 0
                 && TryParseMajor(parts[^2], out major))
        {
            // "command.v1.3" — explicit minor.
            nameLength = parts.Length - 2;
        }
        else
        {
            return false;
        }

        if (major < 0) return false;

        var name = string.Join('.', parts[..nameLength]);
        if (name.Length == 0) return false;

        version = new SchemaVersion(name, major, minor);
        return true;
    }

    public static SchemaVersion Parse(string value) =>
        TryParse(value, out var version)
            ? version
            : throw new FormatException(
                $"'{value}' is not a schema version. Expected {{name}}.v{{major}}[.{{minor}}], " +
                "for example 'command.v1' or 'command.v1.3'.");

    private static bool TryParseMajor(string segment, out int major)
    {
        major = 0;
        if (segment.Length < 2 || segment[0] != 'v') return false;
        return int.TryParse(segment.AsSpan(1), out major);
    }
}
