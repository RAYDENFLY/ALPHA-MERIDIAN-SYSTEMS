using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class LiquidityIntelViewModelTests
{
    private class MockLiquidityApiClient : IApiClient
    {
        public string BaseUrl => "http://127.0.0.1:8000";
        public void SetBaseUrl(string baseUrl) { }
        public Task<ApiResult<SystemStatusDto>> GetStatusAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<SystemStatusDto>.Success(new SystemStatusDto()));
        public Task<ApiResult<LiveSessionEnvelopeDto>> GetLiveSessionAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<LiveSessionEnvelopeDto>.Success(new LiveSessionEnvelopeDto()));
        public Task<ApiResult<JournalSummaryDto>> GetJournalSummaryAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<JournalSummaryDto>.Success(new JournalSummaryDto()));
        public Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(string? search = null, int page = 1, int pageSize = 25, CancellationToken ct = default) => Task.FromResult(ApiResult<ScreenerResponseDto>.Success(new ScreenerResponseDto()));
        public Task<ApiResult<List<string>>> GetSymbolsAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<List<string>>.Success(new List<string>()));
        public Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<AutotraderScreeningLogResponseDto>.Success(new AutotraderScreeningLogResponseDto()));
        public Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<List<AlertItemDto>>.Success(new List<AlertItemDto>()));
        public Task<ApiResult<RetailPositioningResponseDto>> GetRetailPositioningAsync(string symbol = "BTCUSDT", CancellationToken ct = default) => Task.FromResult(ApiResult<RetailPositioningResponseDto>.Success(new RetailPositioningResponseDto()));
        public Task<ApiResult<JournalTradesResponseDto>> GetJournalTradesAsync(int limit = 50, CancellationToken ct = default) => Task.FromResult(ApiResult<JournalTradesResponseDto>.Success(new JournalTradesResponseDto()));

        public Task<ApiResult<ChartSymbolsResponseDto>> GetChartSymbolsAsync(CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<ChartSymbolsResponseDto>.Success(new ChartSymbolsResponseDto
            {
                Symbols = new List<string> { "BTCUSDT", "ETHUSDT", "SOLUSDT" },
                DefaultSymbol = "BTCUSDT",
                DefaultTimeframe = "1h",
                SupportedTimeframes = new List<string> { "15m", "1h", "4h", "1d" }
            }));
        }

        public Task<ApiResult<LiquidityLayersResponseDto>> GetLiquidityLayersAsync(string symbol, string timeframe = "1h", CancellationToken ct = default)
        {
            var dto = new LiquidityLayersResponseDto
            {
                Symbol = symbol,
                Timeframe = timeframe,
                FeedStatus = new FeedStatusDto
                {
                    Source = "AsterDEX Klines & Combined L2 Depth",
                    Status = "LIVE",
                    IsStale = false,
                    OrderBookStatus = "AVAILABLE",
                    OrderBookAgeSeconds = 0.5,
                    CandleAgeSeconds = 20.0,
                    CandleCount = 2
                },
                Candles = new List<KlineDto>
                {
                    new() { Time = 1700000000, Open = 95000m, High = 95500m, Low = 94800m, Close = 95200m, Volume = 50m, TimestampMs = 1700000000000 },
                    new() { Time = 1700003600, Open = 95200m, High = 95800m, Low = 95100m, Close = 95600m, Volume = 65m, TimestampMs = 1700003600000 }
                },
                StructuralLevels = new List<StructuralLevelDto>
                {
                    new() { LevelId = "LVL_1", LevelType = "SUPPORT", Price = 94500m, Timeframe = "1h", Status = "ACTIVE" }
                },
                SweepEvents = new List<SweepEventDto>
                {
                    new() { EventId = "SWP_1", Direction = "LONG", SweepPrice = 94450m, ReclaimPrice = null, SweepTimestampMs = 1700003600000, SweepDepthUsdt = 120m }
                },
                OrderBookOverlay = new OrderBookOverlayDto
                {
                    Status = "AVAILABLE",
                    SpreadBps = 0.5m,
                    TotalBidDepthUsdt = 1000000m,
                    TotalAskDepthUsdt = 1200000m,
                    AgeSeconds = 0.4,
                    Walls = new List<OrderBookWallDto>
                    {
                        new() { Side = "BID", Price = 94000m, DepthUsdt = 500000m, DistanceBps = 100m, IsPersistent = true }
                    }
                }
            };
            return Task.FromResult(ApiResult<LiquidityLayersResponseDto>.Success(dto));
        }

        public Task<ApiResult<List<KlineDto>>> GetKlinesAsync(string symbol, string timeframe = "1h", int limit = 100, CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<List<KlineDto>>.Success(new List<KlineDto>()));
        }
    }

    [Fact]
    public async Task RefreshIntelAsync_PopulatesCandlesLevelsAndWallsWithLiveStatus()
    {
        var client = new MockLiquidityApiClient();
        var vm = new LiquidityIntelViewModel(client);

        await vm.RefreshIntelAsync();

        Assert.Equal("LIVE", vm.Status);
        Assert.Equal("#00E676", vm.StatusColor);
        Assert.Equal(2, vm.Candles.Count);
        Assert.Single(vm.StructuralLevels);
        Assert.Single(vm.SweepEvents);
        Assert.Single(vm.OrderBookWalls);
        Assert.Contains("LIVE", vm.FeedProvenance);
    }
}
