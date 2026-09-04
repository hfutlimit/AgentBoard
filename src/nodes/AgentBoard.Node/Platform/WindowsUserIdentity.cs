// SPDX-License-Identifier: MIT
using System.Security.Principal;

namespace AgentBoard.Node.Platform;

/// <summary>
/// Windows implementation of <see cref="IUserIdentity"/>: the account is
/// identified by its SID.
/// </summary>
/// <remarks>
/// Every Windows-only call is behind an <c>OperatingSystem.IsWindows()</c>
/// guard rather than a <c>[SupportedOSPlatform]</c> attribute. The attribute
/// would push a platform check onto every caller — including the constructor of
/// <see cref="WindowsPlatformInfo"/> and every unit test — which is precisely
/// the coupling v4.3 fix C-5 removes. Guarding internally keeps the class
/// constructible on any host; it is simply never registered off Windows (see
/// <see cref="PlatformFactory"/>).
/// </remarks>
public sealed class WindowsUserIdentity : IUserIdentity
{
    public string CurrentUserName { get; }
    public string PrincipalId { get; }
    public bool IsElevated { get; }

    public WindowsUserIdentity()
    {
        CurrentUserName = Environment.UserName;
        PrincipalId = ReadSid();
        IsElevated = ReadElevation();
    }

    /// <summary>
    /// Reads the SID of the current Windows identity. Returns an empty string
    /// when the call is unsupported or fails — an unresolvable account is a
    /// diagnosable condition, not a reason to crash during startup.
    /// </summary>
    private static string ReadSid()
    {
        if (!OperatingSystem.IsWindows()) return string.Empty;

        try
        {
            using var identity = WindowsIdentity.GetCurrent();
            return identity.User?.Value ?? string.Empty;
        }
        catch (Exception)
        {
            // GetCurrent() throws when the process is impersonating a
            // non-Windows principal or the token cannot be opened.
            return string.Empty;
        }
    }

    private static bool ReadElevation()
    {
        if (!OperatingSystem.IsWindows()) return false;

        try
        {
            using var identity = WindowsIdentity.GetCurrent();
            return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
        }
        catch (Exception)
        {
            return false;
        }
    }
}
