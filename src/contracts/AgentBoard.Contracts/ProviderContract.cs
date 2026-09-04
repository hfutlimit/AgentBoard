// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// How a provider expects to be authenticated (catalog metadata).
/// </summary>
/// <remarks>
/// doc 150 PR-004 puts login, credential storage, token refresh and login-state
/// detection entirely with the provider's own mechanism or its adapter. The
/// catalog only records which of these shapes applies, so the Server can render
/// the right remediation hint without ever handling a credential.
/// </remarks>
public enum ProviderAuthModel
{
    /// <summary>No login is needed.</summary>
    None,

    /// <summary>The provider owns the login flow; the adapter may trigger it.</summary>
    OfficialLogin,

    /// <summary>A key held in the Node's OS-backed secret store.</summary>
    ApiKeyInSecretStore,
}

/// <summary>
/// The auth readiness categories the Server is allowed to see (doc 150 PR-004:
/// "Server 只能看到 readiness、auth state、failure category 和修复提示").
/// </summary>
public enum ProviderAuthState
{
    Unknown,
    Ready,
    NotAuthenticated,
    AuthenticationRequired,
    Expired,
}

/// <summary>
/// The minimal capability set every provider adapter must expose
/// (doc 151 §5.2).
/// </summary>
public enum ProviderAdapterCapability
{
    CheckAuth,

    /// <summary>Optional: only meaningful for providers with an official login flow.</summary>
    BeginOfficialLogin,

    CheckReady,
    StartAttempt,
    StreamLocalEvents,
    CancelAttempt,
    CollectAttemptResult,
}

/// <summary>
/// Which adapter capabilities are mandatory and what a declaration is missing
/// (doc 151 §5.2).
/// </summary>
public static class ProviderAdapterCapabilities
{
    /// <summary>
    /// Everything except <see cref="ProviderAdapterCapability.BeginOfficialLogin"/>,
    /// which is optional because not every provider has an interactive login to
    /// begin (doc 151 §5.2 marks it "(optional)").
    /// </summary>
    public static IReadOnlySet<ProviderAdapterCapability> Mandatory { get; } =
        new HashSet<ProviderAdapterCapability>
        {
            ProviderAdapterCapability.CheckAuth,
            ProviderAdapterCapability.CheckReady,
            ProviderAdapterCapability.StartAttempt,
            ProviderAdapterCapability.StreamLocalEvents,
            ProviderAdapterCapability.CancelAttempt,
            ProviderAdapterCapability.CollectAttemptResult,
        };

    public static IReadOnlySet<ProviderAdapterCapability> MissingMandatory(
        IEnumerable<ProviderAdapterCapability> declared)
    {
        var present = new HashSet<ProviderAdapterCapability>(declared);
        return Mandatory.Where(capability => !present.Contains(capability)).ToHashSet();
    }
}

/// <summary>
/// The Server-visible provider catalog entry (doc 151 §5.2, doc 150 PR-003).
/// </summary>
/// <remarks>
/// This is what the Server may know about a provider. It deliberately has no
/// field for an executable path, a launch argument, a credential or a secret:
/// those live in <see cref="ProviderLocalSettings"/> on the Node.
/// </remarks>
public sealed record ProviderDefinition
{
    public string ProviderId { get; init; } = string.Empty;
    public string Version { get; init; } = string.Empty;

    /// <summary>Transport ids this provider can be driven through.</summary>
    public IReadOnlyList<string> SupportedTransports { get; init; } = Array.Empty<string>();

    /// <summary>Capability names this provider declares.</summary>
    public IReadOnlyList<string> Capabilities { get; init; } = Array.Empty<string>();

    /// <summary>Capabilities the adapter actually implements.</summary>
    public IReadOnlyList<ProviderAdapterCapability> AdapterCapabilities { get; init; } =
        Array.Empty<ProviderAdapterCapability>();

    /// <summary>Platforms this provider runs on, limited to windows and macos.</summary>
    public IReadOnlyList<string> SupportedPlatforms { get; init; } = Array.Empty<string>();

    /// <summary>Failure categories this provider can produce.</summary>
    public IReadOnlyList<FailureCategory> FailureCategories { get; init; } =
        Array.Empty<FailureCategory>();

    public ProviderAuthModel AuthModel { get; init; } = ProviderAuthModel.OfficialLogin;

    /// <summary>Opaque public runtime metadata that carries no secret.</summary>
    public string RuntimeMetadata { get; init; } = string.Empty;
}

/// <summary>
/// The Node-local settings for a provider (doc 151 §5.2, §3).
/// </summary>
/// <remarks>
/// doc 151 §3 assigns this entirely to the Node and permits nothing from it to
/// cross the boundary. Note that <see cref="SecretStoreReference"/> is a
/// locator such as <c>keychain://codex/work</c> — a reference to where the
/// credential lives, never the credential. Storing the value here would put a
/// secret into a settings record that gets logged, exported and backed up.
/// </remarks>
public sealed record ProviderLocalSettings
{
    public string ProviderId { get; init; } = string.Empty;
    public string TransportId { get; init; } = string.Empty;

    /// <summary>Resolved executable path or application identity on this machine.</summary>
    public string ExecutableIdentity { get; init; } = string.Empty;

    /// <summary>Opaque JSON describing the launch-argument policy.</summary>
    public string ArgumentsPolicy { get; init; } = string.Empty;

    /// <summary>How to probe whether the provider's own login is still valid.</summary>
    public string OfficialLoginProbe { get; init; } = string.Empty;

    /// <summary>Locator for the credential, e.g. <c>keychain://codex/work</c>.</summary>
    public string? SecretStoreReference { get; init; }

    /// <summary>Opaque JSON with platform-specific launch settings.</summary>
    public string LaunchSettings { get; init; } = string.Empty;

    /// <summary>Opaque JSON with local redaction and retention rules.</summary>
    public string RedactionAndRetention { get; init; } = string.Empty;

    public bool Enabled { get; init; } = true;
}
