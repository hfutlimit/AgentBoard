using AgentBoard.Contracts;
using Xunit;

namespace AgentBoard.Contracts.Tests;

/// <summary>
/// A0 contract tests for AgentProfile, ProviderDefinition and
/// ProviderLocalSettings. Each test names the doc 150 / doc 151 clause it
/// enforces.
/// </summary>
public sealed class A0ProfileContractTests
{
    // -------------------------------------------------------------------------
    // doc 151 §5.1 / doc 150 PR-003 AgentProfile
    // -------------------------------------------------------------------------

    [Fact]
    public void A_well_formed_profile_is_valid()
    {
        Assert.True(ProfileValidator.IsValid(ValidProfile()));
    }

    [Fact]
    public void Profile_requires_at_least_one_capability_naming_a_stage()
    {
        var noStage = ValidProfile() with { Capabilities = new[] { "generic" } };

        // An agent that names no stage is unassignable while looking
        // configured, which fails far later and more confusingly than empty.
        Assert.False(ProfileValidator.IsValid(noStage));
    }

    [Fact]
    public void Profile_exposes_the_stage_types_it_can_be_assigned_to()
    {
        var profile = ValidProfile() with
        {
            Capabilities = new[] { "development", "review", "python" },
        };

        Assert.Equal(
            new[] { StageType.Development, StageType.Review },
            profile.AssignableStageTypes());
    }

    [Fact]
    public void Profile_requires_a_policy_revision_reference()
    {
        var profile = ValidProfile() with { PolicyRevisionId = string.Empty };

        // doc 150 PR-005: the preset is UX; the compiled revision is what is
        // enforced, so a profile without one has no enforceable policy.
        Assert.False(ProfileValidator.IsValid(profile));
    }

    // -------------------------------------------------------------------------
    // doc 151 §5.1 / doc 150 PR-015 — what the Server payload must not contain
    // -------------------------------------------------------------------------

    [Theory]
    [InlineData("Prompt")]
    [InlineData("SystemPrompt")]
    [InlineData("BasePrompt")]
    [InlineData("Secret")]
    [InlineData("Credential")]
    [InlineData("Token")]
    [InlineData("ApiKey")]
    [InlineData("CliPath")]
    [InlineData("ExecutablePath")]
    [InlineData("Executable")]
    [InlineData("WorkingDirectory")]
    public void Agent_profile_has_no_member_that_could_carry_a_secret_or_a_local_path(string name)
    {
        // doc 151 §5.1: "provider-specific secret、完整 prompt 和本地 runtime
        // path 不属于 Server profile payload."
        Assert.Null(typeof(AgentProfile).GetProperty(name));
    }

    [Fact]
    public void Agent_profile_prompt_field_is_a_reference_not_the_text()
    {
        // A reference is allowed and required; the prompt itself is not.
        Assert.NotNull(typeof(AgentProfile).GetProperty("LocalPromptPolicyRef"));
        Assert.StartsWith("local://", ValidProfile().LocalPromptPolicyRef, StringComparison.Ordinal);
    }

    // -------------------------------------------------------------------------
    // doc 151 §5.2 ProviderDefinition
    // -------------------------------------------------------------------------

    [Fact]
    public void A_well_formed_provider_definition_is_valid()
    {
        Assert.True(ProfileValidator.IsValid(ValidProvider()));
    }

    [Fact]
    public void Provider_must_declare_every_mandatory_adapter_capability()
    {
        var provider = ValidProvider() with
        {
            AdapterCapabilities = new[]
            {
                ProviderAdapterCapability.CheckAuth,
                ProviderAdapterCapability.CheckReady,
            },
        };

        Assert.False(ProfileValidator.IsValid(provider));
        Assert.Equal(4, Enumerable.Count(ProfileValidator.Validate(provider),
            e => e.Field == nameof(ProviderDefinition.AdapterCapabilities)));
    }

