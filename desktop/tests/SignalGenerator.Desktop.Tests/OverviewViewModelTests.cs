using System;
using System.Collections.Generic;
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

    private class MockApiClient : IApiClient
    {
        public string BaseUrl => "http://127.0.0.1:8000";
        public void SetBaseUrl(string baseUrl) { }
        public Task<ApiResult<SystemStatusDto>> GetStatusAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<SystemStatusDto>.Success(new SystemStatusDto()));
        public Task<ApiResult<LiveSessionEnvelopeDto>> GetLiveSessionAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<LiveSessionEnvelopeDto>.Success(new LiveSessionEnvelopeDto()));
        public Task<ApiResult<JournalSummaryDto>> GetJournalSummaryAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<JournalSummaryDto>.Success(new JournalSummaryDto()));
        public Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(string? search = null, int page = 1, int pageSize = 25, CancellationToken ct = default) => Task.FromResult(ApiResult<ScreenerResponseDto>.Success(new ScreenerResponseDto()));
        public Task<ApiResult<List<string>>> GetSymbolsAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<List<string>>.Success(new List<string>()));
    }

    [Fact]
    public void OverviewViewModel_BindsLiveTelemetry_Correctly()
    {
        var mockCoord = new MockPollingCoordinator();
        var mockClient = new MockApiClient();
        var vm = new OverviewViewModel(mockCoord, mockClient);

        // Emit status update
        mockCoord.TriggerStatus(new SystemStatusDto
        {
            Mode = "LIVE_AUTO",
            SessionState = "ACTIVE",
            SessionId = "SESS_TEST_001",
            Account = new AccountSnapshotDto
            {
                Status = "CONNECTED",
                TotalEquityUsdt = 14.4652m,
                AvailableBalanceUsdt = 12.4129m
            },
            RiskLimits = new RiskLimitsDto
            {
                DailyLossUsedUsdt = 0.0m,
                DailyLossLimitUsdt = 0.45m,
                DailyLossStatus = "VERIFIED",
                CanOpenPosition = true,
                LockState = "HELD",
                LockSymbol = "BZUSDT"
            },
            Scanner = new ScannerStatusDto
            {
                State = "IDLE",
                ProcessStatus = "RUNNING",
                ScanCount = 125,
                ConfiguredLiveStrategyId = "15M_SWEEP_RECLAIM_SMC"
            }
        });

        Assert.Equal("LIVE_AUTO", vm.Mode);
        Assert.Equal("ACTIVE", vm.SessionState);
        Assert.Equal("SESS_TEST_001", vm.SessionId);
        Assert.Equal(14.4652m, vm.TotalEquity);
        Assert.Equal(12.4129m, vm.AvailableBalance);
        Assert.Equal(0.0m, vm.DailyLossUsed);
        Assert.Equal(0.45m, vm.DailyLossLimit);
        Assert.Equal("VERIFIED", vm.DailyLossStatus);
        Assert.True(vm.CanOpenPosition);
        Assert.Equal("HELD", vm.GlobalLockState);
        Assert.Equal("BZUSDT", vm.GlobalLockSymbol);
        Assert.Equal("IDLE", vm.ScannerState);
        Assert.Equal("RUNNING", vm.ScannerProcessStatus);
        Assert.Equal(125, vm.ScanCount);
        Assert.Equal("15M_SWEEP_RECLAIM_SMC", vm.ActiveStrategy);

        // Emit session envelope
        mockCoord.TriggerSession(new LiveSessionEnvelopeDto
        {
            Status = "SUCCESS",
            Preflight = new PreflightAuditDto { Verdict = "GO" },
            Data = new LiveSessionDataDto
            {
                DailyFilledEntries = 1,
                EntryLimit = 3,
                LastStatusMessage = "Audit passed cleanly."
            }
        });

        Assert.Equal("GO", vm.PreflightVerdict);
        Assert.Equal(1, vm.DailyEntriesUsed);
        Assert.Equal(3, vm.DailyEntriesAllowance);
        Assert.Equal("1 / 3", vm.EntriesPlacedDisplay);
        Assert.Equal("Audit passed cleanly.", vm.LastStatusMessage);
    }
}
