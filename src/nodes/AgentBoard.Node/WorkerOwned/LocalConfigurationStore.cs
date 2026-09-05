using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace AgentBoard.Node.WorkerOwned;

/// <summary>One complete local snapshot; never merge indexed arrays across configuration providers.</summary>
public sealed class LocalConfigurationStore(string path, WorkerOwnedOptions defaults)
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private readonly object _gate = new();
    public string FilePath { get; } = Path.GetFullPath(path);
    public WorkerOwnedOptions Load()
    {
        lock (_gate)
        {
            if (!File.Exists(FilePath)) return Clone(defaults);
            var options = JsonSerializer.Deserialize<WorkerOwnedOptions>(File.ReadAllText(FilePath), Json)
                ?? throw new InvalidDataException("Invalid local Worker configuration");
            options.ValidateConfiguration();
            return options;
        }
    }

    public ConfigurationSnapshot Read()
    {
        lock (_gate)
        {
            var options = Load();
            // The browser and local settings file do not carry service credentials.
            foreach (var agent in options.Agents) agent.Runtime.AgentBoardToken = "";
            return new(options, Revision());
        }
    }

    public ConfigurationSnapshot Save(ConfigurationSnapshot request)
    {
        lock (_gate)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
            using var editLock = AcquireEditLock();
            if (request.Revision != Revision()) throw new ConfigurationConflictException();
            if (Load().Agents.Any(a => !string.IsNullOrWhiteSpace(a.Runtime.AgentBoardToken)))
                throw new InvalidOperationException("Existing per-Agent API credentials require explicit migration before this portal can save configuration");
            request.Configuration.ValidateConfiguration();
            if (request.Configuration.Agents.Any(a => !string.IsNullOrEmpty(a.Runtime.AgentBoardToken)))
                throw new InvalidOperationException("API credentials must stay in environment variables, not local configuration");
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
            var temporary = FilePath + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllText(temporary, JsonSerializer.Serialize(request.Configuration, Json), new UTF8Encoding(false));
                if (File.Exists(FilePath)) File.Replace(temporary, FilePath, FilePath + ".bak");
                else File.Move(temporary, FilePath);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
            return Read();
        }
    }

    private string Revision() => Convert.ToHexString(SHA256.HashData(File.Exists(FilePath)
        ? File.ReadAllBytes(FilePath) : Encoding.UTF8.GetBytes(JsonSerializer.Serialize(defaults, Json))));

    private FileStream AcquireEditLock()
    {
        try { return new(FilePath + ".edit.lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException) { throw new ConfigurationConflictException(); }
    }

    public static WorkerOwnedOptions Clone(WorkerOwnedOptions value) =>
        JsonSerializer.Deserialize<WorkerOwnedOptions>(JsonSerializer.Serialize(value, Json), Json)!;
}

public sealed record ConfigurationSnapshot(WorkerOwnedOptions Configuration, string Revision);
public sealed class ConfigurationConflictException : Exception;
