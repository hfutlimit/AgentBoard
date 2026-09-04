// SPDX-License-Identifier: MIT
using System.Runtime.InteropServices;

namespace AgentBoard.Node.Platform;

/// <summary>
/// Single place that maps the running host onto its <see cref="IUserIdentity"/>
/// and <see cref="IPlatformInfo"/> implementations.
/// </summary>
/// <remarks>
/// <para>
/// The factory exists so M0.2 (macOS implementations) is a two-line change here
/// plus the new files — no DI wiring in <c>Program.cs</c> and no
/// <c>OperatingSystem.Is*</c> branches scattered across consumers.
/// </para>
/// <para>
/// Until M0.2 lands, a macOS host throws instead of silently falling back to the
/// Windows implementations. Reporting a Windows data root or <c>sc</c> service
/// manager on macOS would corrupt the very paths M1.2 and M1.1 depend on; a
/// startup failure with a clear message is strictly better.
/// </para>
/// </remarks>
public static class PlatformFactory
{
    public static IUserIdentity CreateUserIdentity()
    {
        if (OperatingSystem.IsWindows()) return new WindowsUserIdentity();

        throw Unsupported();
    }

    public static IPlatformInfo CreatePlatformInfo(IUserIdentity identity)
    {
        ArgumentNullException.ThrowIfNull(identity);

        if (OperatingSystem.IsWindows()) return new WindowsPlatformInfo(identity);

        throw Unsupported();
    }

    private static PlatformNotSupportedException Unsupported()
    {
        // RuntimeInformation is the descriptive source; OperatingSystem is the
        // guard. Both are read so the message names the host we actually got.
        var description = RuntimeInformation.OSDescription;
        return new PlatformNotSupportedException(
            $"AgentBoard.Node has no platform implementation for this host ({description}). " +
            "v4.3 M0 covers Windows (M0.1) and macOS (M0.2).");
    }
}
