// SPDX-License-Identifier: MIT
namespace AgentBoard.Node.Platform;

/// <summary>
/// Windows implementation of <see cref="IPlatformInfo"/> (v4.3 M0.1).
/// </summary>
/// <remarks>
/// Data root follows v4.3 §1.3: LOCALAPPDATA/AgentBoard, i.e.
/// <c>Environment.SpecialFolder.LocalApplicationData</c> joined with the
/// application name. The directory itself is created lazily by whichever
/// component first writes to it (SQLite in M1.2, the IPC socket directory in
/// M0.4); this type only reports where it belongs, so a read-only probe of
/// <see cref="LocalDataRoot"/> never touches the filesystem.
/// </remarks>
public sealed class WindowsPlatformInfo : IPlatformInfo
{
    private readonly IUserIdentity _identity;

    public WindowsPlatformInfo(IUserIdentity identity)
    {
        _identity = identity ?? throw new ArgumentNullException(nameof(identity));
    }

    public NodeOs Os => NodeOs.Windows;

    /// <summary>
    /// Host architecture mapped onto the v4.3 two-value matrix. Throws for any
    /// architecture outside that matrix rather than silently reporting x64:
    /// a Node that believes it is x64 while running on arm32 would resolve the
    /// wrong Provider binaries and fail much later, at execution time.
    /// </summary>
    public NodeArch Arch => NodeArchDetector.Detect();

    public string UserHome =>
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

    public string LocalDataRoot => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "AgentBoard");

    public ServiceManagerKind ServiceManager => ServiceManagerKind.Sc;

    public string CurrentUserName => _identity.CurrentUserName;

    public bool IsElevated => _identity.IsElevated;

    /// <summary>
    /// True when an interactive desktop session is attached. A Windows Service
    /// runs in session 0, which has no desktop and therefore cannot complete an
    /// interactive Provider OAuth flow; <c>Environment.UserInteractive</c> alone
    /// is not sufficient because it reports true for some service hosts, so the
    /// session id is checked as well.
    /// </summary>
    public bool HasGui
    {
        get
        {
            if (!OperatingSystem.IsWindows()) return false;
            if (!Environment.UserInteractive) return false;

            try
            {
                // Fully qualified: this assembly owns an AgentBoard.Node.Process
                // namespace (the shared process-execution layer), which would
                // otherwise shadow System.Diagnostics.Process here.
                return System.Diagnostics.Process.GetCurrentProcess().SessionId != 0;
            }
            catch (Exception)
            {
                // SessionId can throw once the process handle is invalid;
                // treating it as "no GUI" is the safe direction because it
                // makes the Portal ask for a manual re-auth instead of
                // launching a browser that will never be seen.
                return false;
            }
        }
    }
}
