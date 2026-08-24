// SPDX-License-Identifier: MIT
using Microsoft.Extensions.Configuration;

namespace AgentBoard.Api.Security;

public static class RuntimeSecurityConfiguration
{
	private const string DevelopmentJwtSecret = "dev-insecure-secret-change-me";

	public static string ResolveJwtSecret(
		IConfiguration configuration,
		bool isDevelopment,
		bool isTesting)
	{
		ArgumentNullException.ThrowIfNull(configuration);

		var secret = configuration["AgentBoard:Jwt:Secret"]?.Trim();
		var missing = string.IsNullOrWhiteSpace(secret)
			|| secret.StartsWith("REPLACE_WITH", StringComparison.Ordinal);
		if (!missing && secret!.Length >= 32)
			return secret;

		if (isDevelopment || isTesting)
			return DevelopmentJwtSecret;

		throw new InvalidOperationException(
			"AgentBoard:Jwt:Secret must be a non-placeholder secret of at least 32 characters outside Development and Testing.");
	}

	public static IReadOnlyList<string> ResolveCorsOrigins(
		IConfiguration configuration,
		bool isDevelopment,
		bool isTesting)
	{
		ArgumentNullException.ThrowIfNull(configuration);

		var origins = configuration.GetSection("AgentBoard:CorsOrigins")
			.Get<string[]>()
			?.Where(origin => !string.IsNullOrWhiteSpace(origin))
			.Select(origin => origin.Trim())
			.Distinct(StringComparer.OrdinalIgnoreCase)
			.ToArray()
			?? Array.Empty<string>();

		if (origins.Length == 0)
			throw new InvalidOperationException("AgentBoard:CorsOrigins must contain at least one origin.");

		if (origins.Contains("*", StringComparer.Ordinal)
			&& !isDevelopment
			&& !isTesting)
		{
			throw new InvalidOperationException(
				"Wildcard CORS is only allowed in Development or Testing; configure explicit AgentBoard:CorsOrigins in production.");
		}

		return origins;
	}
}
