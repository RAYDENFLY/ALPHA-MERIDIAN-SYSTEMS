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

    private class MockApiClient : TestMockApiClient
    {
    }

    private class MockPersonalJournalService : IPersonalJournalService
    {
        public string DatabasePath => "test.db";
        public Task InitializeAsync() => Task.CompletedTask;
        public Task<List<PersonalTradeRecord>> GetAllTradesAsync() => Task.FromResult(new List<PersonalTradeRecord>());
        public Task<PersonalTradeRecord?> GetTradeByIdAsync(string tradeId) => Task.FromResult<PersonalTradeRecord?>(null);
        public Task<bool> SaveTradeAsync(PersonalTradeRecord trade) => Task.FromResult(true);
        public Task<bool> DeleteTradeAsync(string tradeId) => Task.FromResult(true);
        public Task<Dictionary<DateTime, decimal>> GetDailyNetPnlAsync(int year, int month) => Task.FromResult(new Dictionary<DateTime, decimal>());
        public Task<decimal> GetMonthlyNetPnlAsync(int year, int month) => Task.FromResult(0m);
    }

    private class MockRssNewsService : IRssNewsService
    {
        public INewsStorageService Storage { get; } = null!;
        public IReadOnlyList<FeedSourceStatusDto> SourceStatuses { get; } = new List<FeedSourceStatusDto>();
        public Task<List<NewsArticleDto>> FetchLatestNewsAsync(CancellationToken ct = default) => Task.FromResult(new List<NewsArticleDto>());
        public Task<List<NewsArticleDto>> FilterNewsAsync(string? category = null, string? searchQuery = null, DateTime? startDateUtc = null, DateTime? endDateUtc = null, string? publisher = null, CancellationToken ct = default) => Task.FromResult(new List<NewsArticleDto>());
        public Task<NewsArticleDto?> SummarizeArticleAsync(string articleId, CancellationToken ct = default) => Task.FromResult<NewsArticleDto?>(null);
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

    private class MockFxRateService : IFxRateService
    {
        public Task<FxRateSnapshotDto> GetLatestRateAsync(CancellationToken ct = default) =>
            Task.FromResult(new FxRateSnapshotDto { Rate = 18139m, Date = DateTime.UtcNow });

        public Task<FxHistoricalSeriesDto> GetHistoricalSeriesAsync(string period, CancellationToken ct = default) =>
            Task.FromResult(new FxHistoricalSeriesDto { Period = period, CurrentRate = 18139m, StartRate = 18000m, ChangePct = 0.77m, MinRate = 17950m, MaxRate = 18200m });
    }

    private (MainViewModel Vm, MockPollingCoordinator Coord, MockApiClient Client) CreateMainVm()
    {
        var coord = new MockPollingCoordinator();
        var client = new MockApiClient();
        var settings = new MockSettingsService();
        var personalJournal = new MockPersonalJournalService();
        var newsService = new MockRssNewsService();
        var fxService = new MockFxRateService();
        var paperService = new PaperTradingService();

        var overviewVm = new OverviewViewModel(coord, client, fxService, newsService);
        var monitorVm = new MarketMonitorViewModel(client, coord);
        var screenerVm = new ScreenerViewModel(client);
        var multiBookVm = new MultiMarketOrderBookViewModel(client, paperService);
        var workstationVm = new TradingWorkstationViewModel(client, paperService);
        var liquidityVm = new LiquidityIntelViewModel(client);
        var hunterVm = new GroqHunterViewModel(client);
        var journalVm = new JournalViewModel(coord, client, personalJournal);
        var alertsVm = new AlertsViewModel(client);
        var participantsVm = new MarketParticipantsViewModel(client);
        var newsVm = new CryptoNewsViewModel(newsService, coord);
        var settingsVm = new SettingsViewModel(settings, client, coord);

        var mainVm = new MainViewModel(
            coord, settings,
            overviewVm, monitorVm, screenerVm,
            multiBookVm, workstationVm,
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
    public void MainWindow_ContainsOfficialCorporateCopyrightFooter()
    {
        const string expectedFooter = "© 2026 PT Authentic Media Services. Alpha Meridian Systems is a product of AMS Capital.";
        string altPath = System.IO.Path.GetFullPath("desktop/src/SignalGenerator.Desktop/Views/MainWindow.axaml");
        if (System.IO.File.Exists(altPath))
        {
            string content = System.IO.File.ReadAllText(altPath);
            Assert.Contains(expectedFooter, content);
        }
    }

    [Fact]
    public void MainViewModel_NavigatesAcrossTerminalViews()
    {
        var (vm, _, _) = CreateMainVm();

        vm.Navigate("MarketIntelligence");
        Assert.Equal("MarketIntelligence", vm.SelectedNav);
        Assert.IsType<MarketMonitorViewModel>(vm.CurrentViewModel);

        vm.Navigate("Alerts");
        Assert.Equal("Alerts", vm.SelectedNav);
        Assert.IsType<AlertsViewModel>(vm.CurrentViewModel);

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
