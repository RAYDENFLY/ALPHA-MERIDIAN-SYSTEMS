using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.Json.Serialization;

namespace SignalGenerator.Desktop.Models;

#region 1. AI Signal & Execution Monitor (GET /api/autotrader/screening-log)

public class AutotraderScreeningLogResponseDto
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("active_cycle")]
    public ScreeningCycleDto? ActiveCycle { get; set; }

    [JsonPropertyName("latest")]
    public ScreeningCycleDto? Latest { get; set; }

    [JsonPropertyName("history")]
    public List<ScreeningCycleDto> History { get; set; } = new();

    [JsonPropertyName("timestamp_utc")]
    public string? TimestampUtc { get; set; }
}

public class ScreeningCycleDto
{
    [JsonPropertyName("cycle_id")]
    public string CycleId { get; set; } = string.Empty;

    [JsonPropertyName("timestamp_utc")]
    public string? TimestampUtc { get; set; }

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("execution_venue")]
    public string? ExecutionVenue { get; set; }

    [JsonPropertyName("strategy_id")]
    public string? StrategyId { get; set; }

    [JsonPropertyName("duration_ms")]
    public double? DurationMs { get; set; }

    [JsonPropertyName("market_regime")]
    public MarketRegimeDto? MarketRegime { get; set; }

    [JsonPropertyName("stage1")]
    public Stage1ScreeningDto? Stage1 { get; set; }

    [JsonPropertyName("stage2")]
    public Stage2ScreeningDto? Stage2 { get; set; }

    [JsonPropertyName("stage3")]
    public Stage3ScreeningDto? Stage3 { get; set; }

    // Legacy fallback bindings
    [JsonPropertyName("stage1_passed_count")]
    public int Stage1PassedCount { get; set; }

    [JsonPropertyName("stage2_passed_count")]
    public int Stage2PassedCount { get; set; }

    [JsonPropertyName("stage3_selected_symbol")]
    public string? Stage3SelectedSymbol { get; set; }

    [JsonPropertyName("decision_reason")]
    public string? DecisionReason { get; set; }

    [JsonPropertyName("rejection_code")]
    public string? RejectionCode { get; set; }

    [JsonPropertyName("model_id")]
    public string? ModelId { get; set; }

    public string DisplayTimestamp => DateTimeOffset.TryParse(TimestampUtc, out var dt) 
        ? dt.UtcDateTime.ToString("HH:mm:ss") + " UTC" 
        : "--:--:--";
}

public class MarketRegimeDto
{
    [JsonPropertyName("regime_label")]
    public string? RegimeLabel { get; set; }

    [JsonPropertyName("btc_price")]
    public decimal? BtcPrice { get; set; }

    [JsonPropertyName("btc_change_24h_pct")]
    public decimal? BtcChange24hPct { get; set; }

    [JsonPropertyName("eth_price")]
    public decimal? EthPrice { get; set; }

    [JsonPropertyName("eth_change_24h_pct")]
    public decimal? EthChange24hPct { get; set; }
}

public class Stage1ScreeningDto
{
    [JsonPropertyName("total_screened_pairs")]
    public int TotalScreenedPairs { get; set; }

    [JsonPropertyName("execution_time_ms")]
    public double ExecutionTimeMs { get; set; }

    [JsonPropertyName("top_10")]
    public List<Top10CandidateDto> Top10 { get; set; } = new();
}

public class Top10CandidateDto
{
    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("mark_price")]
    public decimal MarkPrice { get; set; }

    [JsonPropertyName("change_24h_pct")]
    public decimal Change24hPct { get; set; }

    [JsonPropertyName("rel_strength_vs_btc")]
    public decimal RelStrengthVsBtc { get; set; }

    [JsonPropertyName("turnover_24h_m")]
    public decimal Turnover24hM { get; set; }

    [JsonPropertyName("spread_bps")]
    public decimal SpreadBps { get; set; }

    [JsonPropertyName("funding_rate_pct")]
    public decimal FundingRatePct { get; set; }

    [JsonPropertyName("range_72h_pct")]
    public decimal Range72hPct { get; set; }

    [JsonPropertyName("trend_15m")]
    public string? Trend15m { get; set; }

    [JsonPropertyName("trend_1h")]
    public string? Trend1h { get; set; }

    [JsonPropertyName("rsi_15m")]
    public decimal Rsi15m { get; set; }

    [JsonPropertyName("rsi_1h")]
    public decimal Rsi1h { get; set; }

    [JsonPropertyName("reason_selected")]
    public string? ReasonSelected { get; set; }

    public string FormattedPrice => MarkPrice.ToString("F4", CultureInfo.InvariantCulture);
    public string FormattedChange => (Change24hPct >= 0 ? "+" : "") + Change24hPct.ToString("F2", CultureInfo.InvariantCulture) + "%";
    public string FormattedTurnover => "$" + Turnover24hM.ToString("F1", CultureInfo.InvariantCulture) + "M";
    public string FormattedSpread => SpreadBps.ToString("F2", CultureInfo.InvariantCulture) + " bps";
}

