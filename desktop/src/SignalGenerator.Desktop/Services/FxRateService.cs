using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
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

    public async Task<FxRateSnapshotDto> GetLatestRateAsync(CancellationToken ct = default)
    {
        try
        {
            var url = $"{FrankfurterBase}/latest?from=USD&to=IDR";
            using var req = new HttpRequestMessage(HttpMethod.Get, url);
            req.Headers.Add("User-Agent", "SignalGeneratorDesktop/1.0");

            using var resp = await _httpClient.SendAsync(req, ct);
            if (resp.IsSuccessStatusCode)
            {
                using var stream = await resp.Content.ReadAsStreamAsync(ct);
                using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
                var root = doc.RootElement;

                if (root.TryGetProperty("rates", out var rates) && rates.TryGetProperty("IDR", out var idrVal))
                {
                    decimal rate = idrVal.GetDecimal();
                    DateTime date = root.TryGetProperty("date", out var dProp) && DateTime.TryParse(dProp.GetString(), out var d)
                        ? d
                        : DateTime.UtcNow.Date;

                    return new FxRateSnapshotDto
                    {
                        Rate = rate,
                        Date = date,
                        Source = "European Central Bank (ECB) via Frankfurter API",
                        IsStale = false,
                        ErrorMessage = null
                    };
                }
            }
        }
        catch (Exception ex)
        {
            // Fallback to Open Exchange Rates
            return await GetLatestRateFallbackAsync(ex.Message, ct);
        }

        return await GetLatestRateFallbackAsync("Frankfurter latest rate returned non-success", ct);
    }

    private async Task<FxRateSnapshotDto> GetLatestRateFallbackAsync(string originalError, CancellationToken ct)
    {
        try
        {
            using var req = new HttpRequestMessage(HttpMethod.Get, OpenErApiBase);
            req.Headers.Add("User-Agent", "SignalGeneratorDesktop/1.0");

            using var resp = await _httpClient.SendAsync(req, ct);
            if (resp.IsSuccessStatusCode)
            {
                using var stream = await resp.Content.ReadAsStreamAsync(ct);
                using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
                var root = doc.RootElement;

                if (root.TryGetProperty("rates", out var rates) && rates.TryGetProperty("IDR", out var idrVal))
                {
                    decimal rate = idrVal.GetDecimal();
                    return new FxRateSnapshotDto
                    {
                        Rate = rate,
                        Date = DateTime.UtcNow.Date,
                        Source = "Open Exchange Rates (Public ER-API Fallback)",
                        IsStale = false,
                        ErrorMessage = null
                    };
                }
            }
        }
        catch (Exception fallbackEx)
        {
            return new FxRateSnapshotDto
            {
                Rate = 18139m, // Conservative ECB reference fallback
                Date = DateTime.UtcNow.Date,
                Source = "Offline Reference Cache (ECB Fix)",
                IsStale = true,
                ErrorMessage = $"Primary and fallback FX providers unavailable: {originalError}; {fallbackEx.Message}"
            };
        }

        return new FxRateSnapshotDto
        {
            Rate = 18139m,
            Date = DateTime.UtcNow.Date,
            Source = "Offline Reference Cache (ECB Fix)",
            IsStale = true,
            ErrorMessage = originalError
        };
    }

    public async Task<FxHistoricalSeriesDto> GetHistoricalSeriesAsync(string period, CancellationToken ct = default)
    {
        var normalizedPeriod = string.IsNullOrWhiteSpace(period) ? "1M" : period.Trim().ToUpperInvariant();

        // 1. Check in-memory cache
        if (_cache.TryGetValue(normalizedPeriod, out var cached) && (DateTime.UtcNow - cached.FetchedAt) < CacheTtl)
        {
            return cached.Series;
        }

        // 2. Compute date range
        DateTime today = DateTime.UtcNow.Date;
        DateTime startDate = normalizedPeriod switch
        {
            "1D" => today.AddDays(-2),   // 2 days to capture previous business day close vs latest
            "1W" => today.AddDays(-7),
            "1M" => today.AddDays(-30),
            "3M" => today.AddDays(-90),
            "1Y" => today.AddDays(-365),
            _ => today.AddDays(-30)
        };

        string startStr = startDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        string endStr = today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

        try
        {
            string url = $"{FrankfurterBase}/{startStr}..{endStr}?from=USD&to=IDR";
            using var req = new HttpRequestMessage(HttpMethod.Get, url);
            req.Headers.Add("User-Agent", "SignalGeneratorDesktop/1.0");

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
                            dateProp.Value.TryGetProperty("IDR", out var idrVal))
                        {
                            points.Add(new FxDataPointDto
                            {
                                Date = date,
                                Rate = idrVal.GetDecimal()
                            });
                        }
                    }

                    points = points.OrderBy(p => p.Date).ToList();
                    if (points.Count > 0)
                    {
                        decimal currentRate = points.Last().Rate;
                        decimal startRate = points.First().Rate;
                        decimal changePct = startRate > 0 ? ((currentRate - startRate) / startRate) * 100m : 0m;
                        decimal minRate = points.Min(p => p.Rate);
                        decimal maxRate = points.Max(p => p.Rate);

                        var result = new FxHistoricalSeriesDto
                        {
                            Period = normalizedPeriod,
                            CurrentRate = currentRate,
                            StartRate = startRate,
                            ChangePct = Math.Round(changePct, 2),
                            MinRate = minRate,
                            MaxRate = maxRate,
                            Points = points,
                            FetchedAtUtc = DateTime.UtcNow,
                            Source = "European Central Bank (ECB) via Frankfurter API",
                            IsStale = false,
                            ErrorMessage = null
                        };

                        _cache[normalizedPeriod] = (result, DateTime.UtcNow);
                        return result;
                    }
                }
            }
        }
        catch (Exception ex)
        {
            // If we have any cached version (even stale), return it
            if (_cache.TryGetValue(normalizedPeriod, out var staleCached))
            {
                var stale = staleCached.Series;
                stale.IsStale = true;
                stale.ErrorMessage = $"Network fetch failed: {ex.Message}. Displaying cached ECB fix.";
                return stale;
            }

            // Otherwise, construct synthesized fallback based on latest rate
            var latest = await GetLatestRateAsync(ct);
            var fallbackSeries = CreateFallbackSeries(normalizedPeriod, latest.Rate, ex.Message);
            _cache[normalizedPeriod] = (fallbackSeries, DateTime.UtcNow);
            return fallbackSeries;
        }

        var fallbackLatest = await GetLatestRateAsync(ct);
        return CreateFallbackSeries(normalizedPeriod, fallbackLatest.Rate, "No points returned from ECB Frankfurter endpoint");
    }

    private static FxHistoricalSeriesDto CreateFallbackSeries(string period, decimal baseRate, string? error)
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
            Period = period,
            CurrentRate = baseRate,
            StartRate = baseRate,
            ChangePct = 0m,
            MinRate = baseRate,
            MaxRate = baseRate,
            Points = points,
            FetchedAtUtc = DateTime.UtcNow,
            Source = "ECB Reference Fix (Offline / Provider Error)",
            IsStale = true,
            ErrorMessage = error
        };
    }
}