    [Fact]
    public void BeginOfficialLogin_is_optional()
    {
        // doc 151 §5.2 marks it "(optional)": not every provider has an
        // interactive login to begin. Everything else is mandatory.
        Assert.DoesNotContain(
            ProviderAdapterCapability.BeginOfficialLogin,
            ProviderAdapterCapabilities.Mandatory);
        Assert.True(ProfileValidator.IsValid(ValidProvider()));
    }

    [Fact]
    public void Provider_platforms_are_limited_to_the_day_one_contract()
    {
        var provider = ValidProvider() with { SupportedPlatforms = new[] { "linux" } };

        Assert.False(ProfileValidator.IsValid(provider));
    }

    [Fact]
    public void Provider_definition_has_no_field_for_a_local_path_or_credential()
    {
        // Those belong to ProviderLocalSettings on the Node (doc 151 §3).
        foreach (var name in new[] { "ExecutablePath", "CliPath", "Credential", "Secret", "ApiKey" })
        {
            Assert.Null(typeof(ProviderDefinition).GetProperty(name));
        }
    }

    // -------------------------------------------------------------------------
    // doc 151 §5.2 / §3 ProviderLocalSettings
    // -------------------------------------------------------------------------

    [Fact]
    public void A_well_formed_local_settings_are_valid()
    {
        Assert.True(ProfileValidator.IsValid(ValidLocalSettings()));
    }

    [Fact]
    public void Secret_store_value_must_be_a_locator_not_a_credential()
    {
        var settings = ValidLocalSettings() with { SecretStoreReference = "sk-abc123" };

        // Settings records get logged, exported and backed up, so a bare value
        // here is a credential leak waiting for a log shipper.
        Assert.False(ProfileValidator.IsValid(settings));
        Assert.Contains(
            ProfileValidator.Validate(settings),
            e => e.Field == nameof(ProviderLocalSettings.SecretStoreReference));
    }

    [Fact]
    public void A_keychain_locator_is_accepted()
    {
        var settings = ValidLocalSettings() with
        {
            SecretStoreReference = "keychain://codex/work",
        };

        Assert.True(ProfileValidator.IsValid(settings));
    }

    [Fact]
    public void Local_settings_have_no_member_that_stores_a_secret_value()
    {
        foreach (var name in new[] { "ApiKey", "Token", "Password", "RefreshToken", "Credential", "Secret" })
        {
            Assert.Null(typeof(ProviderLocalSettings).GetProperty(name));
        }
    }

    [Fact]
    public void Local_settings_require_an_executable_identity()
    {
        var settings = ValidLocalSettings() with { ExecutableIdentity = string.Empty };

        Assert.False(ProfileValidator.IsValid(settings));
    }

    // -------------------------------------------------------------------------
    // Cross-references
    // -------------------------------------------------------------------------

    [Fact]
    public void A_profile_consistent_with_its_provider_is_valid()
    {
        Assert.Empty(ProfileValidator.ValidateProfileAgainstProvider(ValidProfile(), ValidProvider()));
    }

    [Fact]
    public void A_profile_naming_an_unsupported_transport_is_rejected()
    {
        var profile = ValidProfile() with { TransportRef = "transport.http" };

        var errors = ProfileValidator.ValidateProfileAgainstProvider(profile, ValidProvider());

        Assert.Contains(errors, e => e.Field == nameof(AgentProfile.TransportRef));
    }

    [Fact]
    public void A_profile_claiming_a_stage_the_provider_does_not_declare_is_rejected()
    {
        var profile = ValidProfile() with { Capabilities = new[] { "qa" } };

        var errors = ProfileValidator.ValidateProfileAgainstProvider(profile, ValidProvider());

        Assert.Contains(errors, e => e.Field == nameof(AgentProfile.Capabilities));
    }

    [Fact]
    public void A_profile_pointing_at_a_different_provider_is_rejected()
    {
        var profile = ValidProfile() with { ProviderRef = "provider.other" };

        var errors = ProfileValidator.ValidateProfileAgainstProvider(profile, ValidProvider());

        Assert.Contains(errors, e => e.Field == nameof(AgentProfile.ProviderRef));
    }