public class Stage2ScreeningDto
{
    [JsonPropertyName("model_used")]
    public string? ModelUsed { get; set; }

    [JsonPropertyName("top_3")]
    public List<Top3CandidateDto> Top3 { get; set; } = new();

    [JsonPropertyName("eliminated_summary")]
    public Dictionary<string, string> EliminatedSummary { get; set; } = new();
}

public class Top3CandidateDto
{
    [JsonPropertyName("rank")]
    public int Rank { get; set; }

    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("category")]
    public string Category { get; set; } = string.Empty;

    [JsonPropertyName("score")]
    public decimal Score { get; set; }

    [JsonPropertyName("thesis")]
    public string Thesis { get; set; } = string.Empty;

    [JsonPropertyName("verification_questions")]
    public List<string> VerificationQuestions { get; set; } = new();

    public string ScoreText => Score.ToString("F1", CultureInfo.InvariantCulture) + "/100";
}

public class Stage3ScreeningDto
{
    [JsonPropertyName("decision")]
    public string Decision { get; set; } = string.Empty;

    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("direction")]
    public string Direction { get; set; } = string.Empty;

    [JsonPropertyName("order_type")]
    public string? OrderType { get; set; }

    [JsonPropertyName("entry_price")]
    public decimal EntryPrice { get; set; }

    [JsonPropertyName("stop_loss_price")]
    public decimal StopLossPrice { get; set; }

    [JsonPropertyName("take_profit_price")]
    public decimal TakeProfitPrice { get; set; }

    [JsonPropertyName("qty")]
    public decimal Qty { get; set; }

    [JsonPropertyName("leverage")]
    public int Leverage { get; set; }

    [JsonPropertyName("margin_mode")]
    public string? MarginMode { get; set; }

    [JsonPropertyName("max_risk_usdt")]
    public decimal MaxRiskUsdt { get; set; }

    [JsonPropertyName("stop_distance_pct")]
    public decimal StopDistancePct { get; set; }

    [JsonPropertyName("rr_ratio")]
    public decimal RrRatio { get; set; }

    [JsonPropertyName("reasoning")]
    public string? Reasoning { get; set; }

    [JsonPropertyName("strategy_rejection_reason")]
    public string? StrategyRejectionReason { get; set; }

    [JsonPropertyName("veto_code")]
    public string? VetoCode { get; set; }

    [JsonPropertyName("veto_source")]
    public string? VetoSource { get; set; }

    [JsonPropertyName("model_used")]
    public string? ModelUsed { get; set; }

    [JsonPropertyName("tp_target_type")]
    public string? TpTargetType { get; set; }

    [JsonPropertyName("sl_invalidation_type")]
    public string? SlInvalidationType { get; set; }

    [JsonPropertyName("sweep_reclaim_evidence")]
    public string? SweepReclaimEvidence { get; set; }

    [JsonPropertyName("orderflow_wall_evidence")]
    public string? OrderflowWallEvidence { get; set; }
}

#endregion

#region 2. AI Execution Journal (GET /api/v1/journal/trades & summary)

public class JournalTradesResponseDto
{
    [JsonPropertyName("count")]
    public int Count { get; set; }

    [JsonPropertyName("trades")]
    public List<JournalTradeDto> Trades { get; set; } = new();

    [JsonPropertyName("summary")]
    public JournalSummaryDto? Summary { get; set; }
}

public class JournalTradeDto
{
    [JsonPropertyName("journal_id")]
    public string JournalId { get; set; } = string.Empty;

    [JsonPropertyName("trade_id")]
    public string TradeId { get; set; } = string.Empty;

