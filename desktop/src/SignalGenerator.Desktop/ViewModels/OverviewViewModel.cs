using System;
using System.Globalization;
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
    private string _mode = "UNVERIFIED";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(SessionStateColor))]
    private string _sessionState = "UNVERIFIED";

    [ObservableProperty]
    private string _sessionId = "--";

    [ObservableProperty]
    private bool _isLiveTradingLocked = true;

    [ObservableProperty]
    private string _entriesPlacedDisplay = "-- / --";

    [ObservableProperty]
    private int _dailyEntriesUsed = 0;

    [ObservableProperty]
    private int _dailyEntriesAllowance = 3;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(PreflightVerdictColor))]
    private string _preflightVerdict = "PENDING";

    [ObservableProperty]
    private string _globalLockState = "IDLE";

    [ObservableProperty]
    private string _globalLockSymbol = "--";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(AccountStatusColor))]
    private string _accountStatus = "UNAVAILABLE";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(TotalEquityDisplay))]
    [NotifyPropertyChangedFor(nameof(HasTotalEquity))]
    private decimal? _totalEquity;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(WalletBalanceDisplay))]
    private decimal? _walletBalance;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(AvailableBalanceDisplay))]
    private decimal? _availableBalance;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(UsedMarginDisplay))]
    private decimal? _usedMargin;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(UnrealizedPnlDisplay))]
    private decimal? _unrealizedPnl;

    [ObservableProperty]
    private int _openPositionsCount = 0;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(DailyLossUsedDisplay))]
    [NotifyPropertyChangedFor(nameof(HasDailyLossMetrics))]
    private decimal? _dailyLossUsed;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(DailyLossLimitDisplay))]
    [NotifyPropertyChangedFor(nameof(HasDailyLossMetrics))]
    private decimal? _dailyLossLimit;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(DailyLossStatusColor))]
    private string _dailyLossStatus = "UNVERIFIED";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(EffectiveTradeRiskDisplay))]
    [NotifyPropertyChangedFor(nameof(EffectiveTradeRiskDisplayWithUnit))]
    private decimal? _effectiveTradeRisk;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanOpenPositionDisplay))]
    [NotifyPropertyChangedFor(nameof(CanOpenPositionColor))]
    private bool _canOpenPosition = false;

    [ObservableProperty]
    private string _blockReason = "--";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(ReconciliationStatusColor))]
    private string _reconciliationStatus = "UNVERIFIED";

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

    // Display helpers with strict null/unavailable handling and invariant culture formatting
    public string TotalEquityDisplay => TotalEquity.HasValue ? $"${TotalEquity.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";
    public bool HasTotalEquity => TotalEquity.HasValue;

    public string AvailableBalanceDisplay => AvailableBalance.HasValue ? $"${AvailableBalance.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";
    public string UsedMarginDisplay => UsedMargin.HasValue ? $"${UsedMargin.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";
    public string WalletBalanceDisplay => WalletBalance.HasValue ? $"${WalletBalance.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";
    public string UnrealizedPnlDisplay => UnrealizedPnl.HasValue ? $"${UnrealizedPnl.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";

    public string DailyLossUsedDisplay => DailyLossUsed.HasValue ? $"${DailyLossUsed.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";
    public string DailyLossLimitDisplay => DailyLossLimit.HasValue ? $"${DailyLossLimit.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";
    public bool HasDailyLossMetrics => DailyLossUsed.HasValue && DailyLossLimit.HasValue;

    public string EffectiveTradeRiskDisplay => EffectiveTradeRisk.HasValue ? $"${EffectiveTradeRisk.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";
    public string EffectiveTradeRiskDisplayWithUnit => EffectiveTradeRisk.HasValue ? $"${EffectiveTradeRisk.Value.ToString("N2", CultureInfo.InvariantCulture)} USDT" : "Unavailable";

    public string CanOpenPositionDisplay => CanOpenPosition ? "ALLOWED" : "BLOCKED";
    public string CanOpenPositionColor => CanOpenPosition ? "#3FB950" : "#F85149";

    public string DailyLossStatusColor => DailyLossStatus?.ToUpperInvariant() switch
    {
        "VERIFIED" => "#3FB950",
        "UNVERIFIED" => "#D29922",
        "BREACHED" => "#F85149",
        _ => "#8B949E"
    };

    public string ReconciliationStatusColor => ReconciliationStatus?.ToUpperInvariant() switch
    {
        "SUCCESS" or "OK" => "#3FB950",
        "PENDING" or "SYNCING" => "#D29922",
        "FAILED" or "ERROR" => "#F85149",
        _ => "#8B949E"
    };

    public string AccountStatusColor => AccountStatus?.ToUpperInvariant() switch
    {
        "CONNECTED" => "#3FB950",
        "CONNECTING" or "SYNCING" => "#D29922",
        "ERROR" or "FAILED" => "#F85149",
        _ => "#8B949E"
    };

    public string SessionStateColor => SessionState?.ToUpperInvariant() switch
    {
        "ACTIVE" => "#3FB950",
        "STARTING" or "DRAINING" => "#D29922",
        "KILL_SWITCH_HALTED" or "ERROR" => "#F85149",
        _ => "#8B949E"
    };

    public string PreflightVerdictColor => PreflightVerdict?.ToUpperInvariant() switch
    {
        "GO" => "#3FB950",
        "NO-GO" => "#F85149",
        _ => "#D29922"
    };

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

        Mode = !string.IsNullOrWhiteSpace(status.Mode) ? status.Mode : "PAPER";
        SessionState = !string.IsNullOrWhiteSpace(status.SessionState) ? status.SessionState : "INACTIVE";
        SessionId = status.SessionId ?? "None";
        IsLiveTradingLocked = status.LiveTradingLocked;

        if (status.Account != null)
        {
            AccountStatus = !string.IsNullOrWhiteSpace(status.Account.Status)
                ? status.Account.Status
                : (status.Account.IsConnected ? "CONNECTED" : "UNAVAILABLE");
            TotalEquity = status.Account.TotalEquityUsdt;
            WalletBalance = status.Account.WalletBalanceUsdt;
            AvailableBalance = status.Account.AvailableBalanceUsdt;
            UsedMargin = status.Account.UsedMarginUsdt;
            UnrealizedPnl = status.Account.UnrealizedPnlUsdt;
            OpenPositionsCount = status.Account.OpenPositionsCount;
        }
        else
        {
            AccountStatus = "UNAVAILABLE";
            TotalEquity = null;
            WalletBalance = null;
            AvailableBalance = null;
            UsedMargin = null;
            UnrealizedPnl = null;
            OpenPositionsCount = 0;
        }

        if (status.RiskLimits != null)
        {
            DailyLossUsed = status.RiskLimits.DailyLossUsedUsdt;
            DailyLossLimit = status.RiskLimits.DailyLossLimitUsdt;
            DailyLossStatus = status.RiskLimits.DailyLossStatus ?? "UNVERIFIED";
            EffectiveTradeRisk = status.RiskLimits.EffectiveTradeRiskUsdt;
            CanOpenPosition = status.RiskLimits.CanOpenPosition;
            BlockReason = !string.IsNullOrWhiteSpace(status.RiskLimits.BlockReason)
                ? status.RiskLimits.BlockReason
                : (status.RiskLimits.CanOpenPosition ? "None" : "Position Open Disallowed");
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
        else
        {
            DailyLossUsed = null;
            DailyLossLimit = null;
            DailyLossStatus = "UNVERIFIED";
            EffectiveTradeRisk = null;
            CanOpenPosition = false;
            BlockReason = "UNAVAILABLE";
            ReconciliationStatus = "UNAVAILABLE";
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
