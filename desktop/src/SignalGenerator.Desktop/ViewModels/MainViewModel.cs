using System;
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
    public ScreenerViewModel ScreenerVm { get; }
    public JournalViewModel JournalVm { get; }
    public LiquidityIntelViewModel LiquidityIntelVm { get; }
    public AlertsViewModel AlertsVm { get; }
    public GroqHunterViewModel GroqHunterVm { get; }
    public SettingsViewModel SettingsVm { get; }

    [ObservableProperty]
    private ViewModelBase _currentViewModel;

    [ObservableProperty]
    private string _selectedNav = "Overview";

    [ObservableProperty]
    private bool _isConnected;

    [ObservableProperty]
    private string _connectionStatusText = "CONNECTING...";

    [ObservableProperty]
    private string _mode = "PAPER";

    [ObservableProperty]
    private string _sessionState = "INACTIVE";

    [ObservableProperty]
    private string _lockBadge = "UNLOCKED";

    [ObservableProperty]
    private string _totalEquityText = "$0.00 USDT";

    [ObservableProperty]
    private string _lastPollTimestamp = "--:--:--";

    [ObservableProperty]
    private string _backendUrl;

    [ObservableProperty]
    private bool _isAutoPolling;

    [ObservableProperty]
    private string? _lastErrorMessage;

    public MainViewModel(
        IPollingCoordinator pollingCoordinator,
        ISettingsService settingsService,
        OverviewViewModel overviewVm,
        ScreenerViewModel screenerVm,
        JournalViewModel journalVm,
        LiquidityIntelViewModel liquidityIntelVm,
        AlertsViewModel alertsVm,
        GroqHunterViewModel groqHunterVm,
        SettingsViewModel settingsVm)
    {
        _pollingCoordinator = pollingCoordinator ?? throw new ArgumentNullException(nameof(pollingCoordinator));
        _settingsService = settingsService ?? throw new ArgumentNullException(nameof(settingsService));

        OverviewVm = overviewVm;
        ScreenerVm = screenerVm;
        JournalVm = journalVm;
        LiquidityIntelVm = liquidityIntelVm;
        AlertsVm = alertsVm;
        GroqHunterVm = groqHunterVm;
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
            "Screener" => ScreenerVm,
            "Journal" => JournalVm,
            "LiquidityIntel" => LiquidityIntelVm,
            "Alerts" => AlertsVm,
            "GroqHunter" => GroqHunterVm,
            "Settings" => SettingsVm,
            _ => OverviewVm
        };

        if (destination == "Screener" && ScreenerVm.Markets.Count == 0)
        {
            _ = ScreenerVm.LoadMarketsAsync();
        }
    }

    [RelayCommand]
    public async Task RefreshAllAsync()
    {
        await _pollingCoordinator.TriggerPollNowAsync();
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
        ConnectionStatusText = connected ? "CONNECTED" : "OFFLINE";
    }

    private void HandleStatusUpdated(SystemStatusDto status)
    {
        if (status == null) return;
        Mode = status.Mode;
        SessionState = status.SessionState;
        if (status.Account?.TotalEquityUsdt != null)
        {
            TotalEquityText = $"${status.Account.TotalEquityUsdt.Value:F4} USDT";
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
        else
        {
            LockBadge = "UNLOCKED";
        }
    }

    private void HandlePollingError(string error)
    {
        LastErrorMessage = error;
    }
}
