using System.Runtime.InteropServices;
using AgentBoard.Node.Platform;
using Xunit;

namespace AgentBoard.Node.Tests;

/// <summary>
/// M0.1 + M0.2: platform abstraction contracts (v4.3 §2.9) and both platform
/// implementations.
/// </summary>
/// <remarks>
/// Contract assertions and factory assertions run on every host in the M0.6 CI
/// matrix. Implementation assertions are host-guarded with the same
/// <see cref="RuntimeInformation.IsOSPlatform(OSPlatform)"/> early-return style
/// as <see cref="Sprint7_CliLocatorTests"/>.
/// </remarks>
public sealed class M0_PlatformAbstractionTests
{
    // -------------------------------------------------------------------------
    // Contract — must hold on every host in the M0.6 CI matrix
    // -------------------------------------------------------------------------

    [Fact]
    public void IUserIdentity_exposes_platform_neutral_principal_id()
    {
        // v4.3 fix C-5: "sid" is Windows-only vocabulary and must not appear on
        // the cross-platform contract. Guarding the name keeps a future
        // platform from being forced to invent a SID-shaped value.
        Assert.Null(typeof(IUserIdentity).GetProperty("Sid"));
        Assert.NotNull(typeof(IUserIdentity).GetProperty("PrincipalId"));
        Assert.NotNull(typeof(IUserIdentity).GetProperty("CurrentUserName"));
        Assert.NotNull(typeof(IUserIdentity).GetProperty("IsElevated"));
    }

    [Fact]
    public void IPlatformInfo_contract_has_no_platform_specific_member_names()
    {
        var forbidden = new[] { "Windows", "MacOs", "MacOS", "Unix", "Linux", "Sid" };

        foreach (var property in typeof(IPlatformInfo).GetProperties())
        {
            foreach (var term in forbidden)
            {
                Assert.DoesNotContain(term, property.Name, StringComparison.OrdinalIgnoreCase);
            }
        }
    }

    [Fact]
    public void Platform_enums_cover_exactly_the_v4_3_matrix()
    {
        Assert.Equal(new[] { "Windows", "MacOS" }, Enum.GetNames<NodeOs>());
        Assert.Equal(new[] { "X64", "Arm64" }, Enum.GetNames<NodeArch>());
        Assert.Equal(new[] { "Sc", "Launchd" }, Enum.GetNames<ServiceManagerKind>());
    }

    [Fact]
    public void Factory_rejects_a_null_identity()
    {
        Assert.Throws<ArgumentNullException>(() => PlatformFactory.CreatePlatformInfo(null!));
    }

    // -------------------------------------------------------------------------
    // Factory — resolved against whichever host the suite runs on
    // -------------------------------------------------------------------------

    [Fact]
    public void Factory_resolves_the_host_os()
    {
        var expected = ExpectedOs();
        if (expected is null) return; // host outside the v4.3 matrix

        var info = PlatformFactory.CreatePlatformInfo(PlatformFactory.CreateUserIdentity());

        Assert.Equal(expected.Value, info.Os);
    }

    [Fact]
    public void Factory_resolves_a_consistent_identity_and_platform_pair()
    {
        if (ExpectedOs() is null) return;

        var identity = PlatformFactory.CreateUserIdentity();
        var info = PlatformFactory.CreatePlatformInfo(identity);

        // IPlatformInfo duplicates the account fields required by v4.3 §2.9, but
        // they must stay a single source of truth — otherwise /health and the
        // service installer can disagree about which account is running.
        Assert.Equal(Environment.UserName, info.CurrentUserName);
        Assert.Equal(identity.CurrentUserName, info.CurrentUserName);
        Assert.Equal(identity.IsElevated, info.IsElevated);
    }

    [Fact]
    public void Data_root_is_absolute_and_platform_specific()
    {
        if (ExpectedOs() is null) return;

        var info = PlatformFactory.CreatePlatformInfo(PlatformFactory.CreateUserIdentity());

        Assert.True(Path.IsPathRooted(info.LocalDataRoot));
        Assert.EndsWith("AgentBoard", info.LocalDataRoot, StringComparison.Ordinal);

        if (info.Os == NodeOs.MacOS)
        {
            // v4.3 §1.3: macOS uses Application Support, not a dotfile under home.
            Assert.Contains("Application Support", info.LocalDataRoot, StringComparison.Ordinal);
        }
        else
        {
            Assert.Equal(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                Path.GetDirectoryName(info.LocalDataRoot));
        }
    }

