using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Text.Json.Serialization;

namespace SignalGenerator.Desktop.Models;

public enum OrderSide
{
    Buy,
    Sell
}

public enum OrderType
{
    Limit,
    Market,
    StopLimit
}

public enum TimeInForce
{
    Gtc,
    Ioc,
    Fok
}

public enum OrderStatus
{
    New,
    PartiallyFilled,
    Filled,
    Cancelled,
    Rejected,
    Expired
}

public enum MarketType
{
    Perpetual,
    Spot
}

public class PaperOrderModel
{
    public string OrderId { get; set; } = string.Empty;
    public string ClientOrderId { get; set; } = string.Empty;
    public string Symbol { get; set; } = string.Empty;
    public MarketType MarketType { get; set; } = MarketType.Perpetual;
    public OrderSide Side { get; set; }
    public OrderType OrderType { get; set; }
    public decimal Price { get; set; }
    public decimal? StopPrice { get; set; }
    public decimal OriginalQty { get; set; }
    public decimal FilledQty { get; set; }
    public decimal RemainingQty => Math.Max(0m, OriginalQty - FilledQty);
    public OrderStatus Status { get; set; } = OrderStatus.New;
    public TimeInForce TimeInForce { get; set; } = TimeInForce.Gtc;
    public bool ReduceOnly { get; set; }
    public bool PostOnly { get; set; }
    public decimal FeeUsdt { get; set; }
    public decimal NotionalUsdt => Price * OriginalQty;
    public DateTime CreatedAtUtc { get; set; } = DateTime.UtcNow;
    public DateTime UpdatedAtUtc { get; set; } = DateTime.UtcNow;
    public bool IsSimulated { get; set; } = true;
    public string SimulationWatermark => "[SIMULATED - PAPER TRADING]";

    public string SideDisplay => Side == OrderSide.Buy ? "BUY / LONG" : "SELL / SHORT";
    public string SideColor => Side == OrderSide.Buy ? "#00E676" : "#FF5252";
    public string PriceFormatted => $"${Price:N2}";
    public string NotionalFormatted => $"${NotionalUsdt:N2}";
    public string FeeFormatted => $"${FeeUsdt:N4}";
    public string StatusColor => Status switch
    {
        OrderStatus.Filled => "#00E676",
        OrderStatus.New => "#FF9D00",
        OrderStatus.PartiallyFilled => "#448AFF",
        OrderStatus.Cancelled => "#8B949E",
        OrderStatus.Rejected => "#FF5252",
        _ => "#8B949E"
    };
    public string TimeDisplay => CreatedAtUtc.ToString("HH:mm:ss");
}

public class PaperPositionModel
{
    public string Symbol { get; set; } = string.Empty;
    public MarketType MarketType { get; set; } = MarketType.Perpetual;
    public string Side { get; set; } = "LONG"; // LONG or SHORT
    public decimal Size { get; set; }
    public decimal EntryPrice { get; set; }
    public decimal MarkPrice { get; set; }
    public decimal? LiquidationPrice { get; set; }
    public decimal UnrealizedPnl { get; set; }
    public decimal UnrealizedPnlPct { get; set; }
    public decimal Margin { get; set; }
    public int Leverage { get; set; } = 10;
    public decimal? TakeProfitPrice { get; set; }
    public decimal? StopLossPrice { get; set; }
    public DateTime UpdatedAtUtc { get; set; } = DateTime.UtcNow;
    public bool IsSimulated { get; set; } = true;

    public string SideColor => Side.Equals("LONG", StringComparison.OrdinalIgnoreCase) ? "#00E676" : "#FF5252";
    public string PnlColor => UnrealizedPnl >= 0 ? "#00E676" : "#FF5252";
    public string PnlDisplay => $"{(UnrealizedPnl >= 0 ? "+" : "")}${UnrealizedPnl:N2} ({UnrealizedPnlPct:N2}%)";
    public string EntryPriceFormatted => $"${EntryPrice:N2}";
    public string MarkPriceFormatted => $"${MarkPrice:N2}";
    public string LiqPriceFormatted => LiquidationPrice.HasValue ? $"${LiquidationPrice.Value:N2}" : "--";
    public string MarginFormatted => $"${Margin:N2}";
}

