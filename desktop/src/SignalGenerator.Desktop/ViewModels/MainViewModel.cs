using System;
using System.Globalization;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class MainViewModel : ViewModelBase
{
    private readonly IPollingCoordinator _pollingCoordinator;
    private readonly ISettingsService _settingsService;

    public OverviewViewModel OverviewVm { get; }
    public MarketMonitorViewModel MarketMonitorVm { get; }
    public ScreenerViewModel ScreenerVm { get; }
    public LiquidityIntelViewModel LiquidityIntelVm { get; }
    public GroqHunterViewModel GroqHunterVm { get; }
    public JournalViewModel JournalVm { get; }
    public AlertsViewModel AlertsVm { get; }
    public MarketParticipantsViewModel MarketParticipantsVm { get; }
    public CryptoNewsViewModel CryptoNewsVm { get; }
    public SettingsViewModel SettingsVm { get; }

    [ObservableProperty]
    private ViewModelBase _currentViewModel;

    [ObservableProperty]
    private string _selectedNav = "Overview";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(ConnectionStatusColor))]
    private bool _isConnected;

    [ObservableProperty]
    private string _connectionStatusText = "CONNECTING...";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(ModeColor))]
    private string _mode = "UNVERIFIED";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(SessionStateColor))]
    private string _sessionState = "UNVERIFIED";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(LockBadgeColor))]
    private string _lockBadge = "IDLE";

    [ObservableProperty]
    private string _totalEquityText = "Unavailable";

    [ObservableProperty]
    private string _availableBalanceText = "Unavailable";

    [ObservableProperty]
    private string _lastPollTimestamp = "--:--:--";

    [ObservableProperty]
    private string _backendUrl;

    [ObservableProperty]
    private bool _isAutoPolling;

    [ObservableProperty]
    private string? _lastErrorMessage;

    // Semantic Terminal Colors (Bloomberg Style)
    public string ConnectionStatusColor => IsConnected ? "#00E676" : "#FF5252";

    public string ModeColor => Mode?.ToUpperInvariant() switch
    {
        "LIVE_AUTO" or "LIVE" => "#FF9D00",
        "PAPER" => "#448AFF",
        _ => "#8B949E"
    };

    public string SessionStateColor => SessionState?.ToUpperInvariant() switch
    {
        "ACTIVE" => "#00E676",
        "STARTING" or "DRAINING" => "#FFB300",
        "REAUTHORIZATION_REQUIRED" => "#FF9D00",
        "KILL_SWITCH_HALTED" or "ERROR" => "#FF5252",
        _ => "#8B949E"
    };

    public string LockBadgeColor => LockBadge?.StartsWith("LOCKED", StringComparison.OrdinalIgnoreCase) == true
        ? "#FF9D00"
        : "#8B949E";

    public MainViewModel(
        IPollingCoordinator pollingCoordinator,
        ISettingsService settingsService,
        OverviewViewModel overviewVm,
        MarketMonitorViewModel marketMonitorVm,
        ScreenerViewModel screenerVm,
        LiquidityIntelViewModel liquidityIntelVm,
        GroqHunterViewModel groqHunterVm,
        JournalViewModel journalVm,
        AlertsViewModel alertsVm,
        MarketParticipantsViewModel marketParticipantsVm,
        CryptoNewsViewModel cryptoNewsVm,
        SettingsViewModel settingsVm)
    {
        _pollingCoordinator = pollingCoordinator ?? throw new ArgumentNullException(nameof(pollingCoordinator));
        _settingsService = settingsService ?? throw new ArgumentNullException(nameof(settingsService));

        OverviewVm = overviewVm;
        MarketMonitorVm = marketMonitorVm;
        ScreenerVm = screenerVm;
        LiquidityIntelVm = liquidityIntelVm;
        GroqHunterVm = groqHunterVm;
        JournalVm = journalVm;
        AlertsVm = alertsVm;
        MarketParticipantsVm = marketParticipantsVm;
        CryptoNewsVm = cryptoNewsVm;
        SettingsVm = settingsVm;

        _currentViewModel = OverviewVm;
        _backendUrl = _settingsService.CurrentSettings.BaseUrl;
        _isAutoPolling = _settingsService.CurrentSettings.EnableAutoPolling;

        _pollingCoordinator.OnConnectionStatusChanged += HandleConnectionStatusChanged;
        _pollingCoordinator.OnStatusUpdated += HandleStatusUpdated;
        _pollingCoordinator.OnLiveSessionUpdated += HandleLiveSessionUpdated;
        _pollingCoordinator.OnPollingError += HandlePollingError;

        if (_isAutoPolling)
        {
            _pollingCoordinator.Start();
        }
    }

    [RelayCommand]
    public void Navigate(string destination)
    {
        SelectedNav = destination;
        CurrentViewModel = destination switch
        {
            "Overview" => OverviewVm,
            "MarketMonitor" => MarketMonitorVm,
            "Screener" => ScreenerVm,
            "LiquidityIntel" => LiquidityIntelVm,
            "GroqHunter" => GroqHunterVm,
            "Journal" => JournalVm,
            "Alerts" => AlertsVm,
            "MarketParticipants" => MarketParticipantsVm,
            "CryptoNews" => CryptoNewsVm,
            "Settings" => SettingsVm,
            _ => OverviewVm
        };

        if (destination == "MarketMonitor" && MarketMonitorVm.Markets.Count == 0)
        {
            _ = MarketMonitorVm.LoadMarketMatrixAsync();
        }
        else if (destination == "Screener" && ScreenerVm.Markets.Count == 0)
        {
            _ = ScreenerVm.LoadMarketsAsync();
        }
        else if (destination == "MarketParticipants" && MarketParticipantsVm.Observations.Count == 0)
        {
            _ = MarketParticipantsVm.LoadPositioningAsync();
        }
    }

    [RelayCommand]
    public async Task RefreshAllAsync()
    {
        await _pollingCoordinator.TriggerPollNowAsync();
        if (CurrentViewModel == MarketMonitorVm)
        {
            await MarketMonitorVm.LoadMarketMatrixAsync();
        }
        else if (CurrentViewModel == ScreenerVm)
        {
            await ScreenerVm.LoadMarketsAsync();
        }
        else if (CurrentViewModel == MarketParticipantsVm)
        {
            await MarketParticipantsVm.LoadPositioningAsync();
        }
    }

    [RelayCommand]
    public void TogglePolling()
    {
        if (IsAutoPolling)
        {
            _pollingCoordinator.Stop();
            IsAutoPolling = false;
        }
        else
        {
            _pollingCoordinator.Start();
            IsAutoPolling = true;
        }
    }

    private void HandleConnectionStatusChanged(bool connected)
    {
        IsConnected = connected;
        ConnectionStatusText = connected ? "ONLINE" : "OFFLINE";
    }

    private void HandleStatusUpdated(SystemStatusDto status)
    {
        if (status == null) return;
        Mode = !string.IsNullOrWhiteSpace(status.Mode) ? status.Mode : "UNVERIFIED";
        SessionState = !string.IsNullOrWhiteSpace(status.SessionState) ? status.SessionState : "UNVERIFIED";

        if (status.Account != null)
        {
            TotalEquityText = status.Account.TotalEquityUsdt.HasValue
                ? $"${status.Account.TotalEquityUsdt.Value.ToString("N2", CultureInfo.InvariantCulture)} USDT"
                : "Unavailable";

            AvailableBalanceText = status.Account.AvailableBalanceUsdt.HasValue
                ? $"${status.Account.AvailableBalanceUsdt.Value.ToString("N2", CultureInfo.InvariantCulture)} USDT"
                : "Unavailable";
        }
        else
        {
            TotalEquityText = "Unavailable";
            AvailableBalanceText = "Unavailable";
        }

        if (status.RiskLimits != null && !string.IsNullOrWhiteSpace(status.RiskLimits.LockState))
        {
            if (status.RiskLimits.LockState.Equals("HELD", StringComparison.OrdinalIgnoreCase))
            {
                LockBadge = $"LOCKED: {status.RiskLimits.LockSymbol ?? "ACTIVE"}";
            }
            else
            {
                LockBadge = status.RiskLimits.LockState;
            }
        }

        LastPollTimestamp = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";
        LastErrorMessage = null;
    }

    private void HandleLiveSessionUpdated(LiveSessionEnvelopeDto envelope)
    {
        if (envelope?.Data == null) return;

        if (!string.IsNullOrWhiteSpace(envelope.Data.GlobalLockState) &&
            envelope.Data.GlobalLockState.Equals("HELD", StringComparison.OrdinalIgnoreCase))
        {
            LockBadge = $"LOCKED: {envelope.Data.GlobalLockSymbol ?? "ACTIVE"}";
        }
        else if (!string.IsNullOrWhiteSpace(envelope.Data.GlobalLockState))
        {
            LockBadge = envelope.Data.GlobalLockState;
        }
    }

    private void HandlePollingError(string error)
    {
        LastErrorMessage = error;
    }
}
