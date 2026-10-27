using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class OverviewViewModelTests
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

        public void TriggerStatus(SystemStatusDto status) => OnStatusUpdated?.Invoke(status);
        public void TriggerSession(LiveSessionEnvelopeDto session) => OnLiveSessionUpdated?.Invoke(session);

        public void Start() { }
        public void Stop() { }
        public Task TriggerPollNowAsync() => Task.CompletedTask;
        public void SetInterval(int seconds) { }
        public void Dispose() { }
    }

    private class MockApiClient : TestMockApiClient
    {
        public override Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(
            string? search = null,
            int page = 1,
            int pageSize = 25,
            string? exchanges = null,
            string? sortBy = "volume_desc",
            CancellationToken ct = default) =>
            Task.FromResult(ApiResult<ScreenerResponseDto>.Success(new ScreenerResponseDto
            {
                Items = new List<MarketAssetTickerDto>
                {
                    new() { NormalizedSymbol = "BTCUSDT", BenchmarkPrice = 80430m, PriceChange24hPct = 0.12m, TotalVolume24hUsdt = 12000000000m, TotalOpenInterestUsdt = 8000000000m, CrossListed = true },
                    new() { NormalizedSymbol = "ETHUSDT", BenchmarkPrice = 2345m, PriceChange24hPct = 0.82m, TotalVolume24hUsdt = 8500000000m, TotalOpenInterestUsdt = 4500000000m, CrossListed = true },
                    new() { NormalizedSymbol = "SOLUSDT", BenchmarkPrice = 101m, PriceChange24hPct = -0.63m, TotalVolume24hUsdt = 38000000m, TotalOpenInterestUsdt = 35000000m, CrossListed = true }
                }
            }));
    }

    private class MockFxRateService : IFxRateService
    {
        public Task<FxRateSnapshotDto> GetLatestRateAsync(CancellationToken ct = default) =>
            Task.FromResult(new FxRateSnapshotDto { Rate = 18139m, Date = DateTime.UtcNow.Date, Source = "European Central Bank (ECB) via Frankfurter API" });

        public Task<FxHistoricalSeriesDto> GetHistoricalSeriesAsync(string period, CancellationToken ct = default) =>
            Task.FromResult(new FxHistoricalSeriesDto
            {
                Period = period,
                CurrentRate = 18139m,
                StartRate = 18000m,
                ChangePct = 0.77m,
                MinRate = 17950m,
                MaxRate = 18200m,
                Source = "European Central Bank (ECB) via Frankfurter API",
                Points = new List<FxDataPointDto>
                {
                    new() { Date = DateTime.UtcNow.Date.AddDays(-30), Rate = 18000m },
                    new() { Date = DateTime.UtcNow.Date, Rate = 18139m }
                }
            });
    }

    private class MockRssNewsService : IRssNewsService
    {
        public INewsStorageService Storage { get; } = null!;

        public Task<List<NewsArticleDto>> FetchLatestNewsAsync(CancellationToken ct = default) =>
            Task.FromResult(new List<NewsArticleDto>
            {
                new() { Title = "Bitcoin Stabilizes Near 80k As Institutional Liquidity Returns", Publisher = "CoinDesk", Url = "https://coindesk.com/test", PublishDateUtc = DateTime.UtcNow },
                new() { Title = "Federal Reserve Holds Policy Steady Amid Economic Invariants", Publisher = "Federal Reserve", Url = "https://federalreserve.gov/test", PublishDateUtc = DateTime.UtcNow }
            });

        public Task<List<NewsArticleDto>> FilterNewsAsync(string? category = null, string? searchQuery = null, DateTime? startDateUtc = null, DateTime? endDateUtc = null, CancellationToken ct = default) =>
            Task.FromResult(new List<NewsArticleDto>());

        public Task<NewsArticleDto?> SummarizeArticleAsync(string articleId, CancellationToken ct = default) =>
            Task.FromResult<NewsArticleDto?>(null);
    }

    [Fact]
    public void OverviewViewModel_Initializes_WithNullAndUnavailable_InsteadOfFakeFinancials()
    {
        var mockCoord = new MockPollingCoordinator();
        var mockClient = new MockApiClient();
        var mockFx = new MockFxRateService();
        var mockNews = new MockRssNewsService();
        var vm = new OverviewViewModel(mockCoord, mockClient, mockFx, mockNews);

        // Explicitly uninitialized before telemetry arrives
        Assert.Null(vm.TotalEquity);
        Assert.Null(vm.WalletBalance);
        Assert.Null(vm.AvailableBalance);
        Assert.Null(vm.UsedMargin);
        Assert.Null(vm.UnrealizedPnl);
        Assert.Null(vm.DailyLossUsed);
        Assert.Null(vm.DailyLossLimit);
        Assert.Null(vm.EffectiveTradeRisk);

        // Formatted display properties must return "Unavailable" or "--", never fake values
        Assert.Equal("Unavailable", vm.TotalEquityDisplay);
        Assert.False(vm.HasTotalEquity);
        Assert.Equal("Unavailable", vm.AvailableBalanceDisplay);
        Assert.Equal("Unavailable", vm.UsedMarginDisplay);
        Assert.Equal("--", vm.DailyLossUsedDisplay);
        Assert.Equal("--", vm.DailyLossLimitDisplay);
        Assert.False(vm.HasDailyLossMetrics);
        Assert.Equal("Unverified", vm.EffectiveTradeRiskDisplayWithUnit);

        // Positions and Orders collections must start empty
        Assert.Empty(vm.OpenPositions);
        Assert.False(vm.HasOpenPositions);
        Assert.Empty(vm.OpenOrders);
        Assert.False(vm.HasOpenOrders);
        Assert.Equal(0, vm.OpenPositionsCount);
        Assert.Equal(0, vm.OpenOrdersCount);

        // Status must be UNAVAILABLE
        Assert.Equal("UNAVAILABLE", vm.AccountStatus);
        Assert.Equal("#8B949E", vm.AccountStatusColor);
    }

    [Fact]
    public void OverviewViewModel_MapsLiveTelemetry_AndAccountPositionsAndOrders_Accurately()
    {
        var mockCoord = new MockPollingCoordinator();
        var mockClient = new MockApiClient();
        var mockFx = new MockFxRateService();
        var mockNews = new MockRssNewsService();
        var vm = new OverviewViewModel(mockCoord, mockClient, mockFx, mockNews);

        // Emit realistic verified AsterDEX telemetry containing an active open position
        mockCoord.TriggerStatus(new SystemStatusDto
        {
            Mode = "LIVE_AUTO",
            SessionState = "ACTIVE",
            SessionId = "SESS_TEST_001",
            LiveTradingLocked = false,
            Account = new AccountSnapshotDto
            {
                Status = "CONNECTED",
                IsConnected = true,
                IsStale = false,
                TotalEquityUsdt = 14.4652m,
                AvailableBalanceUsdt = 12.4129m,
                UsedMarginUsdt = 1.0523m,
                UnrealizedPnlUsdt = 0.0815m,
                OpenPositionsCount = 1,
                LastSyncAgeSeconds = 4,
                PositionsSummary = new List<OpenPositionDto>
                {
                    new()
                    {
                        Symbol = "ZECUSDT",
                        Side = "SHORT",
                        PositionAmt = 0.0125m,
                        EntryPrice = 1281.9945m,
                        MarkPrice = 1275.5000m,
                        NotionalUsdt = 15.9437m,
                        Leverage = 15,
                        UnrealizedPnlUsdt = 0.0815m,
                        RoePct = 7.75m,
                        LiquidationPrice = 1350.00m,
                        StopLossPrice = 1298.50m,
                        TakeProfitPrice = 1245.00m,
                        TpOrders = new List<PositionOrderBracketDto>
                        {
                            new() { Price = 1245.00m, OrigQty = 0.0125m, Type = "TAKE_PROFIT_MARKET", ReduceOnly = true }
                        },
                        SlOrders = new List<PositionOrderBracketDto>
                        {
                            new() { Price = 1298.50m, OrigQty = 0.0125m, Type = "STOP_MARKET", ReduceOnly = true }
                        }
                    }
                }
            },
            RiskLimits = new RiskLimitsDto
            {
                DailyLossUsedUsdt = 0.0m,
                DailyLossLimitUsdt = 0.45m,
                DailyLossStatus = "VERIFIED",
                EffectiveTradeRiskUsdt = 0.25m,
                CanOpenPosition = true,
                LockState = "HELD",
                LockSymbol = "ZECUSDT",
                ReconciliationStatus = "OK"
            }
        });

        // Verify account metrics
        Assert.Equal("LIVE_AUTO", vm.Mode);
        Assert.Equal("ACTIVE", vm.SessionState);
        Assert.Equal(14.4652m, vm.TotalEquity);
        Assert.Equal("$14.47", vm.TotalEquityDisplay);
        Assert.Equal(12.4129m, vm.AvailableBalance);
        Assert.Equal("$12.41", vm.AvailableBalanceDisplay);
        Assert.Equal(1.0523m, vm.UsedMargin);
        Assert.Equal(1, vm.OpenPositionsCount);

        // Verify Open Positions table mapping
        Assert.Single(vm.OpenPositions);
        Assert.True(vm.HasOpenPositions);
        var pos = vm.OpenPositions[0];
        Assert.Equal("ZECUSDT", pos.Symbol);
        Assert.Equal("SHORT", pos.Side);
        Assert.Equal("#FF5252", pos.DirectionColor);
        Assert.Equal("1281.9945", pos.FormattedEntry);
        Assert.Equal("1275.5000", pos.FormattedMark);
        Assert.Equal("$1,298.5000", pos.FormattedSl);
        Assert.Equal("$1,245.0000", pos.FormattedTp);

        // Verify Open Orders table extraction (2 protective orders extracted from position)
        Assert.Equal(2, vm.OpenOrders.Count);
        Assert.True(vm.HasOpenOrders);
        Assert.Equal(2, vm.OpenOrdersCount);

        // Counter-side for SHORT must be BUY
        var tpOrder = vm.OpenOrders.First(o => o.OrderType == "TAKE_PROFIT_MARKET");
        Assert.Equal("BUY", tpOrder.Side);
        Assert.Equal(1245.00m, tpOrder.Price);
        Assert.True(tpOrder.ReduceOnly);
        Assert.Contains("Take-Profit", tpOrder.AssociatedPosition);

        var slOrder = vm.OpenOrders.First(o => o.OrderType == "STOP_MARKET");
        Assert.Equal("BUY", slOrder.Side);
        Assert.Equal(1298.50m, slOrder.Price);
        Assert.True(slOrder.ReduceOnly);
        Assert.Contains("Dynamic Structural SL", slOrder.AssociatedPosition);
    }

    [Fact]
    public void OverviewViewModel_FlagsStaleData_WhenThresholdExceeded()
    {
        var mockCoord = new MockPollingCoordinator();
        var mockClient = new MockApiClient();
        var mockFx = new MockFxRateService();
        var mockNews = new MockRssNewsService();
        var vm = new OverviewViewModel(mockCoord, mockClient, mockFx, mockNews);

        // Emit stale snapshot (age 150s exceeds 120s window)
        mockCoord.TriggerStatus(new SystemStatusDto
        {
            Account = new AccountSnapshotDto
            {
                Status = "CONNECTED",
                IsConnected = true,
                IsStale = true,
                LastSyncAgeSeconds = 150,
                TotalEquityUsdt = 14.34m
            }
        });

        Assert.True(vm.IsAccountStale);
        Assert.StartsWith("STALE", vm.FreshnessStatus);
        Assert.Equal("#FFB300", vm.FreshnessColor); // Amber warning
    }

    [Fact]
    public void OverviewViewModel_HandlesNullAccountAndRiskLimits_Gracefully()
    {
        var mockCoord = new MockPollingCoordinator();
        var mockClient = new MockApiClient();
        var mockFx = new MockFxRateService();
        var mockNews = new MockRssNewsService();
        var vm = new OverviewViewModel(mockCoord, mockClient, mockFx, mockNews);

        mockCoord.TriggerStatus(new SystemStatusDto
        {
            Mode = "PAPER",
            SessionState = "IDLE",
            SessionId = null,
            Account = null,
            RiskLimits = null
        });

        Assert.Equal("UNAVAILABLE", vm.AccountStatus);
        Assert.Null(vm.TotalEquity);
        Assert.Equal("Unavailable", vm.TotalEquityDisplay);
        Assert.False(vm.HasTotalEquity);
        Assert.Null(vm.AvailableBalance);
        Assert.Equal("Unavailable", vm.AvailableBalanceDisplay);
        Assert.Empty(vm.OpenPositions);
        Assert.False(vm.HasOpenPositions);
        Assert.Empty(vm.OpenOrders);
        Assert.False(vm.HasOpenOrders);
        Assert.Equal(0, vm.OpenPositionsCount);
        Assert.Equal(0, vm.OpenOrdersCount);
    }

    [Fact]
    public void OverviewViewModel_MapsAiScreeningCycle_AndDistinguishesProposalFromExecution()
    {
        var mockCoord = new MockPollingCoordinator();
        var mockClient = new MockApiClient();
        var mockFx = new MockFxRateService();
        var mockNews = new MockRssNewsService();
        var vm = new OverviewViewModel(mockCoord, mockClient, mockFx, mockNews);

        // Emit live session with rejected screening cycle
        mockCoord.TriggerSession(new LiveSessionEnvelopeDto
        {
            Status = "SUCCESS",
            Data = new LiveSessionDataDto
            {
                DailyFilledEntries = 1,
                EntryLimit = 3,
                LatestScreening = new ScreeningCycleDto
                {
                    CycleId = "cycle-test-001",
                    TimestampUtc = "2026-10-27T18:00:00.000Z",
                    Status = "REJECTED",
                    DecisionReason = "NO_SWEEP_REJECT_TRIGGER",
                    Stage1 = new Stage1ScreeningDto
                    {
                        TotalScreenedPairs = 638,
                        Top10 = new List<Top10CandidateDto>
                        {
                            new() { Symbol = "ETHUSDT", MarkPrice = 2345.92m, Rsi15m = 66.9m, ReasonSelected = "NO_SWEEP_REJECT_TRIGGER" }
                        }
                    },
                    Stage2 = new Stage2ScreeningDto
                    {
                        Top3 = new List<Top3CandidateDto>
                        {
                            new() { Symbol = "ZECUSDT", Score = 85.5m, Thesis = "15m Bearish Reclaim" }
                        }
                    },
                    Stage3 = new Stage3ScreeningDto
                    {
                        Symbol = "ZECUSDT",
                        Direction = "SHORT",
                        EntryPrice = 1285.00m,
                        StopLossPrice = 1298.50m,
                        TakeProfitPrice = 1245.00m
                    }
                }
            }
        });

        Assert.True(vm.HasScreeningCycle);
        Assert.Equal(638, vm.ScannedPairsCount);
        Assert.Single(vm.Stage1Top10);
        Assert.Single(vm.Stage2Top3);
        Assert.True(vm.HasStage3Candidate);
        Assert.Equal("ZECUSDT", vm.Stage3Candidate?.Symbol);

        // Verification: AI Proposal was REJECTED, so Execution state must be "PROPOSAL REJECTED -> NO ORDER"
        Assert.Equal("PROPOSAL REJECTED -> NO ORDER", vm.AiExecutionState);
        Assert.Equal("#FF5252", vm.ExecutionStateColor);
        Assert.Equal("NO_SWEEP_REJECT_TRIGGER", vm.AiRiskGateDecision);
    }

    [Fact]
    public async Task OverviewViewModel_MapsUsdIdrFxSeries_AndConversionUnits()
    {
        var mockCoord = new MockPollingCoordinator();
        var mockClient = new MockApiClient();
        var mockFx = new MockFxRateService();
        var mockNews = new MockRssNewsService();
        var vm = new OverviewViewModel(mockCoord, mockClient, mockFx, mockNews);

        // FxSeries initialized via InitialLoadAuxiliaryAsync
        var series = await mockFx.GetHistoricalSeriesAsync("1M");
        vm.FxSeries = series;

        Assert.NotNull(vm.FxSeries);
        Assert.Equal("1M", vm.FxSeries.Period);
        Assert.Equal(18139m, vm.FxSeries.CurrentRate);
        Assert.Equal("Rp 18,139.00", vm.FxSeries.FormattedCurrentRate);
        Assert.Equal("+0.77% (1M)", vm.FxSeries.FormattedChange);
        Assert.Equal("~Rp 18,139.00 (1:1 USD Peg Est.)", vm.FxSeries.FormattedUsdtEstimate);
        Assert.Contains("European Central Bank", vm.FxSeries.Source);
        Assert.Equal(2, vm.FxSeries.Points.Count);
    }

    [Fact]
    public async Task OverviewViewModel_MapsNewsSourceAttribution_AndPreservesHeadlines()
    {
        var mockCoord = new MockPollingCoordinator();
        var mockClient = new MockApiClient();
        var mockFx = new MockFxRateService();
        var mockNews = new MockRssNewsService();
        var vm = new OverviewViewModel(mockCoord, mockClient, mockFx, mockNews);

        var news = await mockNews.FetchLatestNewsAsync();
        vm.RecentNews = new System.Collections.ObjectModel.ObservableCollection<NewsArticleDto>(news);

        Assert.Equal(2, vm.RecentNews.Count);
        Assert.Equal("CoinDesk", vm.RecentNews[0].Publisher);
        Assert.Equal("Federal Reserve", vm.RecentNews[1].Publisher);
        Assert.False(string.IsNullOrWhiteSpace(vm.RecentNews[0].Title));
        Assert.False(string.IsNullOrWhiteSpace(vm.RecentNews[1].Title));
    }
}