    [Fact]
    public void Service_manager_matches_the_platform()
    {
        if (ExpectedOs() is null) return;

        var info = PlatformFactory.CreatePlatformInfo(PlatformFactory.CreateUserIdentity());
        var expected = info.Os == NodeOs.Windows
            ? ServiceManagerKind.Sc
            : ServiceManagerKind.Launchd;

        Assert.Equal(expected, info.ServiceManager);
    }

    [Fact]
    public void Factory_throws_on_a_host_outside_the_v4_3_matrix()
    {
        if (ExpectedOs() is not null) return;

        Assert.Throws<PlatformNotSupportedException>(() => PlatformFactory.CreateUserIdentity());
        Assert.Throws<PlatformNotSupportedException>(
            () => PlatformFactory.CreatePlatformInfo(new StubIdentity()));
    }

    // -------------------------------------------------------------------------
    // Windows implementation
    // -------------------------------------------------------------------------

    [Fact]
    public void Windows_identity_reports_the_current_account_and_sid()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return;

        var identity = new WindowsUserIdentity();

        Assert.False(string.IsNullOrWhiteSpace(identity.CurrentUserName));
        Assert.StartsWith("S-1-", identity.PrincipalId, StringComparison.Ordinal);
    }

    [Fact]
    public void Identity_never_throws_when_the_account_cannot_be_read()
    {
        // Startup must not crash on a host where the token / libc lookup fails.
        // Both implementations swallow the failure and yield empty values.
        IUserIdentity identity = RuntimeInformation.IsOSPlatform(OSPlatform.OSX)
            ? new MacOsUserIdentity()
            : new WindowsUserIdentity();

        Assert.NotNull(identity.PrincipalId);
        Assert.NotNull(identity.CurrentUserName);
    }

    [Fact]
    public void Windows_platform_root_is_localappdata_agentboard()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return;

        var info = new WindowsPlatformInfo(new WindowsUserIdentity());
        var localAppData =
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);

        Assert.Equal(NodeOs.Windows, info.Os);
        Assert.Equal(ServiceManagerKind.Sc, info.ServiceManager);
        Assert.Equal(Path.Combine(localAppData, "AgentBoard"), info.LocalDataRoot);
        Assert.Equal(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            info.UserHome);
    }

    [Fact]
    public void Windows_platform_arch_is_within_the_v4_3_matrix()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return;

        var info = new WindowsPlatformInfo(new WindowsUserIdentity());

        Assert.Contains(info.Arch, new[] { NodeArch.X64, NodeArch.Arm64 });
    }

    [Fact]
    public void Platform_info_delegates_identity_fields_to_IUserIdentity()
    {
        if (ExpectedOs() is null) return;

        var identity = PlatformFactory.CreateUserIdentity();
        var info = PlatformFactory.CreatePlatformInfo(identity);

        Assert.Equal(identity.CurrentUserName, info.CurrentUserName);
        Assert.Equal(identity.IsElevated, info.IsElevated);
    }

    // -------------------------------------------------------------------------
    // macOS implementation
    // -------------------------------------------------------------------------

    [Fact]
    public void MacOs_platform_root_is_application_support_agentboard()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.OSX)) return;

        var info = new MacOsPlatformInfo(new MacOsUserIdentity());
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

        Assert.Equal(NodeOs.MacOS, info.Os);
        Assert.Equal(ServiceManagerKind.Launchd, info.ServiceManager);
        Assert.Equal(
            Path.Combine(home, "Library", "Application Support", "AgentBoard"),
            info.LocalDataRoot);
        Assert.Equal(home, info.UserHome);
    }

    [Fact]
    public void MacOs_identity_principal_id_is_the_numeric_uid()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.OSX)) return;

        var identity = new MacOsUserIdentity();

        Assert.False(string.IsNullOrWhiteSpace(identity.PrincipalId));
        Assert.True(uint.TryParse(identity.PrincipalId, out _));
    }

    [Fact]
    public void MacOs_identity_is_not_elevated_for_an_unprivileged_user()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.OSX)) return;

        // CI runs unprivileged; running the suite as root would break other
        // assumptions long before this assertion.
        Assert.False(new MacOsUserIdentity().IsElevated);
    }

    [Fact]
    public void MacOs_platform_arch_is_within_the_v4_3_matrix()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.OSX)) return;

        var info = new MacOsPlatformInfo(new MacOsUserIdentity());

        Assert.Contains(info.Arch, new[] { NodeArch.X64, NodeArch.Arm64 });
    }

    // -------------------------------------------------------------------------

    private static NodeOs? ExpectedOs()
    {
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return NodeOs.Windows;
        if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX)) return NodeOs.MacOS;
        return null;
    }

    private sealed class StubIdentity : IUserIdentity
    {
        public string CurrentUserName => "stub";
        public string PrincipalId => "stub";
        public bool IsElevated => false;
    }
}
