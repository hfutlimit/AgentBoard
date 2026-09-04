// SPDX-License-Identifier: MIT
using System.Globalization;
using System.Runtime.InteropServices;

namespace AgentBoard.Node.Platform;

/// <summary>
/// macOS implementation of <see cref="IUserIdentity"/>: the account is
/// identified by its numeric UID.
/// </summary>
/// <remarks>
/// <para>
/// <see cref="PrincipalId"/> carries the real UID (<c>getuid</c>) rather than
/// the effective one, so an operator reading the value from the Portal sees the
/// account the Node was launched as — not the account it escalated to.
/// <see cref="IsElevated"/> deliberately reads the effective UID
/// (<c>geteuid</c>), which is what actually decides whether the Node can touch
/// root-owned paths.
/// </para>
/// <para>
/// Both calls are behind an <c>OperatingSystem.IsMacOS()</c> guard so the libc
/// P/Invoke is never reached on Windows, where <c>DllImport("libc")</c> would
/// fail to resolve.
/// </para>
/// </remarks>
public sealed class MacOsUserIdentity : IUserIdentity
{
    public string CurrentUserName { get; }
    public string PrincipalId { get; }
    public bool IsElevated { get; }

    public MacOsUserIdentity()
    {
        CurrentUserName = Environment.UserName;
        PrincipalId = ReadUid();
        IsElevated = ReadElevation();
    }

    private static string ReadUid()
    {
        if (!OperatingSystem.IsMacOS()) return string.Empty;

        try
        {
            return getuid().ToString(CultureInfo.InvariantCulture);
        }
        catch (Exception)
        {
            return string.Empty;
        }
    }

    private static bool ReadElevation()
    {
        if (!OperatingSystem.IsMacOS()) return false;

        try
        {
            return geteuid() == 0;
        }
        catch (Exception)
        {
            return false;
        }
    }

    [DllImport("libc")]
    private static extern uint getuid();

    [DllImport("libc")]
    private static extern uint geteuid();
}
