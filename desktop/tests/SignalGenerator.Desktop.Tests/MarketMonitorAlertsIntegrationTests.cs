using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class MarketIntelligenceWorkspaceTests
{
    private class MockMarketApiClient : TestMockApiClient
    {
        public override Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(
            string? search = null,
            int page = 1,
            int pageSize = 25,
            string? exchanges = null,
            string? sortBy = "volume_desc",
            CancellationToken ct = default)
        {
            var items = new List<MarketAssetTickerDto>
            {
                new()
                {
                    NormalizedSymbol = "BTCUSDT",
                    BaseAsset = "BTC",
                    BenchmarkPrice = 95000m,
                    TotalVolume24hUsdt = 50000000m,
                    TotalOpenInterestUsdt = 35000000m,
                    PriceChange24hPct = 2.5m,
                    CrossListed = true,
                    BestSpreadBps = 0.5m,
                    Venues = new Dictionary<string, VenueDetailDto>
                    {
                        ["asterdex"] = new() { Exchange = "asterdex", ContractType = "PERPETUAL" },
                        ["binance"] = new() { Exchange = "binance", ContractType = "PERPETUAL" }
                    }
                },
                new()
                {
                    NormalizedSymbol = "BZUSDT",
                    BaseAsset = "BZ",
                    BenchmarkPrice = 110m,
                    TotalVolume24hUsdt = 12000000m,
                    TotalOpenInterestUsdt = 8000000m,
                    PriceChange24hPct = -1.2m,
                    CrossListed = false,
                    BestSpreadBps = 1.2m,
                    Venues = new Dictionary<string, VenueDetailDto>
                    {
                        ["asterdex"] = new() { Exchange = "asterdex", ContractType = "PERPETUAL" }
                    }
                },
                new()
                {
                    NormalizedSymbol = "SOLUSDT",
                    BaseAsset = "SOL",
                    BenchmarkPrice = 210m,
                    TotalVolume24hUsdt = 30000000m,
                    TotalOpenInterestUsdt = 20000000m,
                    PriceChange24hPct = 4.1m,
                    CrossListed = true,
                    BestSpreadBps = 0.8m,
                    Venues = new Dictionary<string, VenueDetailDto>
                    {
                        ["bybit"] = new() { Exchange = "bybit", ContractType = "PERPETUAL" },
                        ["hyperliquid"] = new() { Exchange = "hyperliquid", ContractType = "PERPETUAL" }
                    }
                }
            };

            return Task.FromResult(ApiResult<ScreenerResponseDto>.Success(new ScreenerResponseDto
            {
                Items = items,
                TotalCount = 3,
                Page = page,
                PageSize = pageSize,
                TotalPages = 2
            }));
        }
    }

    private class MockPollingCoordinator : IPollingCoordinator
    {
        public bool IsRunning => true;
        public int PollingIntervalSeconds => 5;
        public bool IsConnected => true;
        public DateTime? LastPollTimeUtc => DateTime.UtcNow;
        public string? LastError => null;
        public bool IsAutoPollingEnabled => true;

#pragma warning disable CS0067
        public event Action? OnTick;
        public event Action<SystemStatusDto>? OnStatusUpdated;
        public event Action<LiveSessionEnvelopeDto>? OnLiveSessionUpdated;
        public event Action<JournalSummaryDto>? OnJournalUpdated;
        public event Action<string>? OnPollingError;
        public event Action<bool>? OnConnectionStatusChanged;
#pragma warning restore CS0067

        public void Register(string key, Func<Task> task) { }
        public void Unregister(string key) { }
        public void Start() { }
        public void Stop() { }
        public void SetInterval(int seconds) { }
        public Task TriggerPollNowAsync() => Task.CompletedTask;
        public void Dispose() { }
    }

    [Fact]
    public async Task LoadMarketMatrixAsync_PopulatesAllInstrumentsAndComputesLeaders()
    {
        var client = new MockMarketApiClient();
        var coordinator = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coordinator);

        await vm.LoadMarketMatrixAsync();

        Assert.Equal(3, vm.Markets.Count);
        Assert.Equal("BTCUSDT", vm.HighestVolumeSymbol);
        Assert.Contains("50,000,000", vm.HighestVolumeText);
        Assert.Equal("SOLUSDT", vm.TopGainerSymbol);
        Assert.Equal("+4.10%", vm.TopGainerChange);
    }

    [Fact]
    public async Task SpotFilter_CorrectlyEvaluatesMetadata_AndShowsUnavailableMessage()
    {
        var client = new MockMarketApiClient();
        var coordinator = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coordinator);

        await vm.LoadMarketMatrixAsync();

        // Select SPOT
        vm.SelectMarketType("SPOT");

        // Connected venues only provide PERPETUAL, so SPOT must be flagged unavailable
        Assert.True(vm.IsSpotUnavailable);
        Assert.Empty(vm.Markets);
        Assert.Contains("SPOT MARKET DATA IS NOT PROVIDED", vm.SpotUnavailableMessage);

        // Switch back to PERPETUAL
        vm.SelectMarketType("PERPETUAL");
        Assert.False(vm.IsSpotUnavailable);
        Assert.Equal(3, vm.Markets.Count);
    }

    [Fact]
    public async Task VenueFilter_CorrectlyFiltersByConnectedExchange()
    {
        var client = new MockMarketApiClient();
        var coordinator = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coordinator);

        await vm.LoadMarketMatrixAsync();

        // Filter by bybit
        vm.SelectVenue("BYBIT");
        Assert.Single(vm.Markets);
        Assert.Equal("SOLUSDT", vm.Markets[0].NormalizedSymbol);

        // Filter by asterdex
        vm.SelectVenue("ASTERDEX");
        Assert.Equal(2, vm.Markets.Count);
        Assert.Contains(vm.Markets, m => m.NormalizedSymbol == "BTCUSDT");
        Assert.Contains(vm.Markets, m => m.NormalizedSymbol == "BZUSDT");

        // Reset to ALL
        vm.SelectVenue("ALL");
        Assert.Equal(3, vm.Markets.Count);
    }

    [Fact]
    public async Task SearchFilter_FiltersByTickerOrBaseAsset()
    {
        var client = new MockMarketApiClient();
        var coordinator = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coordinator);

        await vm.LoadMarketMatrixAsync();

        vm.SearchFilter = "BZ";
        Assert.Single(vm.Markets);
        Assert.Equal("BZUSDT", vm.Markets[0].NormalizedSymbol);

        vm.SearchFilter = "SOL";
        Assert.Single(vm.Markets);
        Assert.Equal("SOLUSDT", vm.Markets[0].NormalizedSymbol);

        vm.SearchFilter = "NONEXISTENT";
        Assert.Empty(vm.Markets);
        Assert.Contains("NONEXISTENT", vm.EmptyMessage);
    }

    [Fact]
    public async Task WorkspaceMode_SwitchesBetweenMonitorAndScreener()
    {
        var client = new MockMarketApiClient();
        var coordinator = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coordinator);

        Assert.True(vm.IsMonitorMode);
        Assert.False(vm.IsScreenerMode);

        vm.SetScreenerMode();
        Assert.False(vm.IsMonitorMode);
        Assert.True(vm.IsScreenerMode);

        vm.SetMonitorMode();
        Assert.True(vm.IsMonitorMode);
        Assert.False(vm.IsScreenerMode);
    }

    [Fact]
    public async Task Sorting_OrdersBySelectedFieldCorrectly()
    {
        var client = new MockMarketApiClient();
        var coordinator = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coordinator);

        await vm.LoadMarketMatrixAsync();

        // Sort by change desc
        vm.SetSort("change_desc");
        Assert.Equal("SOLUSDT", vm.Markets[0].NormalizedSymbol); // +4.1%
        Assert.Equal("BZUSDT", vm.Markets[2].NormalizedSymbol);  // -1.2%

        // Sort by price desc
        vm.SetSort("price_desc");
        Assert.Equal("BTCUSDT", vm.Markets[0].NormalizedSymbol); // 95000
    }
}
