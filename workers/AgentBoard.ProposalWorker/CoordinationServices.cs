using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker;

public sealed class WorkerHeartbeatService : BackgroundService
{
    private readonly IHttpClientFactory _http; private readonly WorkerState _state; private readonly WorkerOptions _worker; private readonly AgentBoardOptions _agentBoard; private readonly ILogger<WorkerHeartbeatService> _log;
    public WorkerHeartbeatService(IHttpClientFactory http, WorkerState state, IOptions<WorkerOptions> worker, IOptions<AgentBoardOptions> agentBoard, ILogger<WorkerHeartbeatService> log) => (_http, _state, _worker, _agentBoard, _log) = (http, state, worker.Value, agentBoard.Value, log);
    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(Math.Max(5, _worker.HeartbeatSeconds)));
        do { await SendOnce(ct); } while (await timer.WaitForNextTickAsync(ct));
    }
    private async Task SendOnce(CancellationToken ct)
    {
        _state.LastHeartbeatAt = DateTimeOffset.UtcNow;
        if (string.IsNullOrWhiteSpace(_agentBoard.HeartbeatUrl)) return;
        try { var response = await _http.CreateClient().PostAsJsonAsync(_agentBoard.HeartbeatUrl, new { workerId = _worker.Id, status = _state.Snapshot(), timestamp = _state.LastHeartbeatAt }, ct); if (!response.IsSuccessStatusCode) _log.LogWarning("Worker heartbeat returned {Status}", response.StatusCode); }
        catch (Exception ex) when (!ct.IsCancellationRequested) { _log.LogWarning(ex, "Worker heartbeat failed"); }
    }
}

public sealed class AgentBoardWebSocketService : BackgroundService
{
    private readonly AgentBoardOptions _options; private readonly WorkerState _state; private readonly ILogger<AgentBoardWebSocketService> _log;
    public AgentBoardWebSocketService(IOptions<AgentBoardOptions> options, WorkerState state, ILogger<AgentBoardWebSocketService> log) => (_options, _state, _log) = (options.Value, state, log);
    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(_options.WebSocketUrl)) return;
        var delay = TimeSpan.FromSeconds(1);
        while (!ct.IsCancellationRequested)
        {
            try
            {
                using var socket = new ClientWebSocket(); await socket.ConnectAsync(new Uri(_options.WebSocketUrl), ct); delay = TimeSpan.FromSeconds(1);
                var bytes = new byte[4096];
                while (socket.State == WebSocketState.Open && !ct.IsCancellationRequested)
                {
                    var result = await socket.ReceiveAsync(bytes, ct);
                    if (result.MessageType == WebSocketMessageType.Close) break;
                    _log.LogInformation("AgentBoard worker websocket event: {Event}", Encoding.UTF8.GetString(bytes, 0, result.Count));
                }
            }
            catch (Exception ex) when (!ct.IsCancellationRequested) { _state.LastError = ex.Message; _log.LogWarning(ex, "Worker websocket disconnected"); }
            await Task.Delay(delay, ct); delay = TimeSpan.FromSeconds(Math.Min(30, delay.TotalSeconds * 2));
        }
    }
}
