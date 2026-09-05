// SPDX-License-Identifier: MIT
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AgentBoard.Node.WorkerOwned;

/// <summary>Publishes a frozen Design result before submitting it for review.
/// The API has no transactional idempotency key: recover ordinary response-loss retries
/// by an exact work/content marker, and never overwrite a document edited by a human.</summary>
public static class DesignDocumentPublisher
{
    public static void Validate(JsonObject result)
    {
        if (result["design_document"] is not JsonObject document
            || Text(document, "title") is not { Length: > 0 and <= 250 }
            || Text(document, "content") is not { Length: > 0 })
            throw new InvalidDataException("Design requires design_document={title:<nonempty string, max 250 characters>,content:<complete Markdown STRING>}. Include scope, design decisions, contracts and acceptance criteria, not just a summary or file paths.");
    }

    public static async Task Publish(HttpClient client, long workId, int projectId, JsonElement context,
        JsonObject result, CancellationToken ct)
    {
        Validate(result);
        var design = result["design_document"]!.AsObject();
        var title = Text(design, "title")!;
        var markdown = Text(design, "content")!;
        var taskId = context.GetProperty("item").GetProperty("id").GetInt64();
        var storyId = context.GetProperty("item").GetProperty("story_id").GetInt64();
        int? epicId = context.TryGetProperty("story", out var story)
            && story.TryGetProperty("epic_id", out var epic) && epic.ValueKind == JsonValueKind.Number ? epic.GetInt32() : null;
        var commit = Text(result, "commit") ?? "";
        var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(title + "\n" + commit + "\n" + markdown)));
        var marker = $"<!-- agentboard-design-work:{workId}:{digest} -->";
        var taskUrl = new Uri(client.BaseAddress!, $"project/{projectId}/tasks/{taskId}").AbsoluteUri;
        var content = $"{marker}\n\n关联：[Task #{taskId}]({taskUrl}) · Story #{storyId}\n\n"
            + $"设计 Agent：{Text(result, "agent_id")}；模型：{Text(result, "provider")} / {Text(result, "model")}。\n\n"
            + $"来源提交：`{commit}`。设计交付，待独立评审。\n\n{markdown}";

        // Ignore model-supplied document IDs. Only reuse a server document matching
        // this work, project, Story and exact frozen content.
        using var search = await client.GetAsync($"api/documents?project_id={projectId}&story_id={storyId}&type=design&q={Uri.EscapeDataString(marker)}&limit=200", ct);
        search.EnsureSuccessStatusCode();
        var documents = await search.Content.ReadFromJsonAsync<JsonArray>(ct) ?? throw new HttpRequestException("Invalid document search response");
        var matches = documents.OfType<JsonObject>().Where(d => Text(d, "content")?.Contains(marker, StringComparison.Ordinal) == true).ToArray();
        if (matches.Length > 1) throw new HttpRequestException("Multiple Design documents match this work; resolve duplicates before submission");
        JsonObject document;
        if (matches.Length == 1) document = matches[0];
        else
        {
            using var created = await client.PostAsJsonAsync("api/documents", new
            { project_id = projectId, story_id = storyId, epic_id = epicId, title, content, type = "design", status = "in_review" }, ct);
            created.EnsureSuccessStatusCode();
            document = await created.Content.ReadFromJsonAsync<JsonObject>(ct) ?? throw new HttpRequestException("Invalid document creation response");
        }
        var id = document["id"]!.GetValue<long>();
        using var readback = await client.GetAsync($"api/documents/{id}", ct);
        readback.EnsureSuccessStatusCode();
        document = await readback.Content.ReadFromJsonAsync<JsonObject>(ct) ?? throw new HttpRequestException("Invalid document readback response");
        if (document["project_id"]?.GetValue<int>() != projectId || document["story_id"]?.GetValue<long>() != storyId
            || Text(document, "type") != "design" || Text(document, "title") != title || Text(document, "content") != content)
            throw new HttpRequestException("Design document differs from the frozen result; preserved without overwriting, submission deferred");
        var url = new Uri(client.BaseAddress!, $"project/{projectId}/documents/{id}").AbsoluteUri;
        result["design_document_id"] = id;
        result["design_document_url"] = url;

        var comment = $"{marker}\n\n设计文档已发布：[{title}]({url})。\n\n关联 Story #{storyId}；来源提交 `{commit}`。"
            + $"设计 Agent：{Text(result, "agent_id")}；模型：{Text(result, "provider")} / {Text(result, "model")}。待独立评审。";
        using var commentsResponse = await client.GetAsync($"api/tasks/{taskId}/comments", ct);
        commentsResponse.EnsureSuccessStatusCode();
        var comments = await commentsResponse.Content.ReadFromJsonAsync<JsonArray>(ct) ?? throw new HttpRequestException("Invalid task comment response");
        if (!comments.OfType<JsonObject>().Any(c => Text(c, "content") == comment))
        {
            using var posted = await client.PostAsJsonAsync($"api/tasks/{taskId}/comments", new
            { author = Text(result, "agent_id"), content = comment }, ct);
            posted.EnsureSuccessStatusCode();
            using var verified = await client.GetAsync($"api/tasks/{taskId}/comments", ct);
            verified.EnsureSuccessStatusCode();
            var savedComments = await verified.Content.ReadFromJsonAsync<JsonArray>(ct);
            if (savedComments is null || !savedComments.OfType<JsonObject>().Any(c => Text(c, "content") == comment))
                throw new HttpRequestException("Design document link could not be verified in task comments");
        }
    }

    private static string? Text(JsonObject obj, string key) => obj[key] is JsonValue value
        && value.TryGetValue<string>(out var text) && !string.IsNullOrWhiteSpace(text) ? text : null;
}
