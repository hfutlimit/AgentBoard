using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using AgentBoard.Node.WorkerOwned;
using Xunit;

namespace AgentBoard.Node.Tests;

public class DesignDocumentPublisherTests
{
    private static JsonElement Context => JsonSerializer.SerializeToElement(new
        { item = new { id = 1707, story_id = 429 }, story = new { id = 429, epic_id = 66 } });
    private static JsonObject Result() => JsonNode.Parse("""
        {"decision":"submit","summary":"done","commit":"abc123","agent_id":"designer","provider":"codex","model":"test",
         "design_document":{"title":"排序设计","content":"# 设计\n完整契约和验收用例。"},"design_document_id":999}
        """)!.AsObject();

    [Theory]
    [InlineData("{}")]
    [InlineData("{\"design_document\":{\"title\":\"x\",\"content\":{}}}")]
    [InlineData("{\"design_document\":{\"title\":\"x\",\"content\":\" \"}}")]
    public void Design_requires_complete_markdown_document(string json) =>
        Assert.Throws<InvalidDataException>(() => WorkPlanner.ValidateResult("design", JsonNode.Parse(json)!.AsObject()));

    [Fact]
    public void Prompt_requires_document_but_discussion_remains_readonly()
    {
        Assert.Contains("design_document", WorkPlanner.Prompt("design", "{}"));
        Assert.DoesNotContain("design_document", WorkPlanner.Prompt("design", "{\"discussion\":{\"turn\":2}}"));
        WorkPlanner.ValidateResult("design", Result());
    }

    [Fact]
    public async Task Publishes_reads_back_links_and_reuses_frozen_document()
    {
        using var handler = new Api();
        using var client = Client(handler);
        var result = Result();
        await DesignDocumentPublisher.Publish(client, 33, 3, Context, result, default);
        await DesignDocumentPublisher.Publish(client, 33, 3, Context, result, default);
        Assert.Equal(1, handler.Creates);
        Assert.Equal(2, handler.Reads);
        Assert.Single(handler.Comments);
        Assert.Equal(162, result["design_document_id"]!.GetValue<long>());
        Assert.Equal("http://board/project/3/documents/162", result["design_document_url"]!.GetValue<string>());
        Assert.Equal(66, handler.Document!["epic_id"]!.GetValue<int>());
        Assert.Equal(429, handler.Document["story_id"]!.GetValue<int>());
        Assert.Contains("完整契约", handler.Document["content"]!.GetValue<string>());
        Assert.Contains("project/3/tasks/1707", handler.Document["content"]!.GetValue<string>());
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public async Task Lost_create_or_comment_response_recovers_without_duplicate(bool document)
    {
        using var handler = new Api { LoseCreateResponse = document, LoseCommentResponse = !document };
        using var client = Client(handler);
        var result = Result();
        await Assert.ThrowsAsync<HttpRequestException>(() => DesignDocumentPublisher.Publish(client, 33, 3, Context, result, default));
        await DesignDocumentPublisher.Publish(client, 33, 3, Context, result, default);
        Assert.Equal(1, handler.Creates);
        Assert.Single(handler.Comments);
    }

    [Fact]
    public async Task Human_edit_is_preserved_and_submission_is_deferred()
    {
        using var handler = new Api();
        using var client = Client(handler);
        await DesignDocumentPublisher.Publish(client, 33, 3, Context, Result(), default);
        handler.Document!["content"] = handler.Document["content"]!.GetValue<string>() + "\nHuman edit";
        await Assert.ThrowsAsync<HttpRequestException>(() => DesignDocumentPublisher.Publish(client, 33, 3, Context, Result(), default));
        Assert.Equal(1, handler.Creates);
        Assert.EndsWith("Human edit", handler.Document["content"]!.GetValue<string>());
    }

    [Fact]
    public async Task Publication_failure_does_not_add_completion_metadata_or_comment()
    {
        using var handler = new Api { RejectCreate = true };
        using var client = Client(handler);
        var result = Result();
        await Assert.ThrowsAsync<HttpRequestException>(() => DesignDocumentPublisher.Publish(client, 33, 3, Context, result, default));
        Assert.Null(result["design_document_url"]);
        Assert.Empty(handler.Comments);
    }

    private static HttpClient Client(Api handler) => new(handler) { BaseAddress = new Uri("http://board/") };
    private sealed class Api : HttpMessageHandler
    {
        public JsonObject? Document;
        public JsonArray Comments = [];
        public int Creates, Reads;
        public bool LoseCreateResponse, LoseCommentResponse, RejectCreate;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            var path = request.RequestUri!.AbsolutePath;
            if (path == "/api/documents" && request.Method == HttpMethod.Get)
                return Ok(Document is null ? new JsonArray() : new JsonArray(Document.DeepClone()));
            if (path == "/api/documents" && request.Method == HttpMethod.Post)
            {
                if (RejectCreate) return new(HttpStatusCode.ServiceUnavailable);
                Creates++;
                Document = (await request.Content!.ReadFromJsonAsync<JsonObject>(ct))!;
                Document["id"] = 162;
                if (LoseCreateResponse) { LoseCreateResponse = false; throw new HttpRequestException("response lost"); }
                return Ok(Document);
            }
            if (path == "/api/documents/162") { Reads++; return Ok(Document!); }
            if (path == "/api/tasks/1707/comments" && request.Method == HttpMethod.Get) return Ok(Comments);
            if (path == "/api/tasks/1707/comments" && request.Method == HttpMethod.Post)
            {
                var comment = (await request.Content!.ReadFromJsonAsync<JsonObject>(ct))!;
                Comments.Add(comment);
                if (LoseCommentResponse) { LoseCommentResponse = false; throw new HttpRequestException("response lost"); }
                return Ok(comment);
            }
            throw new InvalidOperationException(request.ToString());
        }
        private static HttpResponseMessage Ok(JsonNode data) => new(HttpStatusCode.OK) { Content = JsonContent.Create(data) };
    }
}
