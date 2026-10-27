using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public class FxRateService : IFxRateService
{
    private readonly HttpClient _httpClient;
    private readonly ConcurrentDictionary<string, (FxHistoricalSeriesDto Series, DateTime FetchedAt)> _cache = new(StringComparer.OrdinalIgnoreCase);
    private static readonly TimeSpan CacheTtl = TimeSpan.FromMinutes(15);

    // Verified public free endpoints (European Central Bank data via Frankfurter, Open Exchange Rates fallback)
    private const string FrankfurterBase = "https://api.frankfurter.app";
    private const string OpenErApiBase = "https://open.er-api.com/v6/latest/USD";

    public FxRateService(HttpClient httpClient)
    {
        _httpClient = httpClient ?? throw new ArgumentNullException(nameof(httpClient));
    }

    public IReadOnlyList<FxPairConfig> GetSupportedPairs() => FxPairConfig.DefaultPairs;

    public Task<FxRateSnapshotDto> GetLatestRateAsync(CancellationToken ct = default)
        => GetLatestRateAsync("USD/IDR", ct);

    public async Task<FxRateSnapshotDto> GetLatestRateAsync(string pair, CancellationToken ct = default)
    {
        var cfg = FxPairConfig.GetConfig(pair);

        try
        {
            var url = $"{FrankfurterBase}/latest?from={cfg.BaseCurrency}&to={cfg.QuoteCurrency}";
            using var req = new HttpRequestMessage(HttpMethod.Get, url);
            req.Headers.Add("User-Agent", "AMS-Capital-Workstation/1.0");

            using var resp = await _httpClient.SendAsync(req, ct);
            if (resp.IsSuccessStatusCode)
            {
                using var stream = await resp.Content.ReadAsStreamAsync(ct);
                using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
                var root = doc.RootElement;

                if (root.TryGetProperty("rates", out var rates) && rates.TryGetProperty(cfg.QuoteCurrency, out var quoteVal))
                {
                    decimal rate = quoteVal.GetDecimal();
                    DateTime date = root.TryGetProperty("date", out var dProp) && DateTime.TryParse(dProp.GetString(), out var d)
                        ? d
                        : DateTime.UtcNow.Date;

                    return new FxRateSnapshotDto
                    {
                        Pair = cfg.PairKey,
                        Rate = rate,
                        Date = date,
                        Source = "European Central Bank (ECB) via Frankfurter API",
                        RateType = cfg.RateType,
                        IsStale = false,
                        ErrorMessage = null
                    };
                }
            }
        }
        catch (Exception ex)
        {
            // Fallback to Open Exchange Rates
            return await GetLatestRateFallbackAsync(cfg, ex.Message, ct);
        }

        return await GetLatestRateFallbackAsync(cfg, "Frankfurter latest rate returned non-success", ct);
    }

    private async Task<FxRateSnapshotDto> GetLatestRateFallbackAsync(FxPairConfig cfg, string originalError, CancellationToken ct)
    {
        try
        {
            using var req = new HttpRequestMessage(HttpMethod.Get, OpenErApiBase);
            req.Headers.Add("User-Agent", "AMS-Capital-Workstation/1.0");

            using var resp = await _httpClient.SendAsync(req, ct);
            if (resp.IsSuccessStatusCode)
            {
                using var stream = await resp.Content.ReadAsStreamAsync(ct);
                using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
                var root = doc.RootElement;

                if (root.TryGetProperty("rates", out var rates))
                {
                    decimal? calculatedRate = CalculateCrossOrInverseRate(cfg, rates);
                    if (calculatedRate.HasValue)
                    {
                        return new FxRateSnapshotDto
                        {
                            Pair = cfg.PairKey,
                            Rate = calculatedRate.Value,
                            Date = DateTime.UtcNow.Date,
                            Source = "Open Exchange Rates (Public ER-API Fallback)",
                            RateType = "Market Reference Fix (Cross/Inverse Rate)",
                            IsStale = false,
                            ErrorMessage = null
                        };
                    }
                }
            }
        }
        catch (Exception fallbackEx)
        {
            return new FxRateSnapshotDto
            {
                Pair = cfg.PairKey,
                Rate = cfg.FallbackRate,
                Date = DateTime.UtcNow.Date,
                Source = "Offline Reference Cache (ECB Fix)",
                RateType = cfg.RateType,
                IsStale = true,
                ErrorMessage = $"Primary and fallback FX providers unavailable: {originalError}; {fallbackEx.Message}"
            };
        }

        return new FxRateSnapshotDto
        {
            Pair = cfg.PairKey,
            Rate = cfg.FallbackRate,
            Date = DateTime.UtcNow.Date,
            Source = "Offline Reference Cache (ECB Fix)",
            RateType = cfg.RateType,
            IsStale = true,
            ErrorMessage = originalError
        };
    }

    public Task<FxHistoricalSeriesDto> GetHistoricalSeriesAsync(string period, CancellationToken ct = default)
        => GetHistoricalSeriesAsync("USD/IDR", period, ct);

    public async Task<FxHistoricalSeriesDto> GetHistoricalSeriesAsync(string pair, string period, CancellationToken ct = default)
    {
        var cfg = FxPairConfig.GetConfig(pair);
        var normalizedPair = cfg.PairKey;
        var normalizedPeriod = string.IsNullOrWhiteSpace(period) ? "1M" : period.Trim().ToUpperInvariant();
        var cacheKey = $"{normalizedPair}:{normalizedPeriod}";

        // 1. Check in-memory cache
        if (_cache.TryGetValue(cacheKey, out var cached) && (DateTime.UtcNow - cached.FetchedAt) < CacheTtl)
        {
            return cached.Series;
        }

        // 2. Compute date range
        DateTime today = DateTime.UtcNow.Date;
        DateTime startDate = normalizedPeriod switch
        {
            "1D" => today.AddDays(-5),   // 5 days to guarantee at least 2 business day ECB fixes across weekends/holidays
            "1W" => today.AddDays(-8),
            "1M" => today.AddDays(-31),
            "3M" => today.AddDays(-92),
            "1Y" => today.AddDays(-366),
            _ => today.AddDays(-31)
        };

        string startStr = startDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        string endStr = today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

        try
        {
            string url = $"{FrankfurterBase}/{startStr}..{endStr}?from={cfg.BaseCurrency}&to={cfg.QuoteCurrency}";
            using var req = new HttpRequestMessage(HttpMethod.Get, url);
            req.Headers.Add("User-Agent", "AMS-Capital-Workstation/1.0");

            using var resp = await _httpClient.SendAsync(req, ct);
            if (resp.IsSuccessStatusCode)
            {
                using var stream = await resp.Content.ReadAsStreamAsync(ct);
                using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
                var root = doc.RootElement;

                if (root.TryGetProperty("rates", out var ratesProp))
                {
                    var points = new List<FxDataPointDto>();
                    foreach (var dateProp in ratesProp.EnumerateObject())
                    {
                        if (DateTime.TryParse(dateProp.Name, CultureInfo.InvariantCulture, DateTimeStyles.None, out var date) &&
                            dateProp.Value.TryGetProperty(cfg.QuoteCurrency, out var quoteVal))
                        {
                            points.Add(new FxDataPointDto
                            {
                                Date = date,
                                Rate = quoteVal.GetDecimal()
                            });
                        }
                    }

                    points = points.OrderBy(p => p.Date).ToList();

                    // For 1D period, constrain to the last 2 business day observations to compute exact 1-day change
                    if (normalizedPeriod == "1D" && points.Count > 2)
                    {
                        points = points.TakeLast(2).ToList();
                    }

                    if (points.Count > 0)
                    {
                        decimal currentRate = points.Last().Rate;
                        decimal startRate = points.First().Rate;
                        decimal changePct = startRate > 0 ? ((currentRate - startRate) / startRate) * 100m : 0m;
                        decimal minRate = points.Min(p => p.Rate);
                        decimal maxRate = points.Max(p => p.Rate);

                        var result = new FxHistoricalSeriesDto
                        {
                            Pair = normalizedPair,
                            Period = normalizedPeriod,
                            CurrentRate = currentRate,
                            StartRate = startRate,
                            ChangePct = Math.Round(changePct, 2),
                            MinRate = minRate,
                            MaxRate = maxRate,
                            Points = points,
                            FetchedAtUtc = DateTime.UtcNow,
                            Source = "European Central Bank (ECB) via Frankfurter API",
                            RateType = cfg.RateType,
                            Decimals = cfg.Decimals,
                            Prefix = cfg.RatePrefix,
                            IsStale = false,
                            ErrorMessage = null
                        };

                        _cache[cacheKey] = (result, DateTime.UtcNow);
                        return result;
                    }
                }
            }
        }
        catch (Exception ex)
        {
            // If we have any cached version (even stale), return it
            if (_cache.TryGetValue(cacheKey, out var staleCached))
            {
                var stale = staleCached.Series;
                stale.IsStale = true;
                stale.ErrorMessage = $"Network fetch failed: {ex.Message}. Displaying cached ECB fix.";
                return stale;
            }

            // Otherwise, construct synthesized fallback based on latest rate
            var latest = await GetLatestRateAsync(normalizedPair, ct);
            var fallbackSeries = CreateFallbackSeries(cfg, normalizedPeriod, latest.Rate, ex.Message);
            _cache[cacheKey] = (fallbackSeries, DateTime.UtcNow);
            return fallbackSeries;
        }

        var fallbackLatest = await GetLatestRateAsync(normalizedPair, ct);
        return CreateFallbackSeries(cfg, normalizedPeriod, fallbackLatest.Rate, "No points returned from ECB Frankfurter endpoint");
    }

    /// <summary>
    /// Mathematically calculates cross or inverse rates from USD-denominated rates dictionary.
    /// Precision: Standard .NET decimal (28-29 significant digits).
    /// </summary>
    public static decimal? CalculateCrossOrInverseRate(FxPairConfig cfg, JsonElement rates)
    {
        // Case 1: Direct USD base (e.g. USD/IDR, USD/JPY)
        if (cfg.BaseCurrency.Equals("USD", StringComparison.OrdinalIgnoreCase))
        {
            if (rates.TryGetProperty(cfg.QuoteCurrency, out var quoteProp) && quoteProp.TryGetDecimal(out var quoteVal))
            {
                return quoteVal;
            }
            return null;
        }

        // Case 2: USD quote (e.g. EUR/USD, GBP/USD, AUD/USD) -> Inverse Rate
        // 1 USD = rates[Base] BaseCurrency => 1 BaseCurrency = (1 / rates[Base]) USD
        if (cfg.QuoteCurrency.Equals("USD", StringComparison.OrdinalIgnoreCase))
        {
            if (rates.TryGetProperty(cfg.BaseCurrency, out var baseProp) && baseProp.TryGetDecimal(out var baseVal) && baseVal > 0)
            {
                return 1.0m / baseVal;
            }
            return null;
        }

        // Case 3: Cross Rate (e.g. IDR/JPY)
        // 1 USD = rates[Base] Base, 1 USD = rates[Quote] Quote
        // => 1 Base = (rates[Quote] / rates[Base]) Quote
        if (rates.TryGetProperty(cfg.BaseCurrency, out var bProp) && bProp.TryGetDecimal(out var bVal) && bVal > 0 &&
            rates.TryGetProperty(cfg.QuoteCurrency, out var qProp) && qProp.TryGetDecimal(out var qVal))
        {
            return qVal / bVal;
        }

        return null;
    }

    private static FxHistoricalSeriesDto CreateFallbackSeries(FxPairConfig cfg, string period, decimal baseRate, string? error)
    {
        var points = new List<FxDataPointDto>();
        DateTime today = DateTime.UtcNow.Date;
        int count = period switch
        {
            "1D" => 2,
            "1W" => 7,
            "1M" => 30,
            "3M" => 90,
            "1Y" => 12,
            _ => 30
        };

        for (int i = count - 1; i >= 0; i--)
        {
            points.Add(new FxDataPointDto
            {
                Date = today.AddDays(-i),
                Rate = baseRate
            });
        }

        return new FxHistoricalSeriesDto
        {
            Pair = cfg.PairKey,
            Period = period,
            CurrentRate = baseRate,
            StartRate = baseRate,
            ChangePct = 0m,
            MinRate = baseRate,
            MaxRate = baseRate,
            Points = points,
            FetchedAtUtc = DateTime.UtcNow,
            Source = "ECB Reference Fix (Offline / Provider Error)",
            RateType = cfg.RateType,
            Decimals = cfg.Decimals,
            Prefix = cfg.RatePrefix,
            IsStale = true,
            ErrorMessage = error
        };
    }
}