    [JsonPropertyName("exchange")]
    public string Exchange { get; set; } = "Aster DEX";

    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("strategy_id")]
    public string StrategyId { get; set; } = string.Empty;

    [JsonPropertyName("mode")]
    public string Mode { get; set; } = "LIVE";

    [JsonPropertyName("side")]
    public string Side { get; set; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("signal_timestamp_utc")]
    public string? SignalTimestampUtc { get; set; }

    [JsonPropertyName("first_fill_timestamp_utc")]
    public string? FirstFillTimestampUtc { get; set; }

    [JsonPropertyName("exit_timestamp_utc")]
    public string? ExitTimestampUtc { get; set; }

    [JsonPropertyName("average_entry_price")]
    public decimal AverageEntryPrice { get; set; }

    [JsonPropertyName("average_exit_price")]
    public decimal? AverageExitPrice { get; set; }

    [JsonPropertyName("filled_quantity")]
    public decimal FilledQuantity { get; set; }

    [JsonPropertyName("exit_quantity")]
    public decimal? ExitQuantity { get; set; }

    [JsonPropertyName("entry_fee_usd")]
    public decimal EntryFeeUsd { get; set; }

    [JsonPropertyName("exit_fee_usd")]
    public decimal ExitFeeUsd { get; set; }

    [JsonPropertyName("total_fees_usd")]
    public decimal TotalFeesUsd { get; set; }

    [JsonPropertyName("funding_usd")]
    public decimal FundingUsd { get; set; }

    [JsonPropertyName("gross_pnl_usd")]
    public decimal GrossPnlUsd { get; set; }

    [JsonPropertyName("net_pnl_usd")]
    public decimal NetPnlUsd { get; set; }

    [JsonPropertyName("realized_r")]
    public decimal? RealizedR { get; set; }

    [JsonPropertyName("exit_reason")]
    public string? ExitReason { get; set; }

    [JsonPropertyName("llm_reason")]
    public string? LlmReason { get; set; }

    [JsonPropertyName("notes")]
    public string? Notes { get; set; }

    public bool IsClosed => Status.Equals("CLOSED", StringComparison.OrdinalIgnoreCase);
    public bool IsWin => NetPnlUsd > 0;
    public string FormattedNetPnl => (NetPnlUsd >= 0 ? "+$" : "-$") + Math.Abs(NetPnlUsd).ToString("F4", CultureInfo.InvariantCulture);
    public string PnlColor => NetPnlUsd > 0 ? "#00E676" : (NetPnlUsd < 0 ? "#FF5252" : "#8B949E");
    public string FormattedEntry => AverageEntryPrice.ToString("F4", CultureInfo.InvariantCulture);
    public string FormattedExit => AverageExitPrice.HasValue ? AverageExitPrice.Value.ToString("F4", CultureInfo.InvariantCulture) : "--";
    public string FormattedRealizedR => RealizedR.HasValue ? $"{RealizedR.Value:+0.00;-0.00}R" : "--";
    
    public DateTime ExitDateUtc => DateTimeOffset.TryParse(ExitTimestampUtc ?? FirstFillTimestampUtc ?? SignalTimestampUtc, out var dt) 
        ? dt.UtcDateTime.Date 
        : DateTime.UtcNow.Date;
}

#endregion

#region 3. Personal Trading Journal Models (Local Desktop SQLite)

public class PersonalTradeRecord
{
    public string TradeId { get; set; } = Guid.NewGuid().ToString("N");
    public DateTime TradeDateUtc { get; set; } = DateTime.UtcNow;
    public string Symbol { get; set; } = "BTCUSDT";
    public string Direction { get; set; } = "LONG";
    public string Exchange { get; set; } = "Aster DEX";
    public decimal EntryPrice { get; set; }
    public decimal? ExitPrice { get; set; }
    public decimal Quantity { get; set; }
    public decimal Fees { get; set; }
    public decimal FundingCosts { get; set; }
    public decimal? GrossPnl { get; set; }
    public decimal? NetPnl { get; set; }
    public string Strategy { get; set; } = "Discretionary SMC";
    public string EntryRationale { get; set; } = string.Empty;
    public string ExitRationale { get; set; } = string.Empty;
    public string LessonsLearned { get; set; } = string.Empty;
    public string Notes { get; set; } = string.Empty;
    public bool IsClosed { get; set; } = true;
    public DateTime CreatedAtUtc { get; set; } = DateTime.UtcNow;
    public DateTime UpdatedAtUtc { get; set; } = DateTime.UtcNow;

