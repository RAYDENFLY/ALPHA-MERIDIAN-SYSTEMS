using System.Text.Json.Serialization;

namespace SignalGenerator.Desktop.Models;

public class JournalSummaryDto
{
    [JsonPropertyName("mode")]
    public string Mode { get; set; } = "LIVE";

    [JsonPropertyName("total_closed_trades")]
    public int TotalClosedTrades { get; set; }

    [JsonPropertyName("wins_count")]
    public int WinsCount { get; set; }

    [JsonPropertyName("losses_count")]
    public int LossesCount { get; set; }

    [JsonPropertyName("breakeven_count")]
    public int BreakevenCount { get; set; }

    [JsonPropertyName("win_rate_pct")]
    public decimal WinRatePct { get; set; }

    [JsonPropertyName("profit_factor")]
    public decimal ProfitFactor { get; set; }

    [JsonPropertyName("average_realized_r")]
    public decimal AverageRealizedR { get; set; }

    [JsonPropertyName("total_gross_pnl_usd")]
    public decimal TotalGrossPnlUsd { get; set; }

    [JsonPropertyName("total_net_pnl_usd")]
    public decimal TotalNetPnlUsd { get; set; }

    [JsonPropertyName("total_fees_paid_usd")]
    public decimal TotalFeesPaidUsd { get; set; }

    [JsonPropertyName("total_funding_usd")]
    public decimal TotalFundingUsd { get; set; }

    [JsonPropertyName("open_count")]
    public int OpenCount { get; set; }

    [JsonPropertyName("open_positions_count")]
    public int OpenPositionsCount { get; set; }

    [JsonPropertyName("daily_loss_usage_usd")]
    public decimal DailyLossUsageUsd { get; set; }

    [JsonPropertyName("daily_loss_limit_usd")]
    public decimal DailyLossLimitUsd { get; set; }

    [JsonPropertyName("daily_filled_entries")]
    public int DailyFilledEntries { get; set; }

    [JsonPropertyName("daily_entries_allowance")]
    public int DailyEntriesAllowance { get; set; }

    [JsonPropertyName("remaining_entries")]
    public int RemainingEntries { get; set; }

    [JsonPropertyName("miss_count")]
    public int MissCount { get; set; }

    [JsonPropertyName("expired_count")]
    public int ExpiredCount { get; set; }

    [JsonPropertyName("reconciliation_ok")]
    public bool ReconciliationOk { get; set; }
}
