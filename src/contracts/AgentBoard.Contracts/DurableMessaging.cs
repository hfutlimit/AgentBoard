// SPDX-License-Identifier: MIT
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AgentBoard.Contracts;

/// <summary>Stable RabbitMQ topology for the versioned execution protocol.</summary>
public static class DurableMessaging
{
    public const string CommandExchange = "agentboard.execution.commands.v1";
    public const string ResultExchange = "agentboard.execution.results.v1";
    public const string DeadLetterExchange = "agentboard.execution.dlx.v1";
    public const string ServerResultQueue = "agentboard.execution.results.server.v1";
    public const string DeadLetterQueue = "agentboard.execution.dead.v1";

    public static string WorkerCommandQueue(string workerId) =>
        $"agentboard.execution.commands.worker.{workerId}.v1";

    public static string WorkerRoutingKey(string workerId) => $"worker.{workerId}";

    public const string ResultRoutingKey = "server.result";
    public const string DeadLetterRoutingKey = "dead";
}

/// <summary>One serializer shared by broker producers and consumers.</summary>
public static class ContractJson
{
    public static JsonSerializerOptions Options { get; } = new(JsonSerializerDefaults.Web)
    {
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower) },
    };
}
