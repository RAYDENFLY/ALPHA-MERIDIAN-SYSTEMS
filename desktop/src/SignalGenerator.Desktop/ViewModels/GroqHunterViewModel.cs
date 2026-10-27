using System;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class GroqHunterViewModel : ViewModelBase
{
    private readonly IApiClient _apiClient;
    private CancellationTokenSource? _cts;

    [ObservableProperty]
    private string _terminalTitle = "AI SIGNAL & EXECUTION MONITOR";

    [ObservableProperty]
    private string _groqStatus = "ONLINE";

    [ObservableProperty]
    private string _modelName = "llama-3.3-70b-versatile";

    [ObservableProperty]
    private string _stage1PrefilterStatus = "ACTIVE (0-Token Python Heuristic)";

    [ObservableProperty]
    private string _stage2SMCVerification = "ACTIVE (Dealing Range + 15m Sweeps)";

    [ObservableProperty]
    private string _stage3LLMExecution = "STANDBY (Triggered on SMC Confluence)";

    [ObservableProperty]
    private string _stage4OrderProtection = "ACTIVE (3-Tier Dynamic SL + Adaptive TP)";

    [ObservableProperty]
    private string _keyRotationStatus = "HEALTHY (Multi-Key Vault Active)";

    [ObservableProperty]
    private string _activeCycleId = "--";

    [ObservableProperty]
    private string _latestSelectedSymbol = "NONE (GATED)";

    [ObservableProperty]
    private string _latestDecisionReason = "Waiting for next 15m candle close...";

    [ObservableProperty]
    private string _latestTimestampUtc = "--:--:--";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(FreshnessStatusColor))]
    private string _freshnessStatus = "LIVE";

    public string FreshnessStatusColor => FreshnessStatus?.ToUpperInvariant() switch
    {
        "LIVE" => "#00E676",
        "STALE" => "#FF9D00",
        "ERROR" => "#FF5252",
        _ => "#8B949E"
    };

    [ObservableProperty]
    private string _dataProvenance = "LIVE /api/autotrader/screening-log";

    [ObservableProperty]
    private DateTime? _lastSuccessfulUpdateTimeUtc;

    [ObservableProperty]
    private string _lastSuccessfulUpdateTimeString = "--:--:--";

    [ObservableProperty]
    private int _selectedStageTab = 0; // 0: Top 10, 1: Top 3, 2: Final Setup, 3: Cycles History

    [ObservableProperty]
    private ScreeningCycleDto? _selectedCycle;

    [ObservableProperty]
    private ObservableCollection<Top10CandidateDto> _top10Candidates = new();

    [ObservableProperty]
    private ObservableCollection<Top3CandidateDto> _top3Candidates = new();

    [ObservableProperty]
    private Stage3ScreeningDto? _finalSetup;

    [ObservableProperty]
    private string _marketRegimeLabel = "ALTCOIN LEADERSHIP";

    [ObservableProperty]
    private ObservableCollection<ScreeningCycleDto> _cycleHistory = new();

    [ObservableProperty]
    private string _scannerState = "RUNNING";

    [ObservableProperty]
    private string _scannerStateColor = "#00E676";

    [ObservableProperty]
    private string _sessionState = "ACTIVE";

    [ObservableProperty]
    private string _sessionStateColor = "#00E676";

    [ObservableProperty]
    private string _authorizedBy = "OPERATOR";

    [ObservableProperty]
    private string _lastScanTimeUtc = "--:--:--";

    [ObservableProperty]
    private string _nextCycleDueUtc = "--:--:--";

    [ObservableProperty]
    private string _currentCycleStage = "IDLE";

    [ObservableProperty]
    private string _lastStatusMessage = "Session active. Monitoring market conditions.";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasRecoveryReason))]
    private string _recoveryReason = string.Empty;

    public bool HasRecoveryReason => !string.IsNullOrWhiteSpace(RecoveryReason);

    [ObservableProperty]
    private bool _isActionPending;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasActionFeedback))]
    private string _actionFeedback = string.Empty;

    public bool HasActionFeedback => !string.IsNullOrWhiteSpace(ActionFeedback);

    [ObservableProperty]
    private bool _isConfirmDialogVisible;

    [ObservableProperty]
    private string _confirmDialogTitle = string.Empty;

    [ObservableProperty]
    private string _confirmDialogDescription = string.Empty;

    [ObservableProperty]
    private string _confirmDialogWarning = string.Empty;

    [ObservableProperty]
    private string _confirmActionType = string.Empty;

    [ObservableProperty]
    private bool _isLoading;

    public GroqHunterViewModel(IApiClient apiClient)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _ = RefreshScreeningLogAsync();
    }

    [RelayCommand]
    public void SelectStage(int stageIndex)
    {
        SelectedStageTab = stageIndex;
    }

    [RelayCommand]
    public void SelectCycle(ScreeningCycleDto cycle)
    {
        if (cycle == null) return;
        SelectedCycle = cycle;
        ApplyCycleData(cycle);
    }

    [RelayCommand]
    public void RequestStartScanner()
    {
        if (IsActionPending) return;

        ConfirmActionType = "START";
        ConfirmDialogTitle = "START AI AUTO-HUNTER CONTINUOUS SCANNER";
        ConfirmDialogDescription = "This operation will start/resume the continuous background market scanning loop. The scanner runs automatically across candle boundaries to detect SMC setups.";
        ConfirmDialogWarning = "READ-ONLY SCREENING SAFETY: Desktop controls operate strictly under isolated advisory mode. Live exchange trade execution will NOT be triggered by starting the scanner.";
        IsConfirmDialogVisible = true;
    }

    [RelayCommand]
    public void RequestStopScanner()
    {
        if (IsActionPending) return;

        ConfirmActionType = "STOP";
        ConfirmDialogTitle = "STOP AI AUTO-HUNTER CONTINUOUS SCANNER";
        ConfirmDialogDescription = "This operation will safely pause the continuous background market scanner loop. Automatic candidate generation will be suspended until resumed.";
        ConfirmDialogWarning = "INVARIANT PRESERVED: Pausing the scanner does NOT cancel existing exchange resting limit orders or close active positions.";
        IsConfirmDialogVisible = true;
    }

    [RelayCommand]
    public void RequestSingleScan()
    {
        if (IsActionPending) return;

        ConfirmActionType = "SINGLE_SCAN";
        ConfirmDialogTitle = "TRIGGER SINGLE 10 -> 3 -> 1 GROQ AI SCAN CYCLE";
        ConfirmDialogDescription = "This operation will execute exactly ONE immediate advisory screening cycle through the 10 -> 3 -> 1 Groq AI Trade Funnel in PAPER mode.";
        ConfirmDialogWarning = "ONE-CYCLE ONLY: This is NOT continuous scanning. Zero orders will be dispatched to the exchange.";
        IsConfirmDialogVisible = true;
    }

    [RelayCommand]
    public void RequestReauthorize()
    {
        if (IsActionPending) return;

        if (SessionState.Equals("ACTIVE", StringComparison.OrdinalIgnoreCase))
        {
            ActionFeedback = "RE-AUTHENTICATION UNAVAILABLE: Live session is currently ACTIVE and authorized by OPERATOR. Pre-flight verification gates are already satisfied.";
            return;
        }

        ConfirmActionType = "REAUTH";
        ConfirmDialogTitle = "RE-AUTHENTICATE LIVE AUTO-EXECUTION SESSION";
        ConfirmDialogDescription = "This operation will execute pre-flight safety audits and restore authorized session status after a server pause or restart.";
        ConfirmDialogWarning = "OPERATOR CREDENTIALS: Authorization is verified via local loopback security boundary. No private API secrets are exposed.";
        IsConfirmDialogVisible = true;
    }

    [RelayCommand]
    public void CancelConfirmDialog()
    {
        IsConfirmDialogVisible = false;
        ConfirmActionType = string.Empty;
    }

    [RelayCommand]
    public async Task ExecuteConfirmedActionAsync()
    {
        if (IsActionPending) return;
        var action = ConfirmActionType;
        IsConfirmDialogVisible = false;
        IsActionPending = true;

        try
        {
            switch (action)
            {
                case "START":
                    ScannerState = "STARTING";
                    ScannerStateColor = "#FFD600";
                    var startRes = await _apiClient.StartScannerAsync(_cts?.Token ?? default);
                    if (startRes.IsSuccess)
                    {
                        ActionFeedback = $"SUCCESS: {startRes.Value?.Message ?? "Background scanner resumed."}";
                    }
                    else
                    {
                        ActionFeedback = $"ERROR STARTING SCANNER: {startRes.ErrorMessage}";
                    }
                    break;

                case "STOP":
                    ScannerState = "STOPPING";
                    ScannerStateColor = "#FFD600";
                    var stopRes = await _apiClient.StopScannerAsync(_cts?.Token ?? default);
                    if (stopRes.IsSuccess)
                    {
                        ActionFeedback = $"SUCCESS: {stopRes.Value?.Message ?? "Background scanner paused."}";
                    }
                    else
                    {
                        ActionFeedback = $"ERROR STOPPING SCANNER: {stopRes.ErrorMessage}";
                    }
                    break;

                case "SINGLE_SCAN":
                    ActionFeedback = "Executing single 10 -> 3 -> 1 Groq AI cycle in PAPER mode...";
                    var scanRes = await _apiClient.TriggerSingleScanAsync(_cts?.Token ?? default);
                    if (scanRes.IsSuccess)
                    {
                        ActionFeedback = "SUCCESS: Single AI Funnel cycle completed in PAPER mode. Telemetry updated.";
                    }
                    else
                    {
                        ActionFeedback = $"ERROR TRIGGERING SCAN CYCLE: {scanRes.ErrorMessage}";
                    }
                    break;

                case "REAUTH":
                    var reauthRes = await _apiClient.ReauthorizeSessionAsync("OPERATOR", _cts?.Token ?? default);
                    if (reauthRes.IsSuccess)
                    {
                        ActionFeedback = "SUCCESS: Live session reauthorized and active.";
                    }
                    else
                    {
                        ActionFeedback = $"REAUTHORIZATION FAILED: {reauthRes.ErrorMessage}";
                    }
                    break;
            }
        }
        catch (Exception ex)
        {
            ActionFeedback = $"Action execution failed: {ex.Message}";
        }
        finally
        {
            await RefreshScreeningLogAsync();
            IsActionPending = false;
        }
    }

    [RelayCommand]
    public async Task RefreshScreeningLogAsync()
    {
        if (IsLoading) return;
        IsLoading = true;

        _cts?.Cancel();
        _cts?.Dispose();
        _cts = new CancellationTokenSource();

        try
        {
            // 1. Fetch live session state & cycle telemetry
            var sessionRes = await _apiClient.GetLiveSessionAsync(_cts.Token);
            if (sessionRes.IsSuccess && sessionRes.Value?.Data != null)
            {
                var data = sessionRes.Value.Data;
                SessionState = data.State;
                SessionStateColor = GetSessionStateColor(data.State);
                AuthorizedBy = string.IsNullOrWhiteSpace(data.AuthorizedBy) ? "NONE" : data.AuthorizedBy;
                LastScanTimeUtc = !string.IsNullOrWhiteSpace(data.LastScanUtc)
                    ? (DateTimeOffset.TryParse(data.LastScanUtc, out var sdt) ? sdt.UtcDateTime.ToString("HH:mm:ss") + " UTC" : data.LastScanUtc)
                    : "--:--:--";
                LastStatusMessage = data.LastStatusMessage ?? "No status message";
                RecoveryReason = data.RecoveryReason ?? string.Empty;

                if (data.CycleTelemetry != null)
                {
                    CurrentCycleStage = data.CycleTelemetry.CurrentStage ?? "IDLE";
                    NextCycleDueUtc = !string.IsNullOrWhiteSpace(data.CycleTelemetry.NextCycleDueUtc)
                        ? (DateTimeOffset.TryParse(data.CycleTelemetry.NextCycleDueUtc, out var ndt) ? ndt.UtcDateTime.ToString("HH:mm:ss") + " UTC" : data.CycleTelemetry.NextCycleDueUtc)
                        : "--:--:--";
                }

                // Resolve accurate ScannerState
                if (data.State.Equals("REAUTHORIZATION_REQUIRED", StringComparison.OrdinalIgnoreCase))
                {
                    ScannerState = "AUTH_REQUIRED";
                    ScannerStateColor = "#FF9100";
                }
                else if (!string.IsNullOrWhiteSpace(data.RecoveryReason))
                {
                    ScannerState = "ERROR";
                    ScannerStateColor = "#FF5252";
                }
                else if (data.ScannerActive || data.IsActive)
                {
                    ScannerState = "RUNNING";
                    ScannerStateColor = "#00E676";
                }
                else if (data.IsPaused || data.State.Equals("PAUSED", StringComparison.OrdinalIgnoreCase) || !data.ScannerActive)
                {
                    ScannerState = "STOPPED";
                    ScannerStateColor = "#8B949E";
                }
                else
                {
                    ScannerState = data.State;
                    ScannerStateColor = "#8B949E";
                }
            }
            else if (!sessionRes.IsSuccess)
            {
                ScannerState = "UNAVAILABLE";
                ScannerStateColor = "#FF5252";
            }

            // 2. Fetch screening history & candidates
            var res = await _apiClient.GetScreeningLogAsync(_cts.Token);
            if (res.IsSuccess && res.Value != null)
            {
                LastSuccessfulUpdateTimeUtc = DateTime.UtcNow;
                LastSuccessfulUpdateTimeString = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";
                FreshnessStatus = "LIVE";

                if (res.Value.ActiveCycle != null && !string.IsNullOrWhiteSpace(res.Value.ActiveCycle.CycleId))
                {
                    ActiveCycleId = res.Value.ActiveCycle.CycleId;
                }

                if (res.Value.History.Count > 0)
                {
                    CycleHistory.Clear();
                    foreach (var cycle in res.Value.History)
                    {
                        CycleHistory.Add(cycle);
                    }
                }

                var primaryCycle = res.Value.History.Count > 0 ? res.Value.History[0] : res.Value.Latest;
                if (primaryCycle != null)
                {
                    SelectedCycle = primaryCycle;
                    ApplyCycleData(primaryCycle);

                    LatestSelectedSymbol = !string.IsNullOrWhiteSpace(primaryCycle.Stage3?.Symbol)
                        ? primaryCycle.Stage3.Symbol
                        : (!string.IsNullOrWhiteSpace(primaryCycle.Stage3SelectedSymbol) ? primaryCycle.Stage3SelectedSymbol : "NONE (VETOED)");

                    LatestDecisionReason = primaryCycle.Stage3?.Reasoning 
                        ?? primaryCycle.DecisionReason 
                        ?? "Cycle evaluated without candidate execution.";

                    LatestTimestampUtc = primaryCycle.DisplayTimestamp;

                    DataProvenance = $"LIVE /api/autotrader/screening-log (Synced {LastSuccessfulUpdateTimeString})";
                }
                else
                {
                    // Valid empty screening state (scanner initialized but awaiting first candidate cycle)
                    DataProvenance = $"LIVE /api/autotrader/screening-log (Awaiting initial cycle; synced {LastSuccessfulUpdateTimeString})";
                }
            }
            else
            {
                FreshnessStatus = "STALE";
                string lastSyncNote = LastSuccessfulUpdateTimeUtc.HasValue
                    ? $"Last successful sync: {LastSuccessfulUpdateTimeString}"
                    : "No prior sync";
                DataProvenance = $"STALE ({lastSyncNote} | Reason: {res.ErrorMessage ?? "Unknown API failure"})";
            }
        }
        catch (OperationCanceledException)
        {
            // Ignored on cooperative cancellation
        }
        catch (Exception ex)
        {
            FreshnessStatus = "STALE";
            string lastSyncNote = LastSuccessfulUpdateTimeUtc.HasValue
                ? $"Last successful sync: {LastSuccessfulUpdateTimeString}"
                : "No prior sync";
            DataProvenance = $"STALE ({lastSyncNote} | Error: {ex.Message})";
        }
        finally
        {
            IsLoading = false;
        }
    }

    private static string GetSessionStateColor(string state) => state.ToUpperInvariant() switch
    {
        "ACTIVE" => "#00E676",
        "PAUSED" => "#FFB300",
        "REAUTHORIZATION_REQUIRED" => "#FF9100",
        "HALTED" => "#8B949E",
        "KILL_SWITCH_HALTED" => "#FF5252",
        "RECOVERY_REQUIRED" => "#FF5252",
        _ => "#8B949E"
    };

    private void ApplyCycleData(ScreeningCycleDto cycle)
    {
        if (cycle.MarketRegime != null && !string.IsNullOrWhiteSpace(cycle.MarketRegime.RegimeLabel))
        {
            MarketRegimeLabel = cycle.MarketRegime.RegimeLabel;
        }

        Top10Candidates.Clear();
        if (cycle.Stage1?.Top10 != null)
        {
            foreach (var c in cycle.Stage1.Top10)
            {
                Top10Candidates.Add(c);
            }
        }

        Top3Candidates.Clear();
        if (cycle.Stage2?.Top3 != null)
        {
            foreach (var c in cycle.Stage2.Top3)
            {
                Top3Candidates.Add(c);
            }
        }

        FinalSetup = cycle.Stage3;
    }
}
