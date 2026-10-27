using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace SignalGenerator.Desktop.Models;

/// <summary>
/// Screening log response from GET /api/autotrader/screening-log
/// </summary>
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
}

/// <summary>
/// Alert item from GET /api/alerts
/// </summary>
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

    [JsonPropertyName("last_checked_price")]
    public decimal? LastCheckedPrice { get; set; }

    [JsonPropertyName("last_triggered_utc")]
    public string? LastTriggeredUtc { get; set; }

    [JsonPropertyName("note")]
    public string? Note { get; set; }
}

/// <summary>
/// Retail positioning from GET /api/research/retail_positioning
/// </summary>
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
    [JsonPropertyName("timestamp_ms")]
    public long TimestampMs { get; set; }

    [JsonPropertyName("buy_ratio")]
    public decimal BuyRatio { get; set; }

    [JsonPropertyName("sell_ratio")]
    public decimal SellRatio { get; set; }

    public string TimestampUtcFormatted => DateTimeOffset.FromUnixTimeMilliseconds(TimestampMs).UtcDateTime.ToString("HH:mm:ss");
    public string BuyRatioPct => $"{(BuyRatio * 100):F1}%";
    public string SellRatioPct => $"{(SellRatio * 100):F1}%";
}
