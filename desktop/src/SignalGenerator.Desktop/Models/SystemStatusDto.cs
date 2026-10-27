using System;
using System.Collections.Generic;
using System.Globalization;
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

    [JsonPropertyName("last_sync_age_seconds")]
    public int? LastSyncAgeSeconds { get; set; }

    [JsonPropertyName("source")]
    public string? Source { get; set; }

    [JsonPropertyName("positions_summary")]
    public List<OpenPositionDto> PositionsSummary { get; set; } = new();
}

public class OpenPositionDto
{
    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("side")]
    public string Side { get; set; } = "LONG";

    [JsonPropertyName("position_amt")]
    public decimal PositionAmt { get; set; }

    [JsonPropertyName("raw_position_amt")]
    public decimal RawPositionAmt { get; set; }

    [JsonPropertyName("entry_price")]
    public decimal EntryPrice { get; set; }

    [JsonPropertyName("mark_price")]
    public decimal MarkPrice { get; set; }

    [JsonPropertyName("notional_usdt")]
    public decimal NotionalUsdt { get; set; }

    [JsonPropertyName("leverage")]
    public int Leverage { get; set; } = 1;

    [JsonPropertyName("margin_mode")]
    public string MarginMode { get; set; } = "CROSS";

    [JsonPropertyName("take_profit_price")]
    public decimal? TakeProfitPrice { get; set; }

    [JsonPropertyName("take_profit_label")]
    public string? TakeProfitLabel { get; set; }

    [JsonPropertyName("stop_loss_price")]
    public decimal? StopLossPrice { get; set; }

    [JsonPropertyName("stop_loss_label")]
    public string? StopLossLabel { get; set; }

    [JsonPropertyName("liquidation_price")]
    public decimal? LiquidationPrice { get; set; }

    [JsonPropertyName("liquidation_label")]
    public string? LiquidationLabel { get; set; }

    [JsonPropertyName("unrealized_pnl_usdt")]
    public decimal UnrealizedPnlUsdt { get; set; }

    [JsonPropertyName("roe_pct")]
    public decimal RoePct { get; set; }

    [JsonPropertyName("roi_pct")]
    public decimal RoiPct { get; set; }

    [JsonPropertyName("source")]
    public string? Source { get; set; }

    [JsonPropertyName("last_sync_utc")]
    public string? LastSyncUtc { get; set; }

    [JsonPropertyName("last_sync_age_seconds")]
    public int? LastSyncAgeSeconds { get; set; }

    [JsonPropertyName("tp_orders")]
    public List<PositionOrderBracketDto> TpOrders { get; set; } = new();

    [JsonPropertyName("sl_orders")]
    public List<PositionOrderBracketDto> SlOrders { get; set; } = new();

    // UI Formatting Helpers
    public string DirectionColor => Side.Equals("LONG", StringComparison.OrdinalIgnoreCase) ? "#00E676" : "#FF5252";
    public string PnlColor => UnrealizedPnlUsdt > 0 ? "#00E676" : (UnrealizedPnlUsdt < 0 ? "#FF5252" : "#8B949E");
    public string FormattedPnl => (UnrealizedPnlUsdt >= 0 ? "+$" : "-$") + Math.Abs(UnrealizedPnlUsdt).ToString("F2", CultureInfo.InvariantCulture) + $" ({RoePct:F2}%)";
    public string FormattedEntry => EntryPrice.ToString("F4", CultureInfo.InvariantCulture);
    public string FormattedMark => MarkPrice.ToString("F4", CultureInfo.InvariantCulture);
    public string FormattedSize => PositionAmt.ToString("F4", CultureInfo.InvariantCulture);
    public string FormattedLiq => LiquidationPrice.HasValue ? $"${LiquidationPrice.Value.ToString("F4", CultureInfo.InvariantCulture)}" : (LiquidationLabel ?? "Unavailable");
    public string FormattedSl => StopLossPrice.HasValue ? $"${StopLossPrice.Value.ToString("N4", CultureInfo.InvariantCulture)}" : (StopLossLabel ?? "None");
    public string FormattedTp => TakeProfitPrice.HasValue ? $"${TakeProfitPrice.Value.ToString("N4", CultureInfo.InvariantCulture)}" : (TakeProfitLabel ?? "None");
}

public class PositionOrderBracketDto
{
    [JsonPropertyName("order_id")]
    public object? OrderId { get; set; }

    [JsonPropertyName("price")]
    public decimal Price { get; set; }

    [JsonPropertyName("orig_qty")]
    public decimal OrigQty { get; set; }

    [JsonPropertyName("type")]
    public string Type { get; set; } = "LIMIT";

    [JsonPropertyName("reduce_only")]
    public bool ReduceOnly { get; set; }

    [JsonPropertyName("close_position")]
    public bool ClosePosition { get; set; }
}

public class OpenOrderDto
{
    public string Symbol { get; set; } = string.Empty;
    public string Side { get; set; } = "BUY";
    public string OrderType { get; set; } = "LIMIT";
    public decimal Price { get; set; }
    public decimal OrigQty { get; set; }
    public decimal RemainingQty { get; set; }
    public string Status { get; set; } = "OPEN";
    public bool ReduceOnly { get; set; }
    public string AssociatedPosition { get; set; } = "--";
    public string TimestampUtc { get; set; } = "--";

    public string SideColor => Side.Equals("BUY", StringComparison.OrdinalIgnoreCase) || Side.Equals("LONG", StringComparison.OrdinalIgnoreCase) ? "#00E676" : "#FF5252";
    public string FormattedPrice => Price.ToString("F4", CultureInfo.InvariantCulture);
    public string FormattedQty => OrigQty.ToString("F4", CultureInfo.InvariantCulture);
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
