using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace SignalGenerator.Desktop.Models;

public class ScreenerResponseDto
{
    [JsonPropertyName("items")]
    public List<MarketAssetTickerDto> Items { get; set; } = new();

    [JsonPropertyName("total_count")]
    public int TotalCount { get; set; }

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
}
