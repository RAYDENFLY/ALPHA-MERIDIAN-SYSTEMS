using System;
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
        public bool StartScannerCalled { get; private set; }
        public bool StopScannerCalled { get; private set; }
        public bool ReauthorizeCalled { get; private set; }
        public bool SingleScanCalled { get; private set; }

        public string CustomSessionState { get; set; } = "ACTIVE";
        public bool CustomScannerActive { get; set; } = true;
        public string? CustomRecoveryReason { get; set; }
        public bool FailNextStart { get; set; }

        public override Task<ApiResult<LiveSessionEnvelopeDto>> GetLiveSessionAsync(CancellationToken ct = default)
        {
            var data = new LiveSessionDataDto
            {
                SessionId = "SESS_TEST_1001",
                Mode = "LIVE_AUTO",
                State = CustomSessionState,
                IsActive = CustomSessionState == "ACTIVE",
                IsPaused = CustomSessionState == "PAUSED",
                AuthorizedBy = "OPERATOR",
                ScannerActive = CustomScannerActive,
                LastScanUtc = "2026-10-27T19:55:00Z",
                RecoveryReason = CustomRecoveryReason,
                LastStatusMessage = "Operating normally.",
                CycleTelemetry = new CycleTelemetryDto
                {
                    CycleId = "cycle-test-12345",
                    CurrentStage = "IDLE",
                    Status = "SUCCESS",
                    NextCycleDueUtc = "2026-10-27T19:56:00Z"
                }
            };

            return Task.FromResult(ApiResult<LiveSessionEnvelopeDto>.Success(new LiveSessionEnvelopeDto
            {
                Status = "SUCCESS",
                Data = data
            }));
        }

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

        public override Task<ApiResult<ScannerControlResponseDto>> StartScannerAsync(CancellationToken ct = default)
        {
            StartScannerCalled = true;
            if (FailNextStart)
            {
                return Task.FromResult(ApiResult<ScannerControlResponseDto>.Failure("Failed to initialize scanner runner.", 500));
            }

            return Task.FromResult(ApiResult<ScannerControlResponseDto>.Success(new ScannerControlResponseDto
            {
                Status = "SUCCESS",
                Message = "Background scanner resumed.",
                ScannerState = "IDLE"
            }));
        }

        public override Task<ApiResult<ScannerControlResponseDto>> StopScannerAsync(CancellationToken ct = default)
        {
            StopScannerCalled = true;
            return Task.FromResult(ApiResult<ScannerControlResponseDto>.Success(new ScannerControlResponseDto
            {
                Status = "SUCCESS",
                Message = "Background scanner paused.",
                ScannerState = "STOPPED"
            }));
        }

        public override Task<ApiResult<LiveSessionEnvelopeDto>> ReauthorizeSessionAsync(string operatorId = "OPERATOR", CancellationToken ct = default)
        {
            ReauthorizeCalled = true;
            return Task.FromResult(ApiResult<LiveSessionEnvelopeDto>.Success(new LiveSessionEnvelopeDto
            {
                Status = "SUCCESS",
                Data = new LiveSessionDataDto
                {
                    State = "ACTIVE",
                    IsActive = true,
                    AuthorizedBy = operatorId,
                    ScannerActive = true
                }
            }));
        }

        public override Task<ApiResult<GroqAutoSetupResponseDto>> TriggerSingleScanAsync(CancellationToken ct = default)
        {
            SingleScanCalled = true;
            return Task.FromResult(ApiResult<GroqAutoSetupResponseDto>.Success(new GroqAutoSetupResponseDto
            {
                Status = "SUCCESS",
                TimestampUtc = "2026-10-27T19:55:30Z"
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

    [Fact]
    public async Task ScannerLifecycle_StateResolution_RendersAccurately()
    {
        var client = new FakeScreeningApiClient();
        var vm = new GroqHunterViewModel(client);

        // 1. RUNNING state
        client.CustomSessionState = "ACTIVE";
        client.CustomScannerActive = true;
        await vm.RefreshScreeningLogAsync();
        Assert.Equal("RUNNING", vm.ScannerState);
        Assert.Equal("#00E676", vm.ScannerStateColor);
        Assert.Equal("ACTIVE", vm.SessionState);

        // 2. STOPPED state
        client.CustomSessionState = "PAUSED";
        client.CustomScannerActive = false;
        await vm.RefreshScreeningLogAsync();
        Assert.Equal("STOPPED", vm.ScannerState);
        Assert.Equal("#8B949E", vm.ScannerStateColor);

        // 3. AUTH_REQUIRED state
        client.CustomSessionState = "REAUTHORIZATION_REQUIRED";
        await vm.RefreshScreeningLogAsync();
        Assert.Equal("AUTH_REQUIRED", vm.ScannerState);
        Assert.Equal("#FF9100", vm.ScannerStateColor);

        // 4. ERROR state
        client.CustomSessionState = "ACTIVE";
        client.CustomRecoveryReason = "Exchange API key invalid";
        await vm.RefreshScreeningLogAsync();
        Assert.Equal("ERROR", vm.ScannerState);
        Assert.Equal("#FF5252", vm.ScannerStateColor);
        Assert.True(vm.HasRecoveryReason);
    }

    [Fact]
    public async Task StartScanner_RequiresConfirmation_AndExecutesSuccessfully()
    {
        var client = new FakeScreeningApiClient();
        var vm = new GroqHunterViewModel(client);

        // 1. Request start -> confirmation dialog opened with safe warnings
        vm.RequestStartScanner();
        Assert.True(vm.IsConfirmDialogVisible);
        Assert.Equal("START", vm.ConfirmActionType);
        Assert.Contains("CONTINUOUS", vm.ConfirmDialogTitle);
        Assert.Contains("READ-ONLY", vm.ConfirmDialogWarning);
        Assert.False(client.StartScannerCalled);

        // 2. Execute confirmation -> calls API
        await vm.ExecuteConfirmedActionAsync();
        Assert.False(vm.IsConfirmDialogVisible);
        Assert.True(client.StartScannerCalled);
        Assert.Contains("SUCCESS", vm.ActionFeedback);
    }

    [Fact]
    public async Task StartScanner_HandlesFailureGracefully()
    {
        var client = new FakeScreeningApiClient { FailNextStart = true };
        var vm = new GroqHunterViewModel(client);

        vm.RequestStartScanner();
        await vm.ExecuteConfirmedActionAsync();

        Assert.True(client.StartScannerCalled);
        Assert.Contains("ERROR", vm.ActionFeedback);
    }

    [Fact]
    public async Task StopScanner_RequiresConfirmation_AndExecutesSuccessfully()
    {
        var client = new FakeScreeningApiClient();
        var vm = new GroqHunterViewModel(client);

        vm.RequestStopScanner();
        Assert.True(vm.IsConfirmDialogVisible);
        Assert.Equal("STOP", vm.ConfirmActionType);
        Assert.Contains("STOP", vm.ConfirmDialogTitle);
        Assert.Contains("NOT cancel existing exchange", vm.ConfirmDialogWarning);

        await vm.ExecuteConfirmedActionAsync();
        Assert.False(vm.IsConfirmDialogVisible);
        Assert.True(client.StopScannerCalled);
        Assert.Contains("SUCCESS", vm.ActionFeedback);
    }

    [Fact]
    public void PreventDuplicateActions_WhenActionPending()
    {
        var client = new FakeScreeningApiClient();
        var vm = new GroqHunterViewModel(client)
        {
            IsActionPending = true // Simulate in-flight action
        };

        vm.RequestStartScanner();
        Assert.False(vm.IsConfirmDialogVisible);

        vm.RequestStopScanner();
        Assert.False(vm.IsConfirmDialogVisible);

        vm.RequestSingleScan();
        Assert.False(vm.IsConfirmDialogVisible);
    }

    [Fact]
    public async Task Distinction_BetweenContinuousScannerAndSingleScanCycle()
    {
        var client = new FakeScreeningApiClient();
        var vm = new GroqHunterViewModel(client);

        // Trigger single scan
        vm.RequestSingleScan();
        Assert.True(vm.IsConfirmDialogVisible);
        Assert.Equal("SINGLE_SCAN", vm.ConfirmActionType);
        Assert.Contains("SINGLE", vm.ConfirmDialogTitle);
        Assert.Contains("ONE-CYCLE ONLY", vm.ConfirmDialogWarning);

        await vm.ExecuteConfirmedActionAsync();
        Assert.True(client.SingleScanCalled);
        Assert.False(client.StartScannerCalled); // Continuous start NOT invoked
        Assert.Contains("Single AI Funnel cycle completed", vm.ActionFeedback);
    }

    [Fact]
    public async Task Reauthorization_UnavailableWhenActive_AvailableWhenRequired()
    {
        var client = new FakeScreeningApiClient { CustomSessionState = "ACTIVE" };
        var vm = new GroqHunterViewModel(client);
        await vm.RefreshScreeningLogAsync();

        // When ACTIVE: Re-authentication is blocked with clear feedback
        vm.RequestReauthorize();
        Assert.False(vm.IsConfirmDialogVisible);
        Assert.Contains("RE-AUTHENTICATION UNAVAILABLE: Live session is currently ACTIVE", vm.ActionFeedback);
        Assert.False(client.ReauthorizeCalled);

        // When REAUTHORIZATION_REQUIRED: Re-authentication dialog opens
        client.CustomSessionState = "REAUTHORIZATION_REQUIRED";
        await vm.RefreshScreeningLogAsync();

        vm.RequestReauthorize();
        Assert.True(vm.IsConfirmDialogVisible);
        Assert.Equal("REAUTH", vm.ConfirmActionType);

        await vm.ExecuteConfirmedActionAsync();
        Assert.True(client.ReauthorizeCalled);
        Assert.Contains("SUCCESS: Live session reauthorized", vm.ActionFeedback);
    }

    [Fact]
    public void CancelConfirmDialog_AbortsExecutionWithoutMutatingBackend()
    {
        var client = new FakeScreeningApiClient();
        var vm = new GroqHunterViewModel(client);

        vm.RequestStartScanner();
        Assert.True(vm.IsConfirmDialogVisible);

        vm.CancelConfirmDialog();
        Assert.False(vm.IsConfirmDialogVisible);
        Assert.Empty(vm.ConfirmActionType);
        Assert.False(client.StartScannerCalled);
    }
}
