using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class MarketMonitorAlertsIntegrationTests
{
    private class MockMarketApiClient : IApiClient
    {
        public string BaseUrl => "http://127.0.0.1:8000";
        public void SetBaseUrl(string baseUrl) { }
        public Task<ApiResult<SystemStatusDto>> GetStatusAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<SystemStatusDto>.Success(new SystemStatusDto()));
        public Task<ApiResult<LiveSessionEnvelopeDto>> GetLiveSessionAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<LiveSessionEnvelopeDto>.Success(new LiveSessionEnvelopeDto()));
        public Task<ApiResult<JournalSummaryDto>> GetJournalSummaryAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<JournalSummaryDto>.Success(new JournalSummaryDto()));
        public Task<ApiResult<List<string>>> GetSymbolsAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<List<string>>.Success(new List<string>()));
        public Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<AutotraderScreeningLogResponseDto>.Success(new AutotraderScreeningLogResponseDto()));
        public Task<ApiResult<RetailPositioningResponseDto>> GetRetailPositioningAsync(string symbol = "BTCUSDT", CancellationToken ct = default) => Task.FromResult(ApiResult<RetailPositioningResponseDto>.Success(new RetailPositioningResponseDto()));
        public Task<ApiResult<JournalTradesResponseDto>> GetJournalTradesAsync(int limit = 50, CancellationToken ct = default) => Task.FromResult(ApiResult<JournalTradesResponseDto>.Success(new JournalTradesResponseDto()));
        public Task<ApiResult<ChartSymbolsResponseDto>> GetChartSymbolsAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<ChartSymbolsResponseDto>.Success(new ChartSymbolsResponseDto()));
        public Task<ApiResult<LiquidityLayersResponseDto>> GetLiquidityLayersAsync(string symbol, string timeframe = "1h", CancellationToken ct = default) => Task.FromResult(ApiResult<LiquidityLayersResponseDto>.Success(new LiquidityLayersResponseDto()));
        public Task<ApiResult<List<KlineDto>>> GetKlinesAsync(string symbol, string timeframe = "1h", int limit = 100, CancellationToken ct = default) => Task.FromResult(ApiResult<List<KlineDto>>.Success(new List<KlineDto>()));

        public Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(string? search = null, int page = 1, int pageSize = 25, CancellationToken ct = default)
        {
            var items = new List<MarketAssetTickerDto>
            {
                new() { NormalizedSymbol = "BTCUSDT", BenchmarkPrice = 95000m, TotalVolume24hUsdt = 50000000m, PriceChange24hPct = 2.5m },
                new() { NormalizedSymbol = "BZUSDT", BenchmarkPrice = 110m, TotalVolume24hUsdt = 12000000m, PriceChange24hPct = -1.2m },
                new() { NormalizedSymbol = "SOLUSDT", BenchmarkPrice = 210m, TotalVolume24hUsdt = 30000000m, PriceChange24hPct = 4.1m }
            };
            return Task.FromResult(ApiResult<ScreenerResponseDto>.Success(new ScreenerResponseDto
            {
                Items = items,
                TotalCount = 3,
                Page = 1,
                PageSize = 50,
                TotalPages = 1
            }));
        }

        public Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(CancellationToken ct = default)
        {
            var alerts = new List<AlertItemDto>
            {
                new() { AlertId = "ALT_1", Symbol = "BTCUSDT", TargetPrice = 96000m, Direction = "ABOVE", Status = "ACTIVE", IsActive = true },
                new() { AlertId = "ALT_2", Symbol = "BTCUSDT", TargetPrice = 94000m, Direction = "BELOW", Status = "ACTIVE", IsActive = true },
                new() { AlertId = "ALT_3", Symbol = "BZUSDT", TargetPrice = 108m, Direction = "BELOW", Status = "ACTIVE", IsActive = true }
            };
            return Task.FromResult(ApiResult<List<AlertItemDto>>.Success(alerts));
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
    public async Task LoadMarketMatrixAsync_PopulatesMarketsAndAlertsConcurrently()
    {
        var client = new MockMarketApiClient();
        var coordinator = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coordinator);

        await vm.LoadMarketMatrixAsync();

        Assert.Equal(3, vm.Markets.Count);
        Assert.Equal(3, vm.Alerts.Count);
        Assert.Equal("■ ALL ACTIVE ALERTS (3)", vm.AlertsHeaderTitle);
    }

    [Fact]
    public async Task SelectingMarket_FiltersAdjacentAlertsToSelectedSymbol()
    {
        var client = new MockMarketApiClient();
        var coordinator = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coordinator);

        await vm.LoadMarketMatrixAsync();

        // Select BTCUSDT
        vm.SelectedMarket = vm.Markets[0]; // BTCUSDT
        Assert.Equal(2, vm.Alerts.Count);
        Assert.All(vm.Alerts, a => Assert.Equal("BTCUSDT", a.Symbol));
        Assert.Equal("■ PRICE ALERTS // BTCUSDT (2)", vm.AlertsHeaderTitle);

        // Select BZUSDT
        vm.SelectedMarket = vm.Markets[1]; // BZUSDT
        Assert.Single(vm.Alerts);
        Assert.Equal("BZUSDT", vm.Alerts[0].Symbol);
        Assert.Equal("■ PRICE ALERTS // BZUSDT (1)", vm.AlertsHeaderTitle);
    }

    [Fact]
    public async Task SelectingSymbolWithNoAlerts_DisplaysExplicitEmptyNotice()
    {
        var client = new MockMarketApiClient();
        var coordinator = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coordinator);

        await vm.LoadMarketMatrixAsync();

        // Select SOLUSDT (has no alerts in mock)
        vm.SelectedMarket = vm.Markets[2]; // SOLUSDT
        Assert.Empty(vm.Alerts);
        Assert.Equal("NO ACTIVE ALERTS FOR SOLUSDT", vm.AlertsEmptyMessage);
        Assert.Equal("■ PRICE ALERTS // SOLUSDT (0)", vm.AlertsHeaderTitle);
    }

    [Fact]
    public async Task ToggleFilter_AllowsViewingAllAlertsEvenWithMarketSelected()
    {
        var client = new MockMarketApiClient();
        var coordinator = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coordinator);

        await vm.LoadMarketMatrixAsync();

        vm.SelectedMarket = vm.Markets[0]; // BTCUSDT
        Assert.Equal(2, vm.Alerts.Count);

        // Toggle filter off
        vm.ToggleAlertFilter();
        Assert.Equal(3, vm.Alerts.Count);
        Assert.Equal("■ ALL ACTIVE ALERTS (3)", vm.AlertsHeaderTitle);
    }
}
