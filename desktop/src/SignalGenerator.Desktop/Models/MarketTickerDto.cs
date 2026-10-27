using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace SignalGenerator.Desktop.Models;

public class ScreenerResponseDto
{
    [JsonPropertyName("items")]
    public List<MarketAssetTickerDto> Items { get; set; } = new();

    [JsonPropertyName("data")]
    public List<MarketAssetTickerDto>? Data
    {
        get => Items;
        set { if (value != null) Items = value; }
    }

    [JsonPropertyName("total_count")]
    public int TotalCount { get; set; }

    [JsonPropertyName("total_matched")]
    public int? TotalMatched
    {
        get => TotalCount;
        set { if (value.HasValue) TotalCount = value.Value; }
    }

    [JsonPropertyName("page")]
    public int Page { get; set; }

    [JsonPropertyName("page_size")]
    public int PageSize { get; set; }

    [JsonPropertyName("total_pages")]
    public int TotalPages { get; set; }
}

public class MarketAssetTickerDto
{
    [JsonPropertyName("normalized_symbol")]
    public string NormalizedSymbol { get; set; } = string.Empty;

    [JsonPropertyName("base_asset")]
    public string BaseAsset { get; set; } = string.Empty;

    [JsonPropertyName("quote_asset")]
    public string QuoteAsset { get; set; } = "USDT";

    [JsonPropertyName("total_volume_24h_usdt")]
    public decimal TotalVolume24hUsdt { get; set; }

    [JsonPropertyName("total_open_interest_usdt")]
    public decimal TotalOpenInterestUsdt { get; set; }

    [JsonPropertyName("oi_change_1h_pct")]
    public decimal? OiChange1hPct { get; set; }

    [JsonPropertyName("oi_change_24h_pct")]
    public decimal? OiChange24hPct { get; set; }

    [JsonPropertyName("benchmark_price")]
    public decimal? BenchmarkPrice { get; set; }

    [JsonPropertyName("price_change_24h_pct")]
    public decimal? PriceChange24hPct { get; set; }

    [JsonPropertyName("venue_count")]
    public int VenueCount { get; set; }

    [JsonPropertyName("cross_listed")]
    public bool CrossListed { get; set; }

    [JsonPropertyName("best_spread_bps")]
    public decimal? BestSpreadBps { get; set; }

    [JsonPropertyName("data_freshness_seconds")]
    public decimal DataFreshnessSeconds { get; set; }

    [JsonPropertyName("venues")]
    public Dictionary<string, VenueDetailDto>? Venues { get; set; }

    public string InstrumentType
    {
        get
        {
            if (Venues != null)
            {
                foreach (var v in Venues.Values)
                {
                    if (!string.IsNullOrWhiteSpace(v.ContractType))
                    {
                        return v.ContractType.ToUpperInvariant();
                    }
                }
            }
            return "PERPETUAL";
        }
    }

    public string FormattedPrice => BenchmarkPrice.HasValue ? BenchmarkPrice.Value.ToString("F4", System.Globalization.CultureInfo.InvariantCulture) : "--";
    public string FormattedVolume => TotalVolume24hUsdt > 0 ? string.Format(System.Globalization.CultureInfo.InvariantCulture, "${0:N0}", TotalVolume24hUsdt) : "--";
    public string FormattedOi => TotalOpenInterestUsdt > 0 ? string.Format(System.Globalization.CultureInfo.InvariantCulture, "${0:N0}", TotalOpenInterestUsdt) : "--";
    public string FormattedSpread => BestSpreadBps.HasValue ? string.Format(System.Globalization.CultureInfo.InvariantCulture, "{0:F2} bps", BestSpreadBps.Value) : "--";
    public string FormattedChange => PriceChange24hPct.HasValue ? string.Format(System.Globalization.CultureInfo.InvariantCulture, "{0:+0.00;-0.00}%", PriceChange24hPct.Value) : "--";
    public string ChangeColor => (PriceChange24hPct ?? 0) >= 0 ? "#00E676" : "#FF5252";
}

public class VenueDetailDto
{
    [JsonPropertyName("symbol")]
    public string Symbol { get; set; } = string.Empty;

    [JsonPropertyName("exchange")]
    public string Exchange { get; set; } = string.Empty;

    [JsonPropertyName("contract_type")]
    public string ContractType { get; set; } = "PERPETUAL";

    [JsonPropertyName("mark_price")]
    public decimal? MarkPrice { get; set; }

    [JsonPropertyName("last_price")]
    public decimal? LastPrice { get; set; }

    [JsonPropertyName("price_change_24h_pct")]
    public decimal? PriceChange24hPct { get; set; }

    [JsonPropertyName("volume_24h_usdt")]
    public decimal? Volume24hUsdt { get; set; }

    [JsonPropertyName("open_interest_notional")]
    public decimal? OpenInterestNotional { get; set; }

    [JsonPropertyName("best_bid")]
    public decimal? BestBid { get; set; }

    [JsonPropertyName("best_ask")]
    public decimal? BestAsk { get; set; }

    [JsonPropertyName("spread_bps")]
    public decimal? SpreadBps { get; set; }

    [JsonPropertyName("status")]
    public string Status { get; set; } = "AVAILABLE";
}