public class PaperFillModel
{
    public string FillId { get; set; } = string.Empty;
    public string OrderId { get; set; } = string.Empty;
    public string Symbol { get; set; } = string.Empty;
    public OrderSide Side { get; set; }
    public decimal Price { get; set; }
    public decimal Qty { get; set; }
    public decimal NotionalUsdt => Price * Qty;
    public decimal FeeUsdt { get; set; }
    public decimal RealizedPnlUsdt { get; set; }
    public DateTime TimestampUtc { get; set; } = DateTime.UtcNow;
    public string Venue { get; set; } = "ASTERDEX_PAPER";
    public bool IsSimulated { get; set; } = true;

    public string SideDisplay => Side == OrderSide.Buy ? "BUY" : "SELL";
    public string SideColor => Side == OrderSide.Buy ? "#00E676" : "#FF5252";
    public string PnlDisplay => RealizedPnlUsdt != 0 
        ? $"{(RealizedPnlUsdt >= 0 ? "+" : "")}${RealizedPnlUsdt:N2}" 
        : "$0.00";
    public string TimeDisplay => TimestampUtc.ToString("HH:mm:ss");
    public string PriceFormatted => $"${Price:N2}";
    public string NotionalFormatted => $"${NotionalUsdt:N2}";
    public string FeeFormatted => $"${FeeUsdt:N4}";
}

public class PaperAccountModel
{
    public decimal StartingBalanceUsdt { get; set; } = 10000.00m;
    public decimal WalletBalanceUsdt { get; set; } = 10000.00m;
    public decimal TotalEquityUsdt { get; set; } = 10000.00m;
    public decimal AvailableBalanceUsdt { get; set; } = 10000.00m;
    public decimal UsedMarginUsdt { get; set; } = 0m;
    public decimal UnrealizedPnlUsdt { get; set; } = 0m;
    public decimal RealizedPnlUsdt { get; set; } = 0m;
    public bool IsSimulated { get; set; } = true;
    public DateTime LastUpdatedUtc { get; set; } = DateTime.UtcNow;
}

public class OrderValidationResult
{
    public bool IsValid { get; set; }
    public string? ErrorMessage { get; set; }
    public List<string> Warnings { get; set; } = new();
    public decimal EstimatedNotional { get; set; }
    public decimal EstimatedFee { get; set; }
    public decimal EstimatedRequiredMargin { get; set; }
}

public class OrderBookLevelItem
{
    public decimal Price { get; set; }
    public decimal Size { get; set; }
    public decimal Notional => Price * Size;
    public decimal CumulativeNotional { get; set; }
    public double DepthPct { get; set; }
    public bool IsWall { get; set; }
    public bool IsChanged { get; set; }
    public string ChangeBrush => IsChanged ? "#304A24" : "Transparent";

    public string PriceFormatted => Price > 100 ? Price.ToString("N2") : Price > 1 ? Price.ToString("N4") : Price.ToString("N6");
    public string SizeFormatted => Size.ToString("N3");
    public string NotionalFormatted => $"${Notional:N1}";
    public string CumulativeFormatted => $"${CumulativeNotional:N0}";
}

public class MarketBookTileModel
{
    public string Symbol { get; set; } = "BTCUSDT";
    public string MarketType { get; set; } = "PERP";
    public decimal LastPrice { get; set; }
    public decimal PriceChange24hPct { get; set; }
    public decimal BestBid { get; set; }
    public decimal BestAsk { get; set; }
    public decimal Spread => Math.Max(0m, BestAsk - BestBid);
    public decimal SpreadBps => LastPrice > 0 ? (Spread / LastPrice) * 10000m : 0m;
    public decimal BidDepthNotional { get; set; }
    public decimal AskDepthNotional { get; set; }
    public decimal MajorBidWallPrice { get; set; }
    public decimal MajorBidWallNotional { get; set; }
    public decimal MajorAskWallPrice { get; set; }
    public decimal MajorAskWallNotional { get; set; }
    public long FeedAgeMs { get; set; }
    public string ConnectionState { get; set; } = "LOADING"; // LOADING, LIVE, STALE, UNAVAILABLE, ERROR
    public string LastUpdatedText { get; set; } = "--:--:--";
    public ObservableCollection<OrderBookLevelItem> Bids { get; set; } = new();
    public ObservableCollection<OrderBookLevelItem> Asks { get; set; } = new();

