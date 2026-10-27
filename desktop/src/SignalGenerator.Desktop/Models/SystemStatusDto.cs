using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace SignalGenerator.Desktop.Models;

public class SystemStatusDto
{
    [JsonPropertyName("app_name")]
    public string AppName { get; set; } = string.Empty;

    [JsonPropertyName("version")]
    public string Version { get; set; } = string.Empty;

    [JsonPropertyName("mode")]
    public string Mode { get; set; } = "PAPER";

    [JsonPropertyName("base_mode")]
    public string BaseMode { get; set; } = "PAPER";

    [JsonPropertyName("session_state")]
    public string SessionState { get; set; } = "INACTIVE";

    [JsonPropertyName("session_id")]
    public string? SessionId { get; set; }

    [JsonPropertyName("live_trading_locked")]
    public bool LiveTradingLocked { get; set; } = true;

    [JsonPropertyName("account")]
    public AccountSnapshotDto? Account { get; set; }

    [JsonPropertyName("capital")]
    public CapitalDto? Capital { get; set; }

    [JsonPropertyName("risk_limits")]
    public RiskLimitsDto? RiskLimits { get; set; }

    [JsonPropertyName("scanner")]
    public ScannerStatusDto? Scanner { get; set; }
}

public class AccountSnapshotDto
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = "UNAVAILABLE";

    [JsonPropertyName("is_connected")]
    public bool IsConnected { get; set; }

    [JsonPropertyName("is_stale")]
    public bool IsStale { get; set; }

    [JsonPropertyName("total_equity_usdt")]
    public decimal? TotalEquityUsdt { get; set; }

    [JsonPropertyName("wallet_balance_usdt")]
    public decimal? WalletBalanceUsdt { get; set; }

    [JsonPropertyName("available_balance_usdt")]
    public decimal? AvailableBalanceUsdt { get; set; }

    [JsonPropertyName("used_margin_usdt")]
    public decimal? UsedMarginUsdt { get; set; }

    [JsonPropertyName("unrealized_pnl_usdt")]
    public decimal? UnrealizedPnlUsdt { get; set; }

    [JsonPropertyName("open_positions_count")]
    public int OpenPositionsCount { get; set; }

    [JsonPropertyName("last_sync_utc")]
    public string? LastSyncUtc { get; set; }

    [JsonPropertyName("source")]
    public string? Source { get; set; }
}

public class CapitalDto
{
    [JsonPropertyName("initial_equity_usdt")]
    public decimal? InitialEquityUsdt { get; set; }

    [JsonPropertyName("current_equity_usdt")]
    public decimal? CurrentEquityUsdt { get; set; }

    [JsonPropertyName("verified_live_equity_usdt")]
    public decimal? VerifiedLiveEquityUsdt { get; set; }

    [JsonPropertyName("margin_mode")]
    public string? MarginMode { get; set; }

    [JsonPropertyName("max_leverage")]
    public decimal? MaxLeverage { get; set; }
}

public class RiskLimitsDto
{
    [JsonPropertyName("configured_trade_risk_usdt")]
    public decimal? ConfiguredTradeRiskUsdt { get; set; }

    [JsonPropertyName("effective_trade_risk_usdt")]
    public decimal? EffectiveTradeRiskUsdt { get; set; }

    [JsonPropertyName("daily_loss_limit_usdt")]
    public decimal? DailyLossLimitUsdt { get; set; }

    [JsonPropertyName("daily_loss_used_usdt")]
    public decimal? DailyLossUsedUsdt { get; set; }

    [JsonPropertyName("daily_loss_status")]
    public string? DailyLossStatus { get; set; }

    [JsonPropertyName("daily_loss_reason")]
    public string? DailyLossReason { get; set; }

    [JsonPropertyName("weekly_loss_limit_usdt")]
    public decimal? WeeklyLossLimitUsdt { get; set; }

    [JsonPropertyName("weekly_loss_used_usdt")]
    public decimal? WeeklyLossUsedUsdt { get; set; }

    [JsonPropertyName("open_positions")]
    public int OpenPositions { get; set; }

    [JsonPropertyName("max_open_positions")]
    public int MaxOpenPositions { get; set; }

    [JsonPropertyName("daily_entries_used")]
    public int? DailyEntriesUsed { get; set; }

    [JsonPropertyName("max_daily_entries")]
    public int? MaxDailyEntries { get; set; }

    [JsonPropertyName("can_open_position")]
    public bool CanOpenPosition { get; set; }

    [JsonPropertyName("block_reason")]
    public string? BlockReason { get; set; }

    [JsonPropertyName("reconciliation_status")]
    public string? ReconciliationStatus { get; set; }

    [JsonPropertyName("reconciliation_timestamp_utc")]
    public string? ReconciliationTimestampUtc { get; set; }

    [JsonPropertyName("lock_state")]
    public string? LockState { get; set; }

    [JsonPropertyName("lock_symbol")]
    public string? LockSymbol { get; set; }

    [JsonPropertyName("target_isolated_margin_usdt")]
    public decimal? TargetIsolatedMarginUsdt { get; set; }

    [JsonPropertyName("hard_risk_limit_usdt")]
    public decimal? HardRiskLimitUsdt { get; set; }
}

public class ScannerStatusDto
{
    [JsonPropertyName("state")]
    public string State { get; set; } = "UNKNOWN";

    [JsonPropertyName("process_status")]
    public string ProcessStatus { get; set; } = "UNKNOWN";

    [JsonPropertyName("configured_live_strategy_id")]
    public string? ConfiguredLiveStrategyId { get; set; }

    [JsonPropertyName("configured_live_strategy_status")]
    public string? ConfiguredLiveStrategyStatus { get; set; }

    [JsonPropertyName("has_completed_scan")]
    public bool HasCompletedScan { get; set; }

    [JsonPropertyName("scan_count")]
    public int ScanCount { get; set; }

    [JsonPropertyName("last_scan_status")]
    public string LastScanStatus { get; set; } = "NEVER_RUN";

    [JsonPropertyName("last_scan_utc")]
    public string? LastScanUtc { get; set; }

    [JsonPropertyName("last_scan_age_seconds")]
    public int? LastScanAgeSeconds { get; set; }

    [JsonPropertyName("background_worker_active")]
    public bool BackgroundWorkerActive { get; set; }

    [JsonPropertyName("is_stale")]
    public bool IsStale { get; set; }

    [JsonPropertyName("symbols_attempted")]
    public List<string> SymbolsAttempted { get; set; } = new();

    [JsonPropertyName("last_error")]
    public string? LastError { get; set; }
}
