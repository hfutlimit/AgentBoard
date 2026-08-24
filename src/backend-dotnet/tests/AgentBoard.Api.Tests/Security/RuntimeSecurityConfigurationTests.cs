// SPDX-License-Identifier: MIT
using AgentBoard.Api.Security;
using FluentAssertions;
using Microsoft.Extensions.Configuration;

namespace AgentBoard.Api.Tests.Security;

public sealed class RuntimeSecurityConfigurationTests
{
	[Fact]
	public void Production_Rejects_Missing_Jwt_Secret()
	{
		var configuration = Configuration(new Dictionary<string, string?>
		{
			["AgentBoard:Jwt:Secret"] = null,
		});

		var action = () => RuntimeSecurityConfiguration.ResolveJwtSecret(
			configuration,
			isDevelopment: false,
			isTesting: false);

		action.Should().Throw<InvalidOperationException>()
			.WithMessage("*JWT*secret*");
	}

	[Fact]
	public void Production_Rejects_Wildcard_Cors()
	{
		var configuration = Configuration(new Dictionary<string, string?>
		{
			["AgentBoard:CorsOrigins:0"] = "*",
		});

		var action = () => RuntimeSecurityConfiguration.ResolveCorsOrigins(
			configuration,
			isDevelopment: false,
			isTesting: false);

		action.Should().Throw<InvalidOperationException>()
			.WithMessage("*CORS*");
	}

	[Fact]
	public void Development_Allows_Local_Jwt_Fallback_And_Wildcard_Cors()
	{
		var configuration = Configuration(new Dictionary<string, string?>
		{
			["AgentBoard:CorsOrigins:0"] = "*",
		});

		RuntimeSecurityConfiguration.ResolveJwtSecret(configuration, true, false)
			.Should().Be("dev-insecure-secret-change-me");
		RuntimeSecurityConfiguration.ResolveCorsOrigins(configuration, true, false)
			.Should().ContainSingle().Which.Should().Be("*");
	}

	private static IConfiguration Configuration(IReadOnlyDictionary<string, string?> values) =>
		new ConfigurationBuilder()
			.AddInMemoryCollection(values)
			.Build();
}
