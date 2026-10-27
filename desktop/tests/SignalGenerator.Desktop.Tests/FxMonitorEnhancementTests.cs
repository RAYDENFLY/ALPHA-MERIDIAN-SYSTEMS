using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Avalonia;
using SignalGenerator.Desktop.Controls;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class FxMonitorEnhancementTests
{
    [Fact]
    public void DefaultPairs_ContainsAllMandatedPairs_WithAccurateConfigurations()
    {
        var pairs = FxPairConfig.DefaultPairs;
        Assert.NotNull(pairs);
        Assert.Equal(6, pairs.Count);

        var usdIdr = FxPairConfig.GetConfig("USD/IDR");
        Assert.Equal("USD", usdIdr.BaseCurrency);
        Assert.Equal("IDR", usdIdr.QuoteCurrency);
        Assert.Equal("Rp ", usdIdr.RatePrefix);
        Assert.Equal(2, usdIdr.Decimals);
        Assert.Equal(18139m, usdIdr.FallbackRate);

        var usdJpy = FxPairConfig.GetConfig("USD/JPY");
        Assert.Equal("USD", usdJpy.BaseCurrency);
        Assert.Equal("JPY", usdJpy.QuoteCurrency);
        Assert.Equal("¥ ", usdJpy.RatePrefix);
        Assert.Equal(2, usdJpy.Decimals);
        Assert.Equal(157.82m, usdJpy.FallbackRate);

        var idrJpy = FxPairConfig.GetConfig("IDR/JPY");
        Assert.Equal("IDR", idrJpy.BaseCurrency);
        Assert.Equal("JPY", idrJpy.QuoteCurrency);
        Assert.Equal("¥ ", idrJpy.RatePrefix);
        Assert.Equal(6, idrJpy.Decimals);
        Assert.Equal(0.008701m, idrJpy.FallbackRate);

        var eurUsd = FxPairConfig.GetConfig("EUR/USD");
        Assert.Equal("EUR", eurUsd.BaseCurrency);
        Assert.Equal("USD", eurUsd.QuoteCurrency);
        Assert.Equal("$ ", eurUsd.RatePrefix);
        Assert.Equal(4, eurUsd.Decimals);
        Assert.Equal(1.1150m, eurUsd.FallbackRate);

        var gbpUsd = FxPairConfig.GetConfig("GBP/USD");
        Assert.Equal("GBP", gbpUsd.BaseCurrency);
        Assert.Equal("USD", gbpUsd.QuoteCurrency);
        Assert.Equal("$ ", gbpUsd.RatePrefix);
        Assert.Equal(4, gbpUsd.Decimals);

        var audUsd = FxPairConfig.GetConfig("AUD/USD");
        Assert.Equal("AUD", audUsd.BaseCurrency);
        Assert.Equal("USD", audUsd.QuoteCurrency);
        Assert.Equal("$ ", audUsd.RatePrefix);
        Assert.Equal(4, audUsd.Decimals);
    }

    [Fact]
    public void InverseRatePrecision_Calculation_IsMathematicallyExact()
    {
        // Test EUR/USD inverse from USD/EUR:
        // If 1 USD = 0.8968609865 EUR, then 1 EUR = 1 / 0.8968609865 USD ≈ 1.115000...
        var eurCfg = FxPairConfig.GetConfig("EUR/USD");
        var jsonDoc = JsonDocument.Parse("{\"rates\":{\"EUR\":0.8968609865}}");
        var calculated = FxRateService.CalculateCrossOrInverseRate(eurCfg, jsonDoc.RootElement.GetProperty("rates"));

        Assert.NotNull(calculated);
        decimal expected = 1.0m / 0.8968609865m;
        Assert.Equal(expected, calculated.Value);
        Assert.True(Math.Abs(calculated.Value - 1.1150m) < 0.001m);

        // Test GBP/USD inverse from USD/GBP:
        var gbpCfg = FxPairConfig.GetConfig("GBP/USD");
        var gbpDoc = JsonDocument.Parse("{\"rates\":{\"GBP\":0.7503}}");
        var gbpCalculated = FxRateService.CalculateCrossOrInverseRate(gbpCfg, gbpDoc.RootElement.GetProperty("rates"));

        Assert.NotNull(gbpCalculated);
        decimal expectedGbp = 1.0m / 0.7503m;
        Assert.Equal(expectedGbp, gbpCalculated.Value);
        Assert.True(Math.Abs(gbpCalculated.Value - 1.3328m) < 0.001m);
    }

    [Fact]
    public void CrossRatePrecision_IDRJPY_Calculation_IsMathematicallyExact()
    {
        // 1 USD = 18139 IDR, 1 USD = 157.82 JPY
        // => 1 IDR = 157.82 / 18139 JPY ≈ 0.008700589889... JPY
        var idrJpyCfg = FxPairConfig.GetConfig("IDR/JPY");
        var jsonDoc = JsonDocument.Parse("{\"rates\":{\"IDR\":18139.0,\"JPY\":157.82}}");
        var calculated = FxRateService.CalculateCrossOrInverseRate(idrJpyCfg, jsonDoc.RootElement.GetProperty("rates"));

        Assert.NotNull(calculated);
        decimal expected = 157.82m / 18139.0m;
        Assert.Equal(expected, calculated.Value);
        Assert.True(Math.Abs(calculated.Value - 0.0087006m) < 0.000001m);
    }

    [Theory]
    [InlineData(45.0, 45.0, 300.0, 10, 0)]   // At left edge -> index 0
    [InlineData(345.0, 45.0, 300.0, 10, 9)]  // At right edge -> index 9
    [InlineData(195.0, 45.0, 300.0, 10, 5)]  // Midpoint -> index 5
    [InlineData(10.0, 45.0, 300.0, 10, 0)]   // Beyond left -> clamped to 0
    [InlineData(500.0, 45.0, 300.0, 10, 9)]  // Beyond right -> clamped to 9
    [InlineData(100.0, 45.0, 300.0, 1, 0)]   // Single point -> index 0
    [InlineData(100.0, 45.0, 300.0, 0, -1)]  // Empty points -> -1
    public void NearestObservationLookup_SnapsCorrectlyWithoutInterpolation(double mouseX, double padLeft, double plotW, int pointCount, int expectedIndex)
    {
        int actual = FxLineChart.FindNearestObservationIndex(mouseX, padLeft, plotW, pointCount);
        Assert.Equal(expectedIndex, actual);
    }

    [Fact]
    public void TooltipPositioning_ZeroClipping_WithinContainerBounds()
    {
        var container = new Size(400, 200);
        var tooltipSize = new Size(120, 50);

        // Case 1: Center anchor -> appears to the right of anchor
        var anchorCenter = new Point(150, 100);
        var rectCenter = FxLineChart.CalculateTooltipBounds(anchorCenter, tooltipSize, container, margin: 8);
        Assert.True(rectCenter.X >= 8 && rectCenter.Right <= 400 - 8);
        Assert.True(rectCenter.Y >= 8 && rectCenter.Bottom <= 200 - 8);
        Assert.Equal(162, rectCenter.X); // 150 + 12

        // Case 2: Near right edge -> flips to the left of anchor to prevent clipping
        var anchorRight = new Point(350, 100);
        var rectRight = FxLineChart.CalculateTooltipBounds(anchorRight, tooltipSize, container, margin: 8);
        Assert.True(rectRight.X >= 8);
        Assert.True(rectRight.Right <= 400 - 8);
        Assert.True(rectRight.X < anchorRight.X); // Flipped to left!

        // Case 3: Near top edge -> clamped so Y >= margin
        var anchorTop = new Point(150, 5);
        var rectTop = FxLineChart.CalculateTooltipBounds(anchorTop, tooltipSize, container, margin: 8);
        Assert.Equal(8, rectTop.Y);
        Assert.True(rectTop.Bottom <= 200 - 8);

        // Case 4: Near bottom edge -> clamped so Bottom <= Height - margin
        var anchorBottom = new Point(150, 195);
        var rectBottom = FxLineChart.CalculateTooltipBounds(anchorBottom, tooltipSize, container, margin: 8);
        Assert.True(rectBottom.Y >= 8);
        Assert.Equal(200 - 8 - 50, rectBottom.Y);
    }

    [Fact]
    public void FxDataPointDto_IsOhlc_DetectedCorrectly()
    {
        var dailyFixPoint = new FxDataPointDto
        {
            Date = DateTime.UtcNow.Date,
            Rate = 18139m
        };
        Assert.False(dailyFixPoint.IsOhlc);

        var candlePoint = new FxDataPointDto
        {
            Date = DateTime.UtcNow.Date,
            Rate = 18139m,
            Open = 18100m,
            High = 18200m,
            Low = 18050m,
            Close = 18139m
        };
        Assert.True(candlePoint.IsOhlc);
    }

    [Fact]
    public async Task OverviewViewModel_PairSelection_UpdatesTitleAndSeries()
    {
        var coord = new MockPollingCoordinator();
        var client = new MockApiClient();
        var fxService = new TestMultiPairFxService();
        var newsService = new MockRssNewsService();

        var vm = new OverviewViewModel(coord, client, fxService, newsService);

        // Initial default is USD/IDR
        Assert.Equal("USD/IDR", vm.SelectedFxPair);
        Assert.Equal("■ USD/IDR FX MONITOR", vm.FxMonitorTitle);

        // Select EUR/USD
        await vm.SelectFxPairAsync("EUR/USD");
        Assert.Equal("EUR/USD", vm.SelectedFxPair);
        Assert.Equal("■ EUR/USD FX MONITOR", vm.FxMonitorTitle);
        Assert.NotNull(vm.FxSeries);
        Assert.Equal("EUR/USD", vm.FxSeries.Pair);
        Assert.Equal(1.1150m, vm.FxSeries.CurrentRate);
        Assert.Equal("$ 1.1150", vm.FxSeries.FormattedCurrentRate);

        // Select IDR/JPY
        await vm.SelectFxPairAsync("IDR/JPY");
        Assert.Equal("IDR/JPY", vm.SelectedFxPair);
        Assert.Equal("■ IDR/JPY FX MONITOR", vm.FxMonitorTitle);
        Assert.NotNull(vm.FxSeries);
        Assert.Equal("IDR/JPY", vm.FxSeries.Pair);
        Assert.Equal(6, vm.FxSeries.Decimals);
        Assert.Equal("¥ 0.008701", vm.FxSeries.FormattedCurrentRate);
    }

    private class TestMultiPairFxService : IFxRateService
    {
        public Task<FxRateSnapshotDto> GetLatestRateAsync(CancellationToken ct = default)
            => GetLatestRateAsync("USD/IDR", ct);

        public Task<FxRateSnapshotDto> GetLatestRateAsync(string pair, CancellationToken ct = default)
        {
            var cfg = FxPairConfig.GetConfig(pair);
            return Task.FromResult(new FxRateSnapshotDto
            {
                Pair = cfg.PairKey,
                Rate = cfg.FallbackRate,
                Date = DateTime.UtcNow.Date,
                Source = "European Central Bank (ECB) via Frankfurter API"
            });
        }

        public Task<FxHistoricalSeriesDto> GetHistoricalSeriesAsync(string period, CancellationToken ct = default)
            => GetHistoricalSeriesAsync("USD/IDR", period, ct);

        public Task<FxHistoricalSeriesDto> GetHistoricalSeriesAsync(string pair, string period, CancellationToken ct = default)
        {
            var cfg = FxPairConfig.GetConfig(pair);
            return Task.FromResult(new FxHistoricalSeriesDto
            {
                Pair = cfg.PairKey,
                Period = period,
                CurrentRate = cfg.FallbackRate,
                StartRate = cfg.FallbackRate,
                ChangePct = 0.5m,
                MinRate = cfg.FallbackRate * 0.98m,
                MaxRate = cfg.FallbackRate * 1.02m,
                Decimals = cfg.Decimals,
                Prefix = cfg.RatePrefix,
                RateType = cfg.RateType,
                Points = new List<FxDataPointDto>
                {
                    new() { Date = DateTime.UtcNow.Date.AddDays(-2), Rate = cfg.FallbackRate * 0.99m },
                    new() { Date = DateTime.UtcNow.Date, Rate = cfg.FallbackRate }
                }
            });
        }

        public IReadOnlyList<FxPairConfig> GetSupportedPairs() => FxPairConfig.DefaultPairs;
    }

    private class MockPollingCoordinator : IPollingCoordinator
    {
#pragma warning disable CS0067
        public bool IsConnected => true;
        public DateTime? LastPollTimeUtc => DateTime.UtcNow;
        public string? LastError => null;
        public bool IsAutoPollingEnabled => true;

        public event Action<SystemStatusDto>? OnStatusUpdated;
        public event Action<LiveSessionEnvelopeDto>? OnLiveSessionUpdated;
        public event Action<JournalSummaryDto>? OnJournalUpdated;
        public event Action<string>? OnPollingError;
        public event Action<bool>? OnConnectionStatusChanged;
#pragma warning restore CS0067

        public void Start() { }
        public void Stop() { }
        public Task TriggerPollNowAsync() => Task.CompletedTask;
        public void SetInterval(int seconds) { }
        public void Dispose() { }
    }

    private class MockApiClient : TestMockApiClient
    {
    }

    private class MockRssNewsService : IRssNewsService
    {
        public INewsStorageService Storage { get; } = null!;
        public IReadOnlyList<FeedSourceStatusDto> SourceStatuses { get; } = new List<FeedSourceStatusDto>();

        public Task<List<NewsArticleDto>> FetchLatestNewsAsync(CancellationToken ct = default) =>
            Task.FromResult(new List<NewsArticleDto>());

        public Task<List<NewsArticleDto>> FilterNewsAsync(string? category = null, string? searchQuery = null, DateTime? startDateUtc = null, DateTime? endDateUtc = null, string? publisher = null, CancellationToken ct = default) =>
            Task.FromResult(new List<NewsArticleDto>());

        public Task<NewsArticleDto?> SummarizeArticleAsync(string articleId, CancellationToken ct = default) =>
            Task.FromResult<NewsArticleDto?>(null);
    }
}
