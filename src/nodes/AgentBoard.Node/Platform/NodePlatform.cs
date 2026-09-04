// SPDX-License-Identifier: MIT
using System.Runtime.InteropServices;

namespace AgentBoard.Node.Platform;

/// <summary>
/// Operating systems the Node supports. v4.3 §1 fixes the matrix at Win +
/// macOS from Day 1 — there is no Linux target and no "unknown" escape hatch,
/// because every downstream decision (service manager, data root, IPC
/// transport, notarisation) branches on this value.
/// </summary>
public enum NodeOs
{
    Windows,
    MacOS,
}

/// <summary>
/// CPU architectures the Node ships for. v4.3 §1.3 fixes the support matrix at
/// exactly two per OS: Windows x64 / arm64 and macOS x64 (Intel) / arm64
/// (Apple Silicon). Anything else is an unsupported host, not a third value.
/// </summary>
public enum NodeArch
{
    X64,
    Arm64,
}

/// <summary>
/// The platform's service supervisor. Consumed by M1.0 (Windows Service via
/// sc) and M1.1 (macOS LaunchAgent via launchd) so install / start / stop do
/// not re-discover the host OS on their own.
/// </summary>
public enum ServiceManagerKind
{
    Sc,
    Launchd,
}

/// <summary>
/// Maps the running host onto <see cref="NodeArch"/>. Shared by both platform
/// implementations so the "what counts as a supported host" rule lives in
/// exactly one place — a Windows-only copy of this switch is how the two
/// implementations would drift apart on the arm64 / x64 split.
/// </summary>
internal static class NodeArchDetector
{
    internal static NodeArch Detect() => RuntimeInformation.OSArchitecture switch
    {
        Architecture.X64 => NodeArch.X64,
        Architecture.Arm64 => NodeArch.Arm64,
        var other => throw new PlatformNotSupportedException(
            $"v4.3 supports x64 and arm64 only; this host reports {other}. " +
            "Widening the matrix is an architecture change, not a local fix."),
    };
}
