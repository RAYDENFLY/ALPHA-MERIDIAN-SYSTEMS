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
        public Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<AutotraderScreeningLogResponseDto>.Success(new AutotraderScreeningLogResponseDto()));
        public Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(CancellationToken ct = default) => Task.FromResult(ApiResult<List<AlertItemDto>>.Success(new List<AlertItemDto>()));
        public Task<ApiResult<RetailPositioningResponseDto>> GetRetailPositioningAsync(string symbol = "BTCUSDT", CancellationToken ct = default) => Task.FromResult(ApiResult<RetailPositioningResponseDto>.Success(new RetailPositioningResponseDto()));
    }

    [Fact]
    public void OverviewViewModel_Initializes_WithNullAndUnavailable_InsteadOfFakeFinancials()
    {
        var mockCoord = new MockPollingCoordinator();
        var mockClient = new MockApiClient();
        var vm = new OverviewViewModel(mockCoord, mockClient);

        // Explicitly uninitialized before telemetry arrives
        Assert.Null(vm.TotalEquity);
        Assert.Null(vm.WalletBalance);
        Assert.Null(vm.AvailableBalance);
        Assert.Null(vm.UsedMargin);
        Assert.Null(vm.UnrealizedPnl);
        Assert.Null(vm.DailyLossUsed);
        Assert.Null(vm.DailyLossLimit);
        Assert.Null(vm.EffectiveTradeRisk);

        // Formatted display properties must return "Unavailable", never fake hardcoded numbers or fake zeroes
        Assert.Equal("Unavailable", vm.TotalEquityDisplay);
        Assert.Equal("Unavailable", vm.WalletBalanceDisplay);
        Assert.Equal("Unavailable", vm.AvailableBalanceDisplay);
        Assert.Equal("Unavailable", vm.UsedMarginDisplay);
        Assert.Equal("Unavailable", vm.UnrealizedPnlDisplay);
        Assert.Equal("Unavailable", vm.DailyLossUsedDisplay);
        Assert.Equal("Unavailable", vm.DailyLossLimitDisplay);
        Assert.Equal("Unavailable", vm.EffectiveTradeRiskDisplay);
        Assert.Equal("Unavailable", vm.EffectiveTradeRiskDisplayWithUnit);

        Assert.False(vm.HasTotalEquity);
        Assert.False(vm.HasDailyLossMetrics);

        // Security defaults
        Assert.False(vm.CanOpenPosition);
        Assert.Equal("BLOCKED", vm.CanOpenPositionDisplay);
        Assert.Equal("#F85149", vm.CanOpenPositionColor);
        Assert.Equal("UNAVAILABLE", vm.AccountStatus);
        Assert.Equal("UNVERIFIED", vm.DailyLossStatus);
        Assert.Equal("UNVERIFIED", vm.ReconciliationStatus);
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
                AvailableBalanceUsdt = 12.4129m,
                UsedMarginUsdt = 0.0m
            },
            RiskLimits = new RiskLimitsDto
            {
                DailyLossUsedUsdt = 0.0m,
                DailyLossLimitUsdt = 0.45m,
                DailyLossStatus = "VERIFIED",
                EffectiveTradeRiskUsdt = 0.25m,
                CanOpenPosition = true,
                LockState = "HELD",
                LockSymbol = "BZUSDT",
                ReconciliationStatus = "OK"
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
        Assert.Equal(0.0m, vm.UsedMargin);
        Assert.Equal(0.0m, vm.DailyLossUsed);
        Assert.Equal(0.45m, vm.DailyLossLimit);
        Assert.Equal(0.25m, vm.EffectiveTradeRisk);
        Assert.Equal("VERIFIED", vm.DailyLossStatus);
        Assert.True(vm.CanOpenPosition);
        Assert.Equal("HELD", vm.GlobalLockState);
        Assert.Equal("BZUSDT", vm.GlobalLockSymbol);
        Assert.Equal("IDLE", vm.ScannerState);
        Assert.Equal("RUNNING", vm.ScannerProcessStatus);
        Assert.Equal(125, vm.ScanCount);
        Assert.Equal("15M_SWEEP_RECLAIM_SMC", vm.ActiveStrategy);

        // Formatted display values
        Assert.Equal("$14.47", vm.TotalEquityDisplay);
        Assert.True(vm.HasTotalEquity);
        Assert.Equal("$12.41", vm.AvailableBalanceDisplay);
        Assert.Equal("$0.00", vm.UsedMarginDisplay);
        Assert.Equal("$0.00", vm.DailyLossUsedDisplay);
        Assert.Equal("$0.45", vm.DailyLossLimitDisplay);
        Assert.True(vm.HasDailyLossMetrics);
        Assert.Equal("$0.25 USDT", vm.EffectiveTradeRiskDisplayWithUnit);
        Assert.Equal("ALLOWED", vm.CanOpenPositionDisplay);
        Assert.Equal("#3FB950", vm.CanOpenPositionColor);
        Assert.Equal("#3FB950", vm.DailyLossStatusColor);
        Assert.Equal("#3FB950", vm.ReconciliationStatusColor);
        Assert.Equal("#3FB950", vm.AccountStatusColor);
        Assert.Equal("#3FB950", vm.SessionStateColor);

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
        Assert.Equal("#3FB950", vm.PreflightVerdictColor);
        Assert.Equal(1, vm.DailyEntriesUsed);
        Assert.Equal(3, vm.DailyEntriesAllowance);
        Assert.Equal("1 / 3", vm.EntriesPlacedDisplay);
        Assert.Equal("Audit passed cleanly.", vm.LastStatusMessage);
    }

    [Fact]
    public void OverviewViewModel_HandlesNullAccountAndRiskLimits_Gracefully()
    {
        var mockCoord = new MockPollingCoordinator();
        var mockClient = new MockApiClient();
        var vm = new OverviewViewModel(mockCoord, mockClient);

        // Emit status update with null sub-objects
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
        Assert.Null(vm.UsedMargin);
        Assert.Equal("Unavailable", vm.UsedMarginDisplay);

        Assert.Null(vm.DailyLossUsed);
        Assert.Equal("Unavailable", vm.DailyLossUsedDisplay);
        Assert.Null(vm.DailyLossLimit);
        Assert.Equal("Unavailable", vm.DailyLossLimitDisplay);
        Assert.False(vm.HasDailyLossMetrics);

        Assert.Null(vm.EffectiveTradeRisk);
        Assert.Equal("Unavailable", vm.EffectiveTradeRiskDisplayWithUnit);

        Assert.False(vm.CanOpenPosition);
        Assert.Equal("BLOCKED", vm.CanOpenPositionDisplay);
        Assert.Equal("UNAVAILABLE", vm.BlockReason);
        Assert.Equal("UNAVAILABLE", vm.ReconciliationStatus);
    }
}