    public void CalculatePnl()
    {
        if (ExitPrice.HasValue && Quantity > 0)
        {
            if (Direction.Equals("LONG", StringComparison.OrdinalIgnoreCase))
            {
                GrossPnl = (ExitPrice.Value - EntryPrice) * Quantity;
            }
            else
            {
                GrossPnl = (EntryPrice - ExitPrice.Value) * Quantity;
            }
            NetPnl = GrossPnl.Value - Fees - FundingCosts;
            IsClosed = true;
        }
        else
        {
            GrossPnl = null;
            NetPnl = null;
            IsClosed = false;
        }
    }

    public string FormattedNetPnl => NetPnl.HasValue 
        ? (NetPnl.Value >= 0 ? "+$" : "-$") + Math.Abs(NetPnl.Value).ToString("F2", CultureInfo.InvariantCulture)
        : "-- (OPEN)";
    public string PnlColor => NetPnl.HasValue && NetPnl.Value > 0 ? "#00E676" : (NetPnl.HasValue && NetPnl.Value < 0 ? "#FF5252" : "#8B949E");
    public string FormattedEntry => EntryPrice.ToString("F4", CultureInfo.InvariantCulture);
    public string FormattedExit => ExitPrice.HasValue ? ExitPrice.Value.ToString("F4", CultureInfo.InvariantCulture) : "--";
}

#endregion

#region 4. Chart & Liquidity Workstation (GET /api/chart/liquidity_layers & klines)

public class ChartSymbolsResponseDto
{
    [JsonPropertyName("symbols")]
    public List<string> Symbols { get; set; } = new();

    [JsonPropertyName("default_symbol")]
    public string DefaultSymbol { get; set; } = "BTCUSDT";

    [JsonPropertyName("default_timeframe")]
    public string DefaultTimeframe { get; set; } = "1h";

    [JsonPropertyName("supported_timeframes")]
    public List<string> SupportedTimeframes { get; set; } = new();
}

public class KlineDto
{
    [JsonPropertyName("time")]
    public long Time { get; set; }

    [JsonPropertyName("open")]
    public decimal Open { get; set; }

    [JsonPropertyName("high")]
    public decimal High { get; set; }

    [JsonPropertyName("low")]
    public decimal Low { get; set; }

    [JsonPropertyName("close")]
    public decimal Close { get; set; }

    [JsonPropertyName("volume")]
    public decimal Volume { get; set; }

    [JsonPropertyName("quote_volume")]
    public decimal QuoteVolume { get; set; }

    [JsonPropertyName("timestamp_ms")]
    public long TimestampMs { get; set; }

    [JsonPropertyName("datetime_utc")]
    public string? DatetimeUtc { get; set; }
}

public class LiquidityLayersResponseDto
{
    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("timeframe")]
    public string Timeframe { get; set; } = string.Empty;

    [JsonPropertyName("venue_source")]
    public string? VenueSource { get; set; }

    [JsonPropertyName("feed_status")]
    public FeedStatusDto? FeedStatus { get; set; }

    [JsonPropertyName("candles")]
    public List<KlineDto> Candles { get; set; } = new();

    [JsonPropertyName("structural_levels")]
    public List<StructuralLevelDto> StructuralLevels { get; set; } = new();

    [JsonPropertyName("sweep_events")]
    public List<SweepEventDto> SweepEvents { get; set; } = new();

    [JsonPropertyName("order_book_overlay")]
    public OrderBookOverlayDto? OrderBookOverlay { get; set; }
}

public class FeedStatusDto
{
    [JsonPropertyName("source")]
    public string? Source { get; set; }

    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("is_stale")]
    public bool IsStale { get; set; }

    [JsonPropertyName("order_book_status")]
    public string? OrderBookStatus { get; set; }

    [JsonPropertyName("order_book_age_seconds")]
    public double? OrderBookAgeSeconds { get; set; }

    [JsonPropertyName("candle_age_seconds")]
    public double? CandleAgeSeconds { get; set; }

    [JsonPropertyName("candle_count")]
    public int CandleCount { get; set; }

    [JsonPropertyName("error_message")]
    public string? ErrorMessage { get; set; }
}

public class StructuralLevelDto
{
    [JsonPropertyName("level_id")]
    public string? LevelId { get; set; }

