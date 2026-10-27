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
    private class FakeScreeningApiClient : TestMockApiClient
    {
        public override Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default)
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
