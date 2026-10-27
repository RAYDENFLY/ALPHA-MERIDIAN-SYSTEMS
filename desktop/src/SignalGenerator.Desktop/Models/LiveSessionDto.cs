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

    [JsonPropertyName("recovery_reason")]
    public string? RecoveryReason { get; set; }

    [JsonPropertyName("scanner_active")]
    public bool ScannerActive { get; set; }

    [JsonPropertyName("last_scan_utc")]
    public string? LastScanUtc { get; set; }

    [JsonPropertyName("is_authorized_and_active")]
    public bool IsAuthorizedAndActive { get; set; }

    [JsonPropertyName("configured_live_strategy_id")]
    public string? ConfiguredLiveStrategyId { get; set; }

    [JsonPropertyName("configured_live_strategy_status")]
    public string? ConfiguredLiveStrategyStatus { get; set; }

    [JsonPropertyName("cycle_telemetry")]
    public CycleTelemetryDto? CycleTelemetry { get; set; }

    [JsonPropertyName("daily_filled_entries")]
    public int DailyFilledEntries { get; set; }

    [JsonPropertyName("entry_limit")]
    public int EntryLimit { get; set; } = 3;

    [JsonPropertyName("global_lock_state")]
    public string? GlobalLockState { get; set; }

    [JsonPropertyName("global_lock_symbol")]
    public string? GlobalLockSymbol { get; set; }

    [JsonPropertyName("latest_screening")]
    public ScreeningCycleDto? LatestScreening { get; set; }

    [JsonPropertyName("protection_reconciliation_worker")]
    public ProtectionWorkerDto? ProtectionReconciliationWorker { get; set; }
}

public class CycleTelemetryDto
{
    [JsonPropertyName("cycle_id")]
    public string? CycleId { get; set; }

    [JsonPropertyName("start_utc")]
    public string? StartUtc { get; set; }

    [JsonPropertyName("end_utc")]
    public string? EndUtc { get; set; }

    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("current_stage")]
    public string? CurrentStage { get; set; }

    [JsonPropertyName("duration_ms")]
    public double? DurationMs { get; set; }

    [JsonPropertyName("next_cycle_due_utc")]
    public string? NextCycleDueUtc { get; set; }

    [JsonPropertyName("last_completed_cycle_id")]
    public string? LastCompletedCycleId { get; set; }

    [JsonPropertyName("last_completed_cycle_utc")]
    public string? LastCompletedCycleUtc { get; set; }

    [JsonPropertyName("last_cycle_status")]
    public string? LastCycleStatus { get; set; }

    [JsonPropertyName("last_cycle_duration_ms")]
    public double? LastCycleDurationMs { get; set; }
}

public class ScannerControlResponseDto
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("message")]
    public string Message { get; set; } = string.Empty;

    [JsonPropertyName("scanner_state")]
    public string ScannerState { get; set; } = string.Empty;
}

public class GroqAutoSetupResponseDto
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("timestamp_utc")]
    public string? TimestampUtc { get; set; }
}


public class ProtectionWorkerDto
{
    [JsonPropertyName("worker_enabled")]
    public bool WorkerEnabled { get; set; }

    [JsonPropertyName("worker_alive")]
    public bool WorkerAlive { get; set; }

    [JsonPropertyName("last_cycle_status")]
    public string? LastCycleStatus { get; set; }

    [JsonPropertyName("last_cycle_result")]
    public ProtectionWorkerCycleResultDto? LastCycleResult { get; set; }
}

public class ProtectionWorkerCycleResultDto
{
    [JsonPropertyName("timestamp_utc")]
    public string? TimestampUtc { get; set; }

    [JsonPropertyName("status")]
    public string Status { get; set; } = "OK";

    [JsonPropertyName("new_fills_imported")]
    public int NewFillsImported { get; set; }

    [JsonPropertyName("reconciled_trades")]
    public int ReconciledTrades { get; set; }
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
