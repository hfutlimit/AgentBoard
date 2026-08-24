// SPDX-License-Identifier: MIT
using System.Net;
using AgentBoard.Api.Clients;
using FluentAssertions;

namespace AgentBoard.Api.Tests.Features.Tasks;

public sealed class FastApiTaskClientTests
{
	[Fact]
	public async Task Proxy_Preserves_FastApi_Status_Body_And_Path()
	{
		var handler = new RecordingHandler(
			new HttpResponseMessage(HttpStatusCode.NotFound)
			{
				Content = new StringContent("{\"detail\":\"task not found\"}")
			});
		var httpClient = new HttpClient(handler)
		{
			BaseAddress = new Uri("http://fastapi.test/")
		};
		var client = new FastApiTaskClient(new StaticHttpClientFactory(httpClient));

		var response = await client.ProxyGenerateSubtasksAsync(42, CancellationToken.None);

		response.StatusCode.Should().Be(HttpStatusCode.NotFound);
		response.Body.Should().Be("{\"detail\":\"task not found\"}");
		handler.Request.Method.Should().Be(HttpMethod.Post);
		handler.Request.RequestUri!.PathAndQuery.Should().Be("/api/tasks/42/generate-subtasks");
	}

	private sealed class StaticHttpClientFactory(HttpClient client) : IHttpClientFactory
	{
		public HttpClient CreateClient(string name) => client;
	}

	private sealed class RecordingHandler(HttpResponseMessage response) : HttpMessageHandler
	{
		public HttpRequestMessage Request { get; private set; } = null!;

		protected override Task<HttpResponseMessage> SendAsync(
			HttpRequestMessage request,
			CancellationToken cancellationToken)
		{
			Request = request;
			return Task.FromResult(response);
		}
	}
}