    [JsonPropertyName("level_type")]
    public string? LevelType { get; set; }

    [JsonPropertyName("type")]
    public string? LegacyType
    {
        get => LevelType;
        set { if (LevelType == null) LevelType = value; }
    }

    [JsonPropertyName("price")]
    public decimal Price { get; set; }

    [JsonPropertyName("zone_top")]
    public decimal? ZoneTop { get; set; }

    [JsonPropertyName("zone_bottom")]
    public decimal? ZoneBottom { get; set; }

    [JsonPropertyName("created_timestamp_ms")]
    public long? CreatedTimestampMs { get; set; }

    [JsonPropertyName("confirmed_timestamp_ms")]
    public long? ConfirmedTimestampMs { get; set; }

    [JsonPropertyName("timestamp_ms")]
    public long? LegacyTimestampMs
    {
        get => ConfirmedTimestampMs;
        set { if (ConfirmedTimestampMs == null) ConfirmedTimestampMs = value; }
    }

    [JsonPropertyName("timeframe")]
    public string? Timeframe { get; set; }

    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("source_label")]
    public string? SourceLabel { get; set; }

    [JsonPropertyName("is_htf")]
    public bool IsHtf { get; set; }

    [JsonPropertyName("is_broken")]
    public bool IsBroken { get; set; }
}

public class SweepEventDto
{
    [JsonPropertyName("event_id")]
    public string? EventId { get; set; }

    [JsonPropertyName("direction")]
    public string? Direction { get; set; }

    [JsonPropertyName("side")]
    public string? LegacySide
    {
        get => Direction;
        set { if (Direction == null) Direction = value; }
    }

    [JsonIgnore]
    public string Side => Direction ?? LegacySide ?? "LONG";

    [JsonPropertyName("sweep_price")]
    public decimal SweepPrice { get; set; }

    [JsonPropertyName("reclaim_price")]
    public decimal? ReclaimPrice { get; set; }

    [JsonPropertyName("sweep_timestamp_ms")]
    public long? SweepTimestampMs { get; set; }

    [JsonPropertyName("timestamp_ms")]
    public long? LegacyTimestampMs
    {
        get => SweepTimestampMs;
        set { if (SweepTimestampMs == null) SweepTimestampMs = value; }
    }

    [JsonIgnore]
    public long TimestampMs => SweepTimestampMs ?? LegacyTimestampMs ?? 0;

    [JsonPropertyName("sweep_depth_usdt")]
    public decimal? SweepDepthUsdt { get; set; }

    [JsonPropertyName("volume_usdt")]
    public decimal? LegacyVolumeUsdt
    {
        get => SweepDepthUsdt;
        set { if (SweepDepthUsdt == null) SweepDepthUsdt = value; }
    }

    [JsonIgnore]
    public decimal VolumeUsdt => SweepDepthUsdt ?? LegacyVolumeUsdt ?? 0m;

    [JsonPropertyName("sweep_depth_bps")]
    public decimal? SweepDepthBps { get; set; }

    [JsonPropertyName("invalidation_price")]
    public decimal? InvalidationPrice { get; set; }

    [JsonPropertyName("target_tp1")]
    public decimal? TargetTp1 { get; set; }

    [JsonPropertyName("target_tp2")]
    public decimal? TargetTp2 { get; set; }

    [JsonPropertyName("target_sl")]
    public decimal? TargetSl { get; set; }

    [JsonPropertyName("is_valid_entry_candidate")]
    public bool IsValidEntryCandidate { get; set; }

    [JsonPropertyName("evidence_text")]
    public string? EvidenceText { get; set; }

    [JsonIgnore]
    public string EventTimeUtc => TimestampMs > 0 
        ? DateTimeOffset.FromUnixTimeMilliseconds(TimestampMs).UtcDateTime.ToString("HH:mm:ss") 
        : "--:--:--";
}

