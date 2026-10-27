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
    private string _freshnessStatus = "LIVE";

    [ObservableProperty]
    private string _dataProvenance = "LIVE /api/autotrader/screening-log";

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
    public async Task RefreshScreeningLogAsync()
    {
        if (IsLoading) return;
        IsLoading = true;

        _cts?.Cancel();
        _cts?.Dispose();
        _cts = new CancellationTokenSource();

        try
        {
            var res = await _apiClient.GetScreeningLogAsync(_cts.Token);
            if (res.IsSuccess && res.Value != null)
            {
                FreshnessStatus = "LIVE";
                DataProvenance = "LIVE /api/autotrader/screening-log";

                if (res.Value.ActiveCycle != null)
                {
                    ActiveCycleId = res.Value.ActiveCycle.CycleId;
                }

                CycleHistory.Clear();
                foreach (var cycle in res.Value.History)
                {
                    CycleHistory.Add(cycle);
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
                }
            }
            else
            {
                FreshnessStatus = "STALE";
                DataProvenance = "STALE (Last poll failed: " + (res.ErrorMessage ?? "Unknown") + ")";
            }
        }
        catch (OperationCanceledException)
        {
            // Ignored on cooperative cancellation
        }
        catch (Exception ex)
        {
            FreshnessStatus = "STALE";
            DataProvenance = $"ERROR: {ex.Message}";
        }
        finally
        {
            IsLoading = false;
        }
    }

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
