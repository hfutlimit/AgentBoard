using System.Runtime.InteropServices;
using AgentBoard.Node.Platform;
using Xunit;

namespace AgentBoard.Node.Tests;

/// <summary>
/// M0.1: platform abstraction contracts (v4.3 §2.9) and the Windows
/// implementation. Contract-level assertions run on every host; the
/// implementation assertions are Windows-guarded until M0.2 lands the macOS
/// pair (the same guard style as <see cref="Sprint7_CliLocatorTests"/>).
/// </summary>
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
    public void Windows_identity_never_throws_when_the_token_is_unavailable()
    {
        // Startup must not crash on a machine where the token cannot be opened.
        // ReadSid / ReadElevation swallow the failure and yield empty values.
        var identity = new WindowsUserIdentity();

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
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return;

        var identity = new WindowsUserIdentity();
        var info = new WindowsPlatformInfo(identity);

        // Duplicating the account fields on IPlatformInfo is required by
        // v4.3 §2.9, but they must stay a single source of truth — otherwise
        // /health and the service installer can disagree about the account.
        Assert.Equal(identity.CurrentUserName, info.CurrentUserName);
        Assert.Equal(identity.IsElevated, info.IsElevated);
    }

    [Fact]
    public void Factory_resolves_a_consistent_identity_and_platform_pair()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return;

        var identity = PlatformFactory.CreateUserIdentity();
        var info = PlatformFactory.CreatePlatformInfo(identity);

        Assert.Equal(Environment.UserName, info.CurrentUserName);
        Assert.Equal(identity.PrincipalId, new WindowsUserIdentity().PrincipalId);
    }

    [Fact]
    public void Factory_rejects_a_null_identity()
    {
        Assert.Throws<ArgumentNullException>(() => PlatformFactory.CreatePlatformInfo(null!));
    }
}
