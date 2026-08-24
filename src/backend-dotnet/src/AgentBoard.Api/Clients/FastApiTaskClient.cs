// SPDX-License-Identifier: MIT
using System.Net;

namespace AgentBoard.Api.Clients;

public sealed record FastApiProxyResponse(
	HttpStatusCode StatusCode,
	string ContentType,
	string Body);

public sealed class FastApiTaskClient
{
	private readonly IHttpClientFactory _httpClientFactory;

	public FastApiTaskClient(IHttpClientFactory httpClientFactory)
	{
		_httpClientFactory = httpClientFactory ?? throw new ArgumentNullException(nameof(httpClientFactory));
	}

	public async Task<FastApiProxyResponse> ProxyGenerateSubtasksAsync(
		int taskId,
		CancellationToken ct)
	{
		try
		{
			var client = _httpClientFactory.CreateClient("AgentBoardFastApi");
			using var response = await client.PostAsync(
				$"api/tasks/{taskId}/generate-subtasks",
				content: null,
				ct);
			var body = await response.Content.ReadAsStringAsync(ct);
			var contentType = response.Content.Headers.ContentType?.ToString()
				?? "application/json";

			if ((int)response.StatusCode >= 500)
			{
				return new FastApiProxyResponse(
					HttpStatusCode.BadGateway,
					"application/json",
					"{\"detail\":\"FastAPI task generation service failed\"}");
			}

			return new FastApiProxyResponse(response.StatusCode, contentType, body);
		}
		catch (HttpRequestException)
		{
			return new FastApiProxyResponse(
				HttpStatusCode.BadGateway,
				"application/json",
				"{\"detail\":\"FastAPI task generation service is unavailable\"}");
		}
	}
}