public class OrderBookOverlayDto
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("mid_price")]
    public decimal? MidPrice { get; set; }

    [JsonPropertyName("spread_bps")]
    public decimal? SpreadBps { get; set; }

    [JsonPropertyName("timestamp_ms")]
    public long? TimestampMs { get; set; }

    [JsonPropertyName("age_seconds")]
    public double? AgeSeconds { get; set; }

    [JsonPropertyName("total_bid_depth_usdt")]
    public decimal? TotalBidDepthUsdt { get; set; }

    [JsonPropertyName("total_ask_depth_usdt")]
    public decimal? TotalAskDepthUsdt { get; set; }

    [JsonPropertyName("walls")]
    public List<OrderBookWallDto> Walls { get; set; } = new();

    [JsonPropertyName("bids_buckets")]
    public List<OrderBookBucketDto> BidsBuckets { get; set; } = new();

    [JsonPropertyName("asks_buckets")]
    public List<OrderBookBucketDto> AsksBuckets { get; set; } = new();

    [JsonPropertyName("persistent_walls")]
    public List<OrderBookWallDto> PersistentWalls { get; set; } = new();

    [JsonPropertyName("contributing_venues")]
    public List<string> ContributingVenues { get; set; } = new();

    [JsonPropertyName("venue_filter")]
    public string? VenueFilter { get; set; }

    [JsonPropertyName("venue_name")]
    public string? VenueName { get; set; }

    [JsonPropertyName("error_message")]
    public string? ErrorMessage { get; set; }
}

public class OrderBookBucketDto
{
    [JsonPropertyName("bucket_price")]
    public decimal BucketPrice { get; set; }

    [JsonPropertyName("side")]
    public string Side { get; set; } = string.Empty;

    [JsonPropertyName("distance_bps")]
    public decimal? DistanceBps { get; set; }

    [JsonPropertyName("total_notional_usdt")]
    public decimal TotalNotionalUsdt { get; set; }

    [JsonPropertyName("total_quantity")]
    public decimal TotalQuantity { get; set; }

    [JsonPropertyName("venue_count")]
    public int VenueCount { get; set; }

    [JsonPropertyName("is_cross_confirmed")]
    public bool IsCrossConfirmed { get; set; }

    [JsonPropertyName("confirmation_tier")]
    public string? ConfirmationTier { get; set; }
}

public class OrderBookWallDto
{
    [JsonPropertyName("price")]
    public decimal Price { get; set; }

    [JsonPropertyName("depth_usdt")]
    public decimal? DepthUsdt { get; set; }

    [JsonPropertyName("notional_usdt")]
    public decimal? LegacyNotionalUsdt
    {
        get => DepthUsdt;
        set { if (DepthUsdt == null) DepthUsdt = value; }
    }

    [JsonIgnore]
    public decimal NotionalUsdt => DepthUsdt ?? LegacyNotionalUsdt ?? 0m;

    [JsonPropertyName("quantity")]
    public decimal? Quantity { get; set; }

    [JsonPropertyName("side")]
    public string Side { get; set; } = string.Empty;

    [JsonPropertyName("distance_bps")]
    public decimal? DistanceBps { get; set; }

    [JsonPropertyName("distance_pct")]
    public decimal? LegacyDistancePct
    {
        get => DistanceBps.HasValue ? DistanceBps.Value / 100m : null;
        set { if (!DistanceBps.HasValue && value.HasValue) DistanceBps = value.Value * 100m; }
    }

    [JsonIgnore]
    public decimal DistancePct => LegacyDistancePct ?? (DistanceBps.HasValue ? DistanceBps.Value / 100m : 0m);

    [JsonPropertyName("multiple")]
    public decimal? Multiple { get; set; }

    [JsonPropertyName("persistence_sec")]
    public double? PersistenceSec { get; set; }

    [JsonPropertyName("is_persistent")]
    public bool IsPersistent { get; set; }

    [JsonPropertyName("venue")]
    public string? Venue { get; set; }

    [JsonPropertyName("venues")]
    public List<string>? Venues { get; set; }

    [JsonPropertyName("confirmation_tier")]
    public string? ConfirmationTier { get; set; }

    [JsonIgnore]
    public string FormattedPrice => Price.ToString("F4", CultureInfo.InvariantCulture);

    [JsonIgnore]
    public string FormattedNotional => "$" + NotionalUsdt.ToString("N0", CultureInfo.InvariantCulture);

    [JsonIgnore]
    public string FormattedDistance => (DistancePct >= 0 ? "+" : "") + DistancePct.ToString("F2", CultureInfo.InvariantCulture) + "%";
}

#endregion

#region 5. Alerts & Retail Positioning (GET /api/alerts & retail_positioning)

