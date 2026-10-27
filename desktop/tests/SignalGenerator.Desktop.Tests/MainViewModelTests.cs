using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class MainViewModelTests
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
        public void TriggerConnection(bool connected) => OnConnectionStatusChanged?.Invoke(connected);

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

    private class MockSettingsService : ISettingsService
    {
        public DesktopSettings CurrentSettings { get; set; } = new DesktopSettings();
        public event Action<DesktopSettings>? OnSettingsChanged;
        public void SaveSettings(DesktopSettings settings) { CurrentSettings = settings; OnSettingsChanged?.Invoke(settings); }
        public void UpdateBaseUrl(string baseUrl) { CurrentSettings.BaseUrl = baseUrl; }
        public void UpdatePollingInterval(int seconds) { CurrentSettings.PollingIntervalSeconds = seconds; }
        public void UpdateAutoPolling(bool enable) { CurrentSettings.EnableAutoPolling = enable; }
    }

    private (MainViewModel Vm, MockPollingCoordinator Coord, MockApiClient Client) CreateMainVm()
    {
        var coord = new MockPollingCoordinator();
        var client = new MockApiClient();
        var settings = new MockSettingsService();

        var overviewVm = new OverviewViewModel(coord, client);
        var monitorVm = new MarketMonitorViewModel(client, coord);
        var screenerVm = new ScreenerViewModel(client);
        var liquidityVm = new LiquidityIntelViewModel();
        var hunterVm = new GroqHunterViewModel(client);
        var journalVm = new JournalViewModel(coord, client);
        var alertsVm = new AlertsViewModel(client);
        var participantsVm = new MarketParticipantsViewModel(client);
        var newsVm = new CryptoNewsViewModel(coord);
        var settingsVm = new SettingsViewModel(settings, client, coord);

        var mainVm = new MainViewModel(
            coord, settings,
            overviewVm, monitorVm, screenerVm,
            liquidityVm, hunterVm, journalVm,
            alertsVm, participantsVm, newsVm,
            settingsVm
        );

        return (mainVm, coord, client);
    }

    [Fact]
    public void MainViewModel_Initializes_WithNullSafeDefaults()
    {
        var (vm, _, _) = CreateMainVm();

        Assert.Equal("UNVERIFIED", vm.Mode);
        Assert.Equal("UNVERIFIED", vm.SessionState);
        Assert.Equal("IDLE", vm.LockBadge);
        Assert.Equal("Unavailable", vm.TotalEquityText);
        Assert.Equal("Unavailable", vm.AvailableBalanceText);
        Assert.Equal("Overview", vm.SelectedNav);
        Assert.IsType<OverviewViewModel>(vm.CurrentViewModel);
        Assert.Equal("#FF5252", vm.ConnectionStatusColor); // Not connected initially
    }

    [Fact]
    public void MainViewModel_UpdatesHeaderTelemetry_FromStatus()
    {
        var (vm, coord, _) = CreateMainVm();

        coord.TriggerConnection(true);
        Assert.True(vm.IsConnected);
        Assert.Equal("ONLINE", vm.ConnectionStatusText);
        Assert.Equal("#00E676", vm.ConnectionStatusColor);

        coord.TriggerStatus(new SystemStatusDto
        {
            Mode = "LIVE_AUTO",
            SessionState = "REAUTHORIZATION_REQUIRED",
            Account = new AccountSnapshotDto
            {
                TotalEquityUsdt = 14.3438m,
                AvailableBalanceUsdt = 12.2915m
            },
            RiskLimits = new RiskLimitsDto
            {
                LockState = "IDLE"
            }
        });

        Assert.Equal("LIVE_AUTO", vm.Mode);
        Assert.Equal("#FF9D00", vm.ModeColor);
        Assert.Equal("REAUTHORIZATION_REQUIRED", vm.SessionState);
        Assert.Equal("#FF9D00", vm.SessionStateColor);
        Assert.Equal("$14.34 USDT", vm.TotalEquityText);
        Assert.Equal("$12.29 USDT", vm.AvailableBalanceText);
        Assert.Equal("IDLE", vm.LockBadge);
        Assert.Equal("#8B949E", vm.LockBadgeColor);
    }

    [Fact]
    public void MainViewModel_HandlesHeldLockState()
    {
        var (vm, coord, _) = CreateMainVm();

        coord.TriggerSession(new LiveSessionEnvelopeDto
        {
            Data = new LiveSessionDataDto
            {
                GlobalLockState = "HELD",
                GlobalLockSymbol = "ZECUSDT"
            }
        });

        Assert.Equal("LOCKED: ZECUSDT", vm.LockBadge);
        Assert.Equal("#FF9D00", vm.LockBadgeColor);
    }

    [Fact]
    public void MainViewModel_NavigatesAcrossBloombergTerminalViews()
    {
        var (vm, _, _) = CreateMainVm();

        vm.Navigate("MarketMonitor");
        Assert.Equal("MarketMonitor", vm.SelectedNav);
        Assert.IsType<MarketMonitorViewModel>(vm.CurrentViewModel);

        vm.Navigate("Screener");
        Assert.Equal("Screener", vm.SelectedNav);
        Assert.IsType<ScreenerViewModel>(vm.CurrentViewModel);

        vm.Navigate("GroqHunter");
        Assert.Equal("GroqHunter", vm.SelectedNav);
        Assert.IsType<GroqHunterViewModel>(vm.CurrentViewModel);

        vm.Navigate("Journal");
        Assert.Equal("Journal", vm.SelectedNav);
        Assert.IsType<JournalViewModel>(vm.CurrentViewModel);

        vm.Navigate("MarketParticipants");
        Assert.Equal("MarketParticipants", vm.SelectedNav);
        Assert.IsType<MarketParticipantsViewModel>(vm.CurrentViewModel);

        vm.Navigate("CryptoNews");
        Assert.Equal("CryptoNews", vm.SelectedNav);
        Assert.IsType<CryptoNewsViewModel>(vm.CurrentViewModel);

        vm.Navigate("Overview");
        Assert.Equal("Overview", vm.SelectedNav);
        Assert.IsType<OverviewViewModel>(vm.CurrentViewModel);
    }
}
