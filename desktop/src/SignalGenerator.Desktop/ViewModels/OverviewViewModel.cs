using System;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class OverviewViewModel : ViewModelBase
{
    private readonly IPollingCoordinator _pollingCoordinator;
    private readonly IApiClient _apiClient;

    [ObservableProperty]
    private string _mode = "PAPER";

    [ObservableProperty]
    private string _sessionState = "INACTIVE";

    [ObservableProperty]
    private string _sessionId = "None";

    [ObservableProperty]
    private bool _isLiveTradingLocked = true;

    [ObservableProperty]
    private string _entriesPlacedDisplay = "0 / 3";

    [ObservableProperty]
    private int _dailyEntriesUsed = 0;

    [ObservableProperty]
    private int _dailyEntriesAllowance = 3;

    [ObservableProperty]
    private string _preflightVerdict = "PENDING";

    [ObservableProperty]
    private string _globalLockState = "RELEASED";

    [ObservableProperty]
    private string _globalLockSymbol = "--";

    [ObservableProperty]
    private string _accountStatus = "UNAVAILABLE";

    [ObservableProperty]
    private decimal _totalEquity = 0.00m;

    [ObservableProperty]
    private decimal _walletBalance = 0.00m;

    [ObservableProperty]
    private decimal _availableBalance = 0.00m;

    [ObservableProperty]
    private decimal _usedMargin = 0.00m;

    [ObservableProperty]
    private decimal _unrealizedPnl = 0.00m;

    [ObservableProperty]
    private int _openPositionsCount = 0;

    [ObservableProperty]
    private decimal _dailyLossUsed = 0.00m;

    [ObservableProperty]
    private decimal _dailyLossLimit = 0.45m;

    [ObservableProperty]
    private string _dailyLossStatus = "UNVERIFIED";

    [ObservableProperty]
    private decimal _effectiveTradeRisk = 0.15m;

    [ObservableProperty]
    private bool _canOpenPosition = false;

    [ObservableProperty]
    private string _blockReason = "--";

    [ObservableProperty]
    private string _reconciliationStatus = "OK";

    [ObservableProperty]
    private string _scannerState = "IDLE";

    [ObservableProperty]
    private string _scannerProcessStatus = "IDLE";

    [ObservableProperty]
    private int _scanCount = 0;

    [ObservableProperty]
    private string _activeStrategy = "15M_SWEEP_RECLAIM_SMC";

    [ObservableProperty]
    private string _lastScanTime = "--";

    [ObservableProperty]
    private string _lastStatusMessage = "Waiting for live session telemetry...";

    [ObservableProperty]
    private bool _isRefreshing;

    public OverviewViewModel(IPollingCoordinator pollingCoordinator, IApiClient apiClient)
    {
        _pollingCoordinator = pollingCoordinator ?? throw new ArgumentNullException(nameof(pollingCoordinator));
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));

        _pollingCoordinator.OnStatusUpdated += HandleStatusUpdated;
        _pollingCoordinator.OnLiveSessionUpdated += HandleLiveSessionUpdated;
    }

    [RelayCommand]
    public async Task RefreshNowAsync()
    {
        if (IsRefreshing) return;
        IsRefreshing = true;
        try
        {
            await _pollingCoordinator.TriggerPollNowAsync();
        }
        finally
        {
            IsRefreshing = false;
        }
    }

    private void HandleStatusUpdated(SystemStatusDto status)
    {
        if (status == null) return;

        Mode = status.Mode;
        SessionState = status.SessionState;
        SessionId = status.SessionId ?? "None";
        IsLiveTradingLocked = status.LiveTradingLocked;

        if (status.Account != null)
        {
            AccountStatus = status.Account.Status;
            TotalEquity = status.Account.TotalEquityUsdt ?? 0m;
            WalletBalance = status.Account.WalletBalanceUsdt ?? 0m;
            AvailableBalance = status.Account.AvailableBalanceUsdt ?? 0m;
            UsedMargin = status.Account.UsedMarginUsdt ?? 0m;
            UnrealizedPnl = status.Account.UnrealizedPnlUsdt ?? 0m;
            OpenPositionsCount = status.Account.OpenPositionsCount;
        }

        if (status.RiskLimits != null)
        {
            DailyLossUsed = status.RiskLimits.DailyLossUsedUsdt ?? 0m;
            DailyLossLimit = status.RiskLimits.DailyLossLimitUsdt ?? 0.45m;
            DailyLossStatus = status.RiskLimits.DailyLossStatus ?? "UNVERIFIED";
            EffectiveTradeRisk = status.RiskLimits.EffectiveTradeRiskUsdt ?? 0m;
            CanOpenPosition = status.RiskLimits.CanOpenPosition;
            BlockReason = status.RiskLimits.BlockReason ?? "None";
            ReconciliationStatus = status.RiskLimits.ReconciliationStatus ?? "OK";

            if (status.RiskLimits.LockState != null)
            {
                GlobalLockState = status.RiskLimits.LockState;
            }
            if (status.RiskLimits.LockSymbol != null)
            {
                GlobalLockSymbol = status.RiskLimits.LockSymbol;
            }
        }

        if (status.Scanner != null)
        {
            ScannerState = status.Scanner.State;
            ScannerProcessStatus = status.Scanner.ProcessStatus;
            ScanCount = status.Scanner.ScanCount;
            ActiveStrategy = status.Scanner.ConfiguredLiveStrategyId ?? "15M_SWEEP_RECLAIM_SMC";
            LastScanTime = status.Scanner.LastScanUtc ?? "--";
        }
    }

    private void HandleLiveSessionUpdated(LiveSessionEnvelopeDto envelope)
    {
        if (envelope == null) return;

        if (envelope.Preflight != null)
        {
            PreflightVerdict = envelope.Preflight.Verdict;
        }

        if (envelope.Data != null)
        {
            DailyEntriesUsed = envelope.Data.DailyFilledEntries;
            DailyEntriesAllowance = envelope.Data.EntryLimit;
            EntriesPlacedDisplay = $"{DailyEntriesUsed} / {DailyEntriesAllowance}";

            if (!string.IsNullOrWhiteSpace(envelope.Data.GlobalLockState))
            {
                GlobalLockState = envelope.Data.GlobalLockState;
            }
            if (!string.IsNullOrWhiteSpace(envelope.Data.GlobalLockSymbol))
            {
                GlobalLockSymbol = envelope.Data.GlobalLockSymbol;
            }

            if (!string.IsNullOrWhiteSpace(envelope.Data.LastStatusMessage))
            {
                LastStatusMessage = envelope.Data.LastStatusMessage;
            }
        }
    }
}
