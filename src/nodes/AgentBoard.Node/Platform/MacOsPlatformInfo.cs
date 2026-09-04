// SPDX-License-Identifier: MIT
namespace AgentBoard.Node.Platform;

/// <summary>
/// macOS implementation of <see cref="IPlatformInfo"/> (v4.3 M0.2).
/// </summary>
/// <remarks>
/// <para>
/// Data root follows v4.3 §1.3: <c>~/Library/Application Support/AgentBoard</c>,
/// built from <see cref="Environment.SpecialFolder.UserProfile"/> so it tracks
/// the real home directory even when <c>HOME</c> has been rewritten. This is
/// deliberately not <c>~/.agentboard</c> — a dotfile under home is a
/// Windows-ism, and on macOS it also lands outside the directory LaunchAgent
/// services are expected to use.
/// </para>
/// <para>
/// The directory is created lazily by whichever component first writes to it
/// (SQLite in M1.2, the run socket directory in M0.4); this type only reports
/// where it belongs, so probing <see cref="LocalDataRoot"/> never touches the
/// filesystem.
/// </para>
/// </remarks>
public sealed class MacOsPlatformInfo : IPlatformInfo
{
    private readonly IUserIdentity _identity;

    public MacOsPlatformInfo(IUserIdentity identity)
    {
        _identity = identity ?? throw new ArgumentNullException(nameof(identity));
    }

    public NodeOs Os => NodeOs.MacOS;

    /// <summary>
    /// Host architecture mapped onto the v4.3 two-value matrix: x64 (Intel) or
    /// arm64 (Apple Silicon). Throws outside that matrix for the same reason as
    /// the Windows implementation — see <see cref="NodeArchDetector"/>.
    /// </summary>
    public NodeArch Arch => NodeArchDetector.Detect();

    public string UserHome =>
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

    public string LocalDataRoot => Path.Combine(UserHome, "Library", "Application Support", "AgentBoard");

    public ServiceManagerKind ServiceManager => ServiceManagerKind.Launchd;

    public string CurrentUserName => _identity.CurrentUserName;

    public bool IsElevated => _identity.IsElevated;

    /// <summary>
    /// True when the process runs inside an Aqua (GUI) login session.
    /// </summary>
    /// <remarks>
    /// The Node ships as a per-user LaunchAgent, which runs in the user's Aqua
    /// session and inherits <c>SECURITYSESSIONID</c> from <c>loginwindow</c>. A
    /// LaunchDaemon runs in the system context and does not have it. That
    /// distinction is what decides whether the Portal may open a browser to
    /// complete a Provider OAuth flow, so a wrong answer here shows up as a
    /// login attempt that silently never appears.
    /// <para>
    /// This is the one member that cannot be verified by unit test on any host
    /// other than a real macOS login session; it is on the M7.5 real-machine
    /// verification checklist.
    /// </para>
    /// </remarks>
    public bool HasGui
    {
        get
        {
            if (!OperatingSystem.IsMacOS()) return false;

            var aquaSession = Environment.GetEnvironmentVariable("SECURITYSESSIONID");
            return !string.IsNullOrWhiteSpace(aquaSession);
        }
    }
}