    public string PriceColor => PriceChange24hPct >= 0 ? "#00E676" : "#FF5252";
    public string StatusBadgeColor => ConnectionState switch
    {
        "LIVE" => "#00E676",
        "LOADING" => "#8B949E",
        "UNAVAILABLE" => "#FF9D00",
        "STALE" => "#FF9D00",
        _ => "#FF5252"
    };

    public string LastPriceFormatted => $"${LastPrice:N2}";
    public string BestBidFormatted => $"${BestBid:N2}";
    public string BestAskFormatted => $"${BestAsk:N2}";
    public string SpreadFormatted => $"{SpreadBps:N2} bps";
    public string MajorBidWallFormatted => $"${MajorBidWallPrice:N1}";
}

#region Cross-Book & Order Book API DTOs

public class CrossBookResponseDto
{
    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("contract_type")]
    public string? ContractType { get; set; }

    [JsonPropertyName("verified_contract")]
    public bool VerifiedContract { get; set; }

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("composite_mid")]
    public decimal? CompositeMid { get; set; }

    [JsonPropertyName("timestamp_ms")]
    public long? TimestampMs { get; set; }

    [JsonPropertyName("venue_summaries")]
    public Dictionary<string, CrossBookVenueSummaryDto>? VenueSummaries { get; set; }

    [JsonPropertyName("bids_buckets")]
    public List<CrossBookBucketDto> BidsBuckets { get; set; } = new();

    [JsonPropertyName("asks_buckets")]
    public List<CrossBookBucketDto> AsksBuckets { get; set; } = new();
}

public class CrossBookVenueSummaryDto
{
    [JsonPropertyName("exchange_name")]
    public string? ExchangeName { get; set; }

    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("freshness_status")]
    public string? FreshnessStatus { get; set; }

    [JsonPropertyName("best_bid")]
    public decimal? BestBid { get; set; }

    [JsonPropertyName("best_ask")]
    public decimal? BestAsk { get; set; }

    [JsonPropertyName("mid_price")]
    public decimal? MidPrice { get; set; }

    [JsonPropertyName("spread_bps")]
    public decimal? SpreadBps { get; set; }

    [JsonPropertyName("total_bid_notional_top50")]
    public decimal? TotalBidNotionalTop50 { get; set; }

    [JsonPropertyName("total_ask_notional_top50")]
    public decimal? TotalAskNotionalTop50 { get; set; }
}

public class CrossBookBucketDto
{
    [JsonPropertyName("bucket_price")]
    public decimal BucketPrice { get; set; }

    [JsonPropertyName("side")]
    public string Side { get; set; } = string.Empty;

    [JsonPropertyName("total_notional_usdt")]
    public decimal TotalNotionalUsdt { get; set; }

    [JsonPropertyName("total_quantity")]
    public decimal TotalQuantity { get; set; }
}

public class ModelBOrderBookResponseDto
{
    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("books")]
    public Dictionary<string, ModelBExchangeBookDto>? Books { get; set; }
}

public class ModelBExchangeBookDto
{
    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("exchange")]
    public string Exchange { get; set; } = string.Empty;

    [JsonPropertyName("timestamp_ms")]
    public long? TimestampMs { get; set; }

    [JsonPropertyName("best_bid")]
    public decimal? BestBid { get; set; }

    [JsonPropertyName("best_ask")]
    public decimal? BestAsk { get; set; }

    [JsonPropertyName("mid_price")]
    public decimal? MidPrice { get; set; }

    [JsonPropertyName("spread_bps")]
    public decimal? SpreadBps { get; set; }

    [JsonPropertyName("bids_top5")]
    public List<ModelBPriceLevelDto> BidsTop5 { get; set; } = new();

    [JsonPropertyName("asks_top5")]
    public List<ModelBPriceLevelDto> AsksTop5 { get; set; } = new();
}

public class ModelBPriceLevelDto
{
    [JsonPropertyName("price")]
    public decimal Price { get; set; }

    [JsonPropertyName("size")]
    public decimal Size { get; set; }
}

#endregion
