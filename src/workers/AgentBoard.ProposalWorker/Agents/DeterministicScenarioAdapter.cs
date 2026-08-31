using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Agents;

/// <summary>
/// Deterministic, in-process adapter used by the Golden Happy Path gate.
/// It consumes the same RabbitMQ workflow message as a real CLI adapter and
/// advances work only through AgentBoard's production HTTP contract.  The
/// adapter is inert unless an AgentInstance is explicitly registered with
/// <c>executor_type=scenario</c>; production CLI adapters are unchanged.
/// </summary>
public sealed class DeterministicScenarioAdapter : IAgentAdapter
{
    private readonly IHttpClientFactory _http;
    private readonly AgentBoardOptions _agentBoard;
    private readonly AgentOptions _scenario;
    private readonly ILogger<DeterministicScenarioAdapter> _log;

    public DeterministicScenarioAdapter(
        IHttpClientFactory http,
        IOptions<AgentBoardOptions> agentBoard,
        IOptions<AgentsOptions> agents,
        ILogger<DeterministicScenarioAdapter> log)
    {
        _http = http;
        _agentBoard = agentBoard.Value;
        _scenario = agents.Value.Scenario;
        _log = log;
    }

    public string AgentType => "scenario";

    public async Task<AgentExecutionResult> ExecuteAsync(
        ExecutionContext context, CancellationToken ct)
    {
        var started = DateTimeOffset.UtcNow;
        if (!string.Equals(
                _scenario.Command, "enabled", StringComparison.OrdinalIgnoreCase))
        {
            return Failure(
                "DeterministicScenarioAdapter is disabled; set " +
                "Agents:Scenario:Command=enabled only in an isolated test environment",
                started);
        }
        if (string.IsNullOrWhiteSpace(_agentBoard.ServerUrl)
            || string.IsNullOrWhiteSpace(_agentBoard.StartupToken))
        {
            return Failure(
                "DeterministicScenarioAdapter requires AgentBoard:ServerUrl " +
                "and AgentBoard:StartupToken",
                started);
        }

        using var request = BuildRequest(context);
        var client = _http.CreateClient();
        request.RequestUri = new Uri(
            _agentBoard.ServerUrl.TrimEnd('/') + request.RequestUri!.OriginalString,
            UriKind.Absolute);
        request.Headers.Authorization = new AuthenticationHeaderValue(
            "Bearer", _agentBoard.StartupToken);

        try
        {
            if (_scenario.DelayMilliseconds > 0)
            {
                await Task.Delay(_scenario.DelayMilliseconds, ct);
            }
            var response = await client.SendAsync(request, ct);
            var body = await response.Content.ReadAsStringAsync(ct);
            var audit = JsonSerializer.Serialize(new
            {
                action = "scenario_http",
                workload_type = context.WorkloadType,
                workload_id = context.WorkloadId,
                method = request.Method.Method,
                path = request.RequestUri.AbsolutePath,
                status = (int)response.StatusCode,
                response = body,
            });
            if (!response.IsSuccessStatusCode)
            {
                return new AgentExecutionResult(
                    Success: false,
                    OutputJson: audit,
                    ErrorMessage: $"scenario HTTP {(int)response.StatusCode}: {body}",
                    ExitCode: (int)response.StatusCode,
                    Duration: DateTimeOffset.UtcNow - started);
            }

            _log.LogInformation(
                "scenario adapter completed {WorkloadType} {WorkloadId} via {Method} {Path}",
                context.WorkloadType, context.WorkloadId,
                request.Method, request.RequestUri.AbsolutePath);
            return new AgentExecutionResult(
                Success: true,
                OutputJson: audit,
                ErrorMessage: null,
                ExitCode: 0,
                Duration: DateTimeOffset.UtcNow - started);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _log.LogError(ex,
                "scenario adapter failed {WorkloadType} {WorkloadId}",
                context.WorkloadType, context.WorkloadId);
            return Failure(ex.Message, started);
        }
    }

    private static HttpRequestMessage BuildRequest(ExecutionContext context)
    {
        return context.WorkloadType switch
        {
            WorkloadTypes.Task or WorkloadTypes.Rework => new HttpRequestMessage(
                HttpMethod.Post,
                $"/api/tasks/{context.WorkloadId}/submit-review"),
            WorkloadTypes.Review => new HttpRequestMessage(
                HttpMethod.Post,
                $"/api/tasks/{context.WorkloadId}/review")
            {
                Content = JsonContent.Create(new
                {
                    verdict = "approve",
                    comment = "deterministic golden scenario approve",
                }),
            },
            WorkloadTypes.Ticket => new HttpRequestMessage(
                HttpMethod.Post,
                $"/api/ticket-requests/{TicketRequestId(context)}/execute"),
            _ => throw new InvalidOperationException(
                $"scenario adapter does not support workload_type '{context.WorkloadType}'"),
        };
    }

    private static long TicketRequestId(ExecutionContext context)
    {
        using var payload = JsonDocument.Parse(context.PayloadJson);
        if (payload.RootElement.TryGetProperty("ref_id", out var refId)
            && refId.ValueKind == JsonValueKind.Number
            && refId.TryGetInt64(out var requestId)
            && requestId > 0)
        {
            return requestId;
        }
        throw new InvalidOperationException(
            "scenario ticket workload requires a positive ref_id request id");
    }

    private static AgentExecutionResult Failure(
        string error, DateTimeOffset started) => new(
            Success: false,
            OutputJson: JsonSerializer.Serialize(new
            {
                action = "scenario_http",
                error,
            }),
            ErrorMessage: error,
            ExitCode: 1,
            Duration: DateTimeOffset.UtcNow - started);
}
