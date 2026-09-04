// SPDX-License-Identifier: MIT
namespace AgentBoard.Node.Platform;

/// <summary>
/// Cross-platform view of the host the Node is running on (v4.3 §2.9).
/// </summary>
/// <remarks>
/// <para>
/// This is the contract that keeps M0.4 (IPC transport), M1.2 (SQLite path)
/// and M1.0 / M1.1 (service install) free of per-platform branching. v4.3 §1.3
/// pins the two data roots: LOCALAPPDATA/AgentBoard on Windows and
/// ~/Library/Application Support/AgentBoard on macOS. Note the macOS side is
/// the Application Support directory, not a dotfile under home — a dotfile home
/// path is a Windows-ism and is wrong for a GUI-session LaunchAgent.
/// </para>
/// <para>
/// <see cref="HasGui"/> matters because a Node installed as a service runs in
/// session 0 with no desktop: it cannot open a browser to complete a Provider
/// OAuth flow, so the Portal must surface "re-auth required" instead of trying
/// to launch a browser that will never appear.
/// </para>
/// </remarks>
public interface IPlatformInfo
{
    /// <summary>Host operating system.</summary>
    NodeOs Os { get; }

    /// <summary>Host CPU architecture, limited to the v4.3 support matrix.</summary>
    NodeArch Arch { get; }

    /// <summary>User profile directory (USERPROFILE on Windows, home on Unix).</summary>
    string UserHome { get; }

    /// <summary>
    /// Node-owned data directory. Everything the Node persists (SQLite, run
    /// sockets, logs) lives under this root.
    /// </summary>
    string LocalDataRoot { get; }

    /// <summary>Service supervisor used to install / start / stop the Node.</summary>
    ServiceManagerKind ServiceManager { get; }

    /// <summary>Account name the process runs as.</summary>
    string CurrentUserName { get; }

    /// <summary>True when running with administrative / root rights.</summary>
    bool IsElevated { get; }

    /// <summary>True when a desktop session is attached (not service session 0).</summary>
    bool HasGui { get; }
}