public class AlertItemDto
{
    [JsonPropertyName("alert_id")]
    public string AlertId { get; set; } = string.Empty;

    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("target_price")]
    public decimal TargetPrice { get; set; }

    [JsonPropertyName("direction")]
    public string Direction { get; set; } = string.Empty;

    [JsonPropertyName("price_source")]
    public string PriceSource { get; set; } = string.Empty;

    [JsonPropertyName("alert_type")]
    public string AlertType { get; set; } = string.Empty;

    [JsonPropertyName("is_active")]
    public bool IsActive { get; set; }

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("armed")]
    public bool Armed { get; set; }

    [JsonPropertyName("current_price")]
    public decimal? CurrentPrice { get; set; }

    [JsonPropertyName("last_checked_price")]
    public decimal? LastCheckedPrice { get; set; }

    [JsonPropertyName("distance_pct")]
    public decimal? DistancePct { get; set; }

    [JsonPropertyName("last_triggered_at")]
    public string? LastTriggeredAt { get; set; }

    [JsonPropertyName("created_at")]
    public string? CreatedAt { get; set; }

    [JsonPropertyName("note")]
    public string? Note { get; set; }

    public string FormattedTarget => TargetPrice.ToString("F4", CultureInfo.InvariantCulture);
    public string FormattedCurrent => CurrentPrice.HasValue ? CurrentPrice.Value.ToString("F4", CultureInfo.InvariantCulture) : "--";
    public string FormattedDistance => DistancePct.HasValue ? DistancePct.Value.ToString("F2", CultureInfo.InvariantCulture) + "%" : "--";
}

public class RetailPositioningResponseDto
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("provider")]
    public string Provider { get; set; } = string.Empty;

    [JsonPropertyName("metric")]
    public string Metric { get; set; } = string.Empty;

    [JsonPropertyName("metric_definition")]
    public string MetricDefinition { get; set; } = string.Empty;

    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("period")]
    public string Period { get; set; } = string.Empty;

    [JsonPropertyName("observations")]
    public List<RetailObservationDto> Observations { get; set; } = new();

    [JsonPropertyName("source_url")]
    public string? SourceUrl { get; set; }

    [JsonPropertyName("cache_status")]
    public string? CacheStatus { get; set; }
}

public class RetailObservationDto
{
    [JsonPropertyName("observation_timestamp_ms")]
    public long ObservationTimestampMs { get; set; }

    [JsonPropertyName("timestamp_ms")]
    public long TimestampMs { get; set; }

    [JsonPropertyName("long_account_ratio")]
    public decimal LongAccountRatio { get; set; }

    [JsonPropertyName("short_account_ratio")]
    public decimal ShortAccountRatio { get; set; }

    [JsonPropertyName("long_short_account_ratio")]
    public decimal LongShortAccountRatio { get; set; }

    // Fallback bindings
    [JsonPropertyName("buy_ratio")]
    public decimal BuyRatio { get; set; }

    [JsonPropertyName("sell_ratio")]
    public decimal SellRatio { get; set; }

    public decimal EffectiveBuyRatio => LongAccountRatio > 0 ? LongAccountRatio : BuyRatio;
    public decimal EffectiveSellRatio => ShortAccountRatio > 0 ? ShortAccountRatio : SellRatio;
    public long EffectiveTimestampMs => ObservationTimestampMs > 0 ? ObservationTimestampMs : TimestampMs;

    public string TimestampUtcFormatted => EffectiveTimestampMs > 0 
        ? DateTimeOffset.FromUnixTimeMilliseconds(EffectiveTimestampMs).UtcDateTime.ToString("HH:mm:ss")
        : "--:--:--";
    public string BuyRatioPct => (EffectiveBuyRatio * 100).ToString("F1", CultureInfo.InvariantCulture) + "%";
    public string SellRatioPct => (EffectiveSellRatio * 100).ToString("F1", CultureInfo.InvariantCulture) + "%";
}

#endregion

#region 6. Global Market Intelligence (RSS & News Summaries)

public class NewsArticleDto
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string Title { get; set; } = string.Empty;
    public string Publisher { get; set; } = string.Empty;
    public DateTime PublishDateUtc { get; set; } = DateTime.UtcNow;
    public string Excerpt { get; set; } = string.Empty;
    public string? AiSummary { get; set; }
    public string Url { get; set; } = string.Empty;
    public string Category { get; set; } = "GLOBAL_MACRO";
    public string SentimentBias { get; set; } = "NEUTRAL";
    public string AffectedAssets { get; set; } = "CRYPTO / FX";
    public DateTime? SummaryGeneratedUtc { get; set; }
    public bool IsAiSummarized => !string.IsNullOrWhiteSpace(AiSummary);
    public bool IsStale { get; set; }
    public string DisplayTime => PublishDateUtc.ToString("HH:mm") + " UTC";
}

