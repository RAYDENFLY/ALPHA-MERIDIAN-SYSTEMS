using System.Text.Json.Serialization;

namespace SignalGenerator.Desktop.Models;

/// <summary>
/// Persisted configuration for the desktop terminal.
/// </summary>
public class DesktopSettings
{
    [JsonPropertyName("base_url")]
    public string BaseUrl { get; set; } = "http://127.0.0.1:8000";

    [JsonPropertyName("polling_interval_seconds")]
    public int PollingIntervalSeconds { get; set; } = 3;

    [JsonPropertyName("enable_auto_polling")]
    public bool EnableAutoPolling { get; set; } = true;

    [JsonPropertyName("theme")]
    public string Theme { get; set; } = "Dark";

    [JsonPropertyName("http_timeout_seconds")]
    public int HttpTimeoutSeconds { get; set; } = 8;
}
