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
    private class MockLiquidityApiClient : TestMockApiClient
    {
        public override Task<ApiResult<ChartSymbolsResponseDto>> GetChartSymbolsAsync(CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<ChartSymbolsResponseDto>.Success(new ChartSymbolsResponseDto
            {
                Symbols = new List<string> { "BTCUSDT", "ETHUSDT", "SOLUSDT" },
                DefaultSymbol = "BTCUSDT",
                DefaultTimeframe = "1h",
                SupportedTimeframes = new List<string> { "15m", "1h", "4h", "1d" }
            }));
        }

        public override Task<ApiResult<LiquidityLayersResponseDto>> GetLiquidityLayersAsync(string symbol, string timeframe = "1h", CancellationToken ct = default)
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
                        new() { Side = "BID", Price = 94000m, DepthUsdt = 500000m, DistanceBps = 100m, IsPersistent = true, ConfirmationTier = "TRIPLE_CONFIRMED" }
                    },
                    BidsBuckets = new List<OrderBookBucketDto>
                    {
                        new() { BucketPrice = 94000m, TotalNotionalUsdt = 500000m, Side = "BID" }
                    },
                    AsksBuckets = new List<OrderBookBucketDto>
                    {
                        new() { BucketPrice = 96000m, TotalNotionalUsdt = 400000m, Side = "ASK" }
                    }
                }
            };
            return Task.FromResult(ApiResult<LiquidityLayersResponseDto>.Success(dto));
        }

        public override Task<ApiResult<List<KlineDto>>> GetKlinesAsync(string symbol, string timeframe = "1h", int limit = 100, CancellationToken ct = default)
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
        Assert.Single(vm.BidsBuckets);
        Assert.Single(vm.AsksBuckets);
        Assert.Contains("LIVE", vm.FeedProvenance);
    }

    [Fact]
    public void IndicatorAndWallToggles_FunctionCorrectly()
    {
        var client = new MockLiquidityApiClient();
        var vm = new LiquidityIntelViewModel(client);

        // Default state: all enabled
        Assert.True(vm.ShowEma20);
        Assert.True(vm.ShowEma50);
        Assert.True(vm.ShowEma100);
        Assert.True(vm.ShowEma200);
        Assert.True(vm.ShowStochastic);
        Assert.True(vm.ShowBidWalls);
        Assert.True(vm.ShowAskWalls);

        // Toggle each
        vm.ToggleEma20();
        Assert.False(vm.ShowEma20);

        vm.ToggleEma50();
        Assert.False(vm.ShowEma50);

        vm.ToggleEma100();
        Assert.False(vm.ShowEma100);

        vm.ToggleEma200();
        Assert.False(vm.ShowEma200);

        vm.ToggleStochastic();
        Assert.False(vm.ShowStochastic);

        vm.ToggleBidWalls();
        Assert.False(vm.ShowBidWalls);

        vm.ToggleAskWalls();
        Assert.False(vm.ShowAskWalls);
    }

    [Fact]
    public void SelectWall_UpdatesInspectionDetails()
    {
        var client = new MockLiquidityApiClient();
        var vm = new LiquidityIntelViewModel(client);

        var wall = new OrderBookWallDto
        {
            Side = "BID",
            Price = 80680.50m,
            DepthUsdt = 1500000m,
            DistanceBps = 12.5m,
            ConfirmationTier = "TRIPLE_CONFIRMED"
        };

        vm.SelectWall(wall);
        Assert.NotNull(vm.SelectedWall);
        Assert.Contains("[BID WALL SELECTED]", vm.SelectedWallDetails);
        Assert.Contains("80680.5000", vm.SelectedWallDetails);
        Assert.Contains("12.5 bps", vm.SelectedWallDetails);
        Assert.Contains("TRIPLE_CONFIRMED", vm.SelectedWallDetails);

        vm.SelectWall(null);
        Assert.Null(vm.SelectedWall);
        Assert.Equal("Select a liquidity wall from the table or chart to inspect order flow depth.", vm.SelectedWallDetails);
    }
}