#endregion

#region 7. Overview Command Center (FX, Market Pulse & Operational Stream)

public class FxDataPointDto
{
    public DateTime Date { get; set; }
    public decimal Rate { get; set; }
}

public class FxHistoricalSeriesDto
{
    public string Period { get; set; } = "1M";
    public decimal CurrentRate { get; set; }
    public decimal StartRate { get; set; }
    public decimal ChangePct { get; set; }
    public decimal MinRate { get; set; }
    public decimal MaxRate { get; set; }
    public List<FxDataPointDto> Points { get; set; } = new();
    public DateTime FetchedAtUtc { get; set; } = DateTime.UtcNow;
    public string Source { get; set; } = "European Central Bank (ECB) via Frankfurter API";
    public bool IsStale { get; set; }
    public string? ErrorMessage { get; set; }

    public string FormattedCurrentRate => $"Rp {CurrentRate.ToString("N2", CultureInfo.InvariantCulture)}";
    public string FormattedChange => (ChangePct >= 0 ? "+" : "") + $"{ChangePct.ToString("F2", CultureInfo.InvariantCulture)}% ({Period})";
    public string ChangeColor => ChangePct > 0 ? "#00E676" : (ChangePct < 0 ? "#FF5252" : "#8B949E");
    public string FormattedUsdtEstimate => $"~Rp {CurrentRate.ToString("N2", CultureInfo.InvariantCulture)} (1:1 USD Peg Est.)";
    public string DisplayFetchedTime => FetchedAtUtc.ToString("HH:mm:ss") + " UTC";
}

public class FxRateSnapshotDto
{
    public decimal Rate { get; set; }
    public DateTime Date { get; set; }
    public string Source { get; set; } = "European Central Bank (ECB)";
    public bool IsStale { get; set; }
    public string? ErrorMessage { get; set; }
}

public class MarketPulseItemDto
{
    public string Symbol { get; set; } = string.Empty;
    public decimal Price { get; set; }
    public decimal Change24hPct { get; set; }
    public decimal Volume24hUsdt { get; set; }
    public decimal? FundingRatePct { get; set; }
    public decimal? OpenInterestUsdt { get; set; }
    public string Venue { get; set; } = "AsterDEX / Multi";
    public string TimestampDisplay { get; set; } = "--";

    public string ChangeColor => Change24hPct > 0 ? "#00E676" : (Change24hPct < 0 ? "#FF5252" : "#8B949E");
    public string FormattedChange => (Change24hPct >= 0 ? "+" : "") + Change24hPct.ToString("F2", CultureInfo.InvariantCulture) + "%";
    public string FormattedPrice => Price >= 100 ? $"${Price:N2}" : $"${Price:N4}";
    public string FormattedVolume => Volume24hUsdt >= 1_000_000_000 ? $"${Volume24hUsdt / 1_000_000_000:F2}B" : $"${Volume24hUsdt / 1_000_000:F2}M";
    public string FormattedOi => OpenInterestUsdt.HasValue 
        ? (OpenInterestUsdt.Value >= 1_000_000_000 ? $"${OpenInterestUsdt.Value / 1_000_000_000:F2}B" : $"${OpenInterestUsdt.Value / 1_000_000:F2}M")
        : "--";
    public string FormattedFunding => FundingRatePct.HasValue ? $"{FundingRatePct.Value:F4}%" : "--";
}

public class RecentOperationalActivityDto
{
    public string TimestampDisplay { get; set; } = "--";
    public string Category { get; set; } = string.Empty;
    public string Symbol { get; set; } = "--";
    public string Status { get; set; } = string.Empty;
    public string Explanation { get; set; } = string.Empty;
    public string Source { get; set; } = string.Empty;

    public string StatusColor => Status.ToUpperInvariant() switch
    {
        "SUCCESS" or "OK" or "APPROVED" or "CLOSED_WIN" => "#00E676",
        "REJECTED" or "FAILED" or "CLOSED_LOSS" => "#FF5252",
        "RUNNING" or "ACTIVE" or "OPEN" => "#FFB300",
        _ => "#8B949E"
    };
}

#endregion