    // -------------------------------------------------------------------------
    // doc 151 §5.3 policy revision immutability
    // -------------------------------------------------------------------------

    [Fact]
    public void Policy_revision_is_immutable_after_construction()
    {
        var revision = new PolicyRevision("policy-rev-17", "sha256:abc", AutonomyPreset.Developer);

        // PropertyInfo.CanWrite is also true for an init-only accessor, so it
        // cannot distinguish "settable" from "settable during initialisation".
        // The IsExternalInit modreq on the setter's return type can.
        foreach (var property in typeof(PolicyRevision).GetProperties())
        {
            var setter = property.SetMethod;
            Assert.NotNull(setter);

            var isInitOnly = setter.ReturnParameter
                .GetRequiredCustomModifiers()
                .Any(modifier => modifier.FullName == "System.Runtime.CompilerServices.IsExternalInit");

            Assert.True(
                isInitOnly,
                $"{property.Name} must be init-only; a writable set accessor would let a " +
                "published revision be edited in place, making 'which policy governed this " +
                "decision' unanswerable after the fact");
        }

        Assert.Equal("policy-rev-17", revision.Id);
    }

    [Fact]
    public void Copying_a_policy_revision_does_not_mutate_the_original()
    {
        var original = new PolicyRevision("policy-rev-17", "sha256:abc", AutonomyPreset.Developer);

        var copy = original with { ContentHash = "sha256:def" };

        Assert.Equal("sha256:abc", original.ContentHash);
        Assert.Equal("sha256:def", copy.ContentHash);
        Assert.NotSame(original, copy);
    }

    [Fact]
    public void Autonomy_presets_are_exactly_the_three_operator_options()
    {
        Assert.Equal(
            new[] { "Review", "Developer", "Full" },
            Enum.GetNames<AutonomyPreset>());
    }

    // -------------------------------------------------------------------------
    // Builders
    // -------------------------------------------------------------------------

    private static AgentProfile ValidProfile() => new()
    {
        AgentId = "agent.dev.codex",
        Role = "developer",
        Capabilities = new[] { "development" },
        ProviderRef = "provider.codex",
        TransportRef = "transport.codex-cli",
        RuntimeOptions = "{\"model\":\"gpt-5\"}",
        PolicyRevisionId = "policy-rev-17",
        LocalPromptPolicyRef = "local://prompt-policy/dev-v1",
        Preset = AutonomyPreset.Developer,
    };

    private static ProviderDefinition ValidProvider() => new()
    {
        ProviderId = "provider.codex",
        Version = "1.0",
        SupportedTransports = new[] { "transport.codex-cli", "transport.codex-native" },
        Capabilities = new[] { "development", "review" },
        AdapterCapabilities = new[]
        {
            ProviderAdapterCapability.CheckAuth,
            ProviderAdapterCapability.BeginOfficialLogin,
            ProviderAdapterCapability.CheckReady,
            ProviderAdapterCapability.StartAttempt,
            ProviderAdapterCapability.StreamLocalEvents,
            ProviderAdapterCapability.CancelAttempt,
            ProviderAdapterCapability.CollectAttemptResult,
        },
        SupportedPlatforms = new[] { "windows", "macos" },
        FailureCategories = new[] { FailureCategory.AuthExpired, FailureCategory.ProviderFailure },
        AuthModel = ProviderAuthModel.OfficialLogin,
        RuntimeMetadata = "{}",
    };

    private static ProviderLocalSettings ValidLocalSettings() => new()
    {
        ProviderId = "provider.codex",
        TransportId = "transport.codex-cli",
        ExecutableIdentity = "codex",
        ArgumentsPolicy = "{}",
        OfficialLoginProbe = "codex login status",
        SecretStoreReference = "keychain://codex/work",
        LaunchSettings = "{}",
        RedactionAndRetention = "{}",
        Enabled = true,
    };
}
