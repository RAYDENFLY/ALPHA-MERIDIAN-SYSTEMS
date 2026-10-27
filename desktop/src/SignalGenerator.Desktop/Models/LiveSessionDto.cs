using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace SignalGenerator.Desktop.Models;

public class LiveSessionEnvelopeDto
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = "UNKNOWN";

    [JsonPropertyName("data")]
    public LiveSessionDataDto? Data { get; set; }

    [JsonPropertyName("preflight")]
    public PreflightAuditDto? Preflight { get; set; }

    [JsonPropertyName("timestamp_utc")]
    public string? TimestampUtc { get; set; }
}

public class LiveSessionDataDto
{
    [JsonPropertyName("session_id")]
    public string? SessionId { get; set; }

    [JsonPropertyName("mode")]
    public string Mode { get; set; } = "PAPER";

    [JsonPropertyName("state")]
    public string State { get; set; } = "INACTIVE";

    [JsonPropertyName("is_active")]
    public bool IsActive { get; set; }

    [JsonPropertyName("is_paused")]
    public bool IsPaused { get; set; }

    [JsonPropertyName("started_at_utc")]
    public string? StartedAtUtc { get; set; }

    [JsonPropertyName("paused_at_utc")]
    public string? PausedAtUtc { get; set; }

    [JsonPropertyName("stopped_at_utc")]
    public string? StoppedAtUtc { get; set; }

    [JsonPropertyName("authorized_by")]
    public string? AuthorizedBy { get; set; }

    [JsonPropertyName("orders_placed_count")]
    public int OrdersPlacedCount { get; set; }

    [JsonPropertyName("orders_filled_count")]
    public int OrdersFilledCount { get; set; }

    [JsonPropertyName("last_candidate_symbol")]
    public string? LastCandidateSymbol { get; set; }

    [JsonPropertyName("last_status_message")]
    public string? LastStatusMessage { get; set; }

    [JsonPropertyName("daily_filled_entries")]
    public int DailyFilledEntries { get; set; }

    [JsonPropertyName("entry_limit")]
    public int EntryLimit { get; set; } = 3;

    [JsonPropertyName("global_lock_state")]
    public string? GlobalLockState { get; set; }

    [JsonPropertyName("global_lock_symbol")]
    public string? GlobalLockSymbol { get; set; }
}

public class PreflightAuditDto
{
    [JsonPropertyName("verdict")]
    public string Verdict { get; set; } = "UNKNOWN";

    [JsonPropertyName("timestamp_utc")]
    public string? TimestampUtc { get; set; }

    [JsonPropertyName("checks")]
    public List<PreflightCheckItemDto> Checks { get; set; } = new();
}

public class PreflightCheckItemDto
{
    [JsonPropertyName("check_name")]
    public string CheckName { get; set; } = string.Empty;

    [JsonPropertyName("passed")]
    public bool Passed { get; set; }

    [JsonPropertyName("message")]
    public string? Message { get; set; }

    [JsonPropertyName("severity")]
    public string Severity { get; set; } = "INFO";
}
