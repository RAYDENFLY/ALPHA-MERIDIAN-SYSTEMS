using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class AiSignalExecutionMonitorTests
{
    private class FakeScreeningApiClient : IApiClient
    {
        public string BaseUrl => "http://127.0.0.1:8000";
        public void SetBaseUrl(string baseUrl) { }
        public Task<ApiResult<SystemStatusDto>> GetStatusAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<SystemStatusDto>.Success(new SystemStatusDto()));
        public Task<ApiResult<LiveSessionEnvelopeDto>> GetLiveSessionAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<LiveSessionEnvelopeDto>.Success(new LiveSessionEnvelopeDto()));
        public Task<ApiResult<JournalSummaryDto>> GetJournalSummaryAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<JournalSummaryDto>.Success(new JournalSummaryDto()));
        public Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(string? search = null, int page = 1, int pageSize = 25, CancellationToken ct = default) => Task.FromResult(ApiResult<ScreenerResponseDto>.Success(new ScreenerResponseDto()));
        public Task<ApiResult<List<string>>> GetSymbolsAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<List<string>>.Success(new List<string>()));
        public Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<List<AlertItemDto>>.Success(new List<AlertItemDto>()));
        public Task<ApiResult<RetailPositioningResponseDto>> GetRetailPositioningAsync(string symbol = "BTCUSDT", CancellationToken ct = default) => Task.FromResult(ApiResult<RetailPositioningResponseDto>.Success(new RetailPositioningResponseDto()));
        public Task<ApiResult<JournalTradesResponseDto>> GetJournalTradesAsync(int limit = 50, CancellationToken ct = default) => Task.FromResult(ApiResult<JournalTradesResponseDto>.Success(new JournalTradesResponseDto()));
        public Task<ApiResult<ChartSymbolsResponseDto>> GetChartSymbolsAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<ChartSymbolsResponseDto>.Success(new ChartSymbolsResponseDto()));
        public Task<ApiResult<LiquidityLayersResponseDto>> GetLiquidityLayersAsync(string symbol, string timeframe = "1h", CancellationToken ct = default) => Task.FromResult(ApiResult<LiquidityLayersResponseDto>.Success(new LiquidityLayersResponseDto()));
        public Task<ApiResult<List<KlineDto>>> GetKlinesAsync(string symbol, string timeframe = "1h", int limit = 100, CancellationToken ct = default) => Task.FromResult(ApiResult<List<KlineDto>>.Success(new List<KlineDto>()));

        public Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default)
        {
            var cycle = new ScreeningCycleDto
            {
                CycleId = "cycle-test-12345",
                TimestampUtc = "2026-10-27T17:30:00Z",
                Status = "REJECTED",
                ExecutionVenue = "LIVE_AUTO",
                MarketRegime = new MarketRegimeDto { RegimeLabel = "ALTCOIN_LEADERSHIP" },
                Stage1 = new Stage1ScreeningDto
                {
                    TotalScreenedPairs = 600,
                    Top10 = new List<Top10CandidateDto>
                    {
                        new Top10CandidateDto
                        {
                            Symbol = "ETHUSDT",
                            MarkPrice = 2350m,
                            Change24hPct = 1.25m,
                            Turnover24hM = 300m,
                            SpreadBps = 0.05m,
                            ReasonSelected = "INSUFFICIENT_ROOM_TO_RESISTANCE"
                        }
                    }
                },
                Stage2 = new Stage2ScreeningDto
                {
                    Top3 = new List<Top3CandidateDto>
                    {
                        new Top3CandidateDto
                        {
                            Rank = 1,
                            Symbol = "ETHUSDT",
                            Score = 75m,
                            Thesis = "Structural 15m pullback"
                        }
                    }
                },
                Stage3 = new Stage3ScreeningDto
                {
                    Decision = "WAIT",
                    Symbol = "ETHUSDT",
                    Direction = "LONG",
                    EntryPrice = 2350m,
                    Reasoning = "All candidates gated by DEALING_RANGE or room to resistance.",
                    SlInvalidationType = "15M_SWEEP_WICK"
                }
            };

            return Task.FromResult(ApiResult<AutotraderScreeningLogResponseDto>.Success(new AutotraderScreeningLogResponseDto
            {
                ActiveCycle = cycle,
                History = new List<ScreeningCycleDto> { cycle }
            }));
        }
    }

    [Fact]
    public async Task GroqHunterViewModel_MapsThreeStagesCorrectly()
    {
        var client = new FakeScreeningApiClient();
        var vm = new GroqHunterViewModel(client);

        await vm.RefreshScreeningLogAsync();

        Assert.Equal("LIVE", vm.FreshnessStatus);
        Assert.Equal("cycle-test-12345", vm.ActiveCycleId);
        Assert.Single(vm.Top10Candidates);
        Assert.Equal("ETHUSDT", vm.Top10Candidates[0].Symbol);
        Assert.Equal("INSUFFICIENT_ROOM_TO_RESISTANCE", vm.Top10Candidates[0].ReasonSelected);

        Assert.Single(vm.Top3Candidates);
        Assert.Equal(1, vm.Top3Candidates[0].Rank);
        Assert.Equal("ETHUSDT", vm.Top3Candidates[0].Symbol);

        Assert.NotNull(vm.FinalSetup);
        Assert.Equal("WAIT", vm.FinalSetup.Decision);
        Assert.Equal("15M_SWEEP_WICK", vm.FinalSetup.SlInvalidationType);
    }
}
