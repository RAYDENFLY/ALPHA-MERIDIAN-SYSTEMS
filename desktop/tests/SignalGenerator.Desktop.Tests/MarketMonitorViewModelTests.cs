using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class MarketMonitorViewModelTests
{
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

    private class MockApiClient : IApiClient
    {
        public string BaseUrl => "http://127.0.0.1:8000";
        public void SetBaseUrl(string baseUrl) { }
        public Task<ApiResult<SystemStatusDto>> GetStatusAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<SystemStatusDto>.Success(new SystemStatusDto()));
        public Task<ApiResult<LiveSessionEnvelopeDto>> GetLiveSessionAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<LiveSessionEnvelopeDto>.Success(new LiveSessionEnvelopeDto()));
        public Task<ApiResult<JournalSummaryDto>> GetJournalSummaryAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<JournalSummaryDto>.Success(new JournalSummaryDto()));
        public Task<ApiResult<List<string>>> GetSymbolsAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<List<string>>.Success(new List<string>()));
        public Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<AutotraderScreeningLogResponseDto>.Success(new AutotraderScreeningLogResponseDto()));
        public Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<List<AlertItemDto>>.Success(new List<AlertItemDto>()));
        public Task<ApiResult<RetailPositioningResponseDto>> GetRetailPositioningAsync(string symbol = "BTCUSDT", CancellationToken ct = default) => Task.FromResult(ApiResult<RetailPositioningResponseDto>.Success(new RetailPositioningResponseDto()));

        public Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(string? search = null, int page = 1, int pageSize = 25, CancellationToken ct = default)
        {
            var items = new List<MarketAssetTickerDto>
            {
                new MarketAssetTickerDto
                {
                    NormalizedSymbol = "BTCUSDT",
                    BenchmarkPrice = 67500.0m,
                    TotalVolume24hUsdt = 150000000.0m,
                    PriceChange24hPct = 2.45m,
                    VenueCount = 3
                },
                new MarketAssetTickerDto
                {
                    NormalizedSymbol = "SOLUSDT",
                    BenchmarkPrice = 180.50m,
                    TotalVolume24hUsdt = 45000000.0m,
                    PriceChange24hPct = 5.80m,
                    VenueCount = 3
                }
            };

            return Task.FromResult(ApiResult<ScreenerResponseDto>.Success(new ScreenerResponseDto
            {
                Items = items,
                TotalCount = 2,
                Page = 1,
                PageSize = 25,
                TotalPages = 1
            }));
        }
    }

    [Fact]
    public async Task MarketMonitorViewModel_LoadsMatrixAndComputesLeaders()
    {
        var client = new MockApiClient();
        var coord = new MockPollingCoordinator();
        var vm = new MarketMonitorViewModel(client, coord);

        await vm.LoadMarketMatrixAsync();

        Assert.Equal(2, vm.Markets.Count);
        Assert.Equal(2, vm.TotalMarketsCount);
        Assert.Equal("BTCUSDT", vm.HighestVolumeSymbol);
        Assert.Contains("150,000,000", vm.HighestVolumeText);
        Assert.Equal("SOLUSDT", vm.TopGainerSymbol);
        Assert.Equal("+5.80%", vm.TopGainerChange);
    }
}
