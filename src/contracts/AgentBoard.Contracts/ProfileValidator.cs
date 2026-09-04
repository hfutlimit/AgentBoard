// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// Validation for the profile and provider contracts (doc 150 PR-003, PR-004;
/// doc 151 §5.1, §5.2).
/// </summary>
public static class ProfileValidator
{
    /// <summary>Platforms in the Day 1 runtime contract (doc 151 §9.1).</summary>
    public static IReadOnlySet<string> SupportedPlatforms { get; } =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "windows", "macos" };

    public static IReadOnlyList<EnvelopeError> Validate(AgentProfile profile)
    {
        var errors = new List<EnvelopeError>();

        Require(errors, nameof(profile.AgentId), profile.AgentId);
        Require(errors, nameof(profile.Role), profile.Role);
        Require(errors, nameof(profile.ProviderRef), profile.ProviderRef);
        Require(errors, nameof(profile.TransportRef), profile.TransportRef);
        Require(errors, nameof(profile.PolicyRevisionId), profile.PolicyRevisionId);
        Require(errors, nameof(profile.LocalPromptPolicyRef), profile.LocalPromptPolicyRef);

        if (profile.Capabilities.Count == 0)
        {
            errors.Add(new EnvelopeError(
                nameof(profile.Capabilities),
                "must declare at least one capability; an agent that can do nothing cannot be assigned"));
        }
        else if (profile.Capabilities.Any(string.IsNullOrWhiteSpace))
        {
            errors.Add(new EnvelopeError(
                nameof(profile.Capabilities), "must not contain blank entries"));
        }
        else if (!profile.AssignableStageTypes().Any())
        {
            // Capabilities that name no stage leave the agent unassignable
            // while looking configured, which is worse than being empty.
            errors.Add(new EnvelopeError(
                nameof(profile.Capabilities),
                "must include at least one capability naming a stage type"));
        }

        return errors;
    }

    public static IReadOnlyList<EnvelopeError> Validate(ProviderDefinition provider)
    {
        var errors = new List<EnvelopeError>();

        Require(errors, nameof(provider.ProviderId), provider.ProviderId);
        Require(errors, nameof(provider.Version), provider.Version);

        if (provider.SupportedTransports.Count == 0)
        {
            errors.Add(new EnvelopeError(
                nameof(provider.SupportedTransports),
                "must declare at least one transport"));
        }

        if (provider.Capabilities.Count == 0)
        {
            errors.Add(new EnvelopeError(
                nameof(provider.Capabilities),
                "must declare at least one capability"));
        }

        foreach (var platform in provider.SupportedPlatforms)
        {
            if (!SupportedPlatforms.Contains(platform))
            {
                errors.Add(new EnvelopeError(
                    nameof(provider.SupportedPlatforms),
                    $"'{platform}' is outside the Day 1 platform contract"));
            }
        }

        if (provider.SupportedPlatforms.Count == 0)
        {
            errors.Add(new EnvelopeError(
                nameof(provider.SupportedPlatforms), "must declare at least one platform"));
        }

        var missing = ProviderAdapterCapabilities.MissingMandatory(provider.AdapterCapabilities);
        foreach (var capability in missing)
        {
            errors.Add(new EnvelopeError(
                nameof(provider.AdapterCapabilities),
                $"is missing mandatory capability '{capability}'"));
        }

        return errors;
    }

    public static IReadOnlyList<EnvelopeError> Validate(ProviderLocalSettings settings)
    {
        var errors = new List<EnvelopeError>();

        Require(errors, nameof(settings.ProviderId), settings.ProviderId);
        Require(errors, nameof(settings.TransportId), settings.TransportId);
        Require(errors, nameof(settings.ExecutableIdentity), settings.ExecutableIdentity);
        Require(errors, nameof(settings.OfficialLoginProbe), settings.OfficialLoginProbe);

        // A bare value here would be a credential, not a locator: settings get
        // logged, exported and backed up, so the distinction matters (doc 151 §3
        // lists credentials as "never" allowed to cross, doc 150 PR-004 puts
        // them in the OS-backed store only).
        if (!string.IsNullOrWhiteSpace(settings.SecretStoreReference)
            && !settings.SecretStoreReference.Contains("://", StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(settings.SecretStoreReference),
                "must be a locator such as 'keychain://provider/work', not a credential value"));
        }

        return errors;
    }

    /// <summary>
    /// Checks that a profile's references actually resolve against the provider
    /// catalog entry it names.
    /// </summary>
    public static IReadOnlyList<EnvelopeError> ValidateProfileAgainstProvider(
        AgentProfile profile,
        ProviderDefinition provider)
    {
        var errors = new List<EnvelopeError>();

        if (!string.Equals(profile.ProviderRef, provider.ProviderId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(profile.ProviderRef),
                $"does not match provider '{provider.ProviderId}'"));
        }

        if (!provider.SupportedTransports.Contains(profile.TransportRef, StringComparer.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(profile.TransportRef),
                $"'{profile.TransportRef}' is not a supported transport of provider '{provider.ProviderId}'"));
        }

        var declared = provider.Capabilities.ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (var stage in profile.AssignableStageTypes())
        {
            if (!declared.Contains(stage.ToString()))
            {
                errors.Add(new EnvelopeError(
                    nameof(profile.Capabilities),
                    $"stage '{stage}' is not declared by provider '{provider.ProviderId}'"));
            }
        }

        return errors;
    }

    public static bool IsValid(AgentProfile profile) => Validate(profile).Count == 0;

    public static bool IsValid(ProviderDefinition provider) => Validate(provider).Count == 0;

    public static bool IsValid(ProviderLocalSettings settings) => Validate(settings).Count == 0;

    private static void Require(List<EnvelopeError> errors, string field, string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            errors.Add(new EnvelopeError(field, "is required"));
        }
    }
}
