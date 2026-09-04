// SPDX-License-Identifier: MIT
using System.Runtime.InteropServices;

namespace AgentBoard.Node.Platform;

/// <summary>
/// Single place that maps the running host onto its <see cref="IUserIdentity"/>
/// and <see cref="IPlatformInfo"/> implementations.
/// </summary>
/// <remarks>
/// <para>
/// The factory is the single host-to-implementation mapping, so consumers never
/// carry an <c>OperatingSystem.Is*</c> branch and <c>Program.cs</c> needs no
/// platform wiring.
/// </para>
/// <para>
/// A host outside the v4.3 matrix throws instead of falling back. Reporting a
/// Windows data root or the <c>sc</c> service manager on macOS would corrupt the
/// very paths M1.1 and M1.2 depend on; a startup failure that names the host is
/// strictly better than a Node that boots and then writes to the wrong place.
/// </para>
/// </remarks>
public static class PlatformFactory
{
    public static IUserIdentity CreateUserIdentity()
    {
        if (OperatingSystem.IsWindows()) return new WindowsUserIdentity();
        if (OperatingSystem.IsMacOS()) return new MacOsUserIdentity();

        throw Unsupported();
    }

    public static IPlatformInfo CreatePlatformInfo(IUserIdentity identity)
    {
        ArgumentNullException.ThrowIfNull(identity);

        if (OperatingSystem.IsWindows()) return new WindowsPlatformInfo(identity);
        if (OperatingSystem.IsMacOS()) return new MacOsPlatformInfo(identity);

        throw Unsupported();
    }

    private static PlatformNotSupportedException Unsupported()
    {
        // RuntimeInformation is the descriptive source; OperatingSystem is the
        // guard. Both are read so the message names the host we actually got.
        var description = RuntimeInformation.OSDescription;
        return new PlatformNotSupportedException(
            $"AgentBoard.Node has no platform implementation for this host ({description}). " +
            "v4.3 supports Windows (M0.1) and macOS (M0.2) only.");
    }
}
