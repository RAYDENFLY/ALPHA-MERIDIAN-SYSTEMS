using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class GroqHunterViewModel : ViewModelBase
{
    private readonly IApiClient _apiClient;

    [ObservableProperty]
    private string _groqStatus = "ONLINE";

    [ObservableProperty]
    private string _modelName = "llama-3.3-70b-versatile";

    [ObservableProperty]
    private string _stage1PrefilterStatus = "ACTIVE (0-Token Local Heuristic)";

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
    private ObservableCollection<ScreeningCycleDto> _cycleHistory = new();

    [ObservableProperty]
    private bool _isLoading;

    public GroqHunterViewModel(IApiClient apiClient)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _ = RefreshScreeningLogAsync();
    }

    [RelayCommand]
    public async Task RefreshScreeningLogAsync()
    {
        if (IsLoading) return;
        IsLoading = true;

        try
        {
            var res = await _apiClient.GetScreeningLogAsync();
            if (res.IsSuccess && res.Value != null)
            {
                if (res.Value.ActiveCycle != null)
                {
                    ActiveCycleId = res.Value.ActiveCycle.CycleId;
                }

                if (res.Value.Latest != null)
                {
                    LatestSelectedSymbol = !string.IsNullOrWhiteSpace(res.Value.Latest.Stage3SelectedSymbol)
                        ? res.Value.Latest.Stage3SelectedSymbol
                        : "NONE (VETOED)";
                    LatestDecisionReason = res.Value.Latest.DecisionReason ?? "Cycle evaluated without candidate execution.";
                    LatestTimestampUtc = res.Value.Latest.TimestampUtc ?? DateTime.UtcNow.ToString("HH:mm:ss");
                }

                CycleHistory.Clear();
                foreach (var cycle in res.Value.History)
                {
                    CycleHistory.Add(cycle);
                }
            }
        }
        catch
        {
            // Fallback preserves existing state
        }
        finally
        {
            IsLoading = false;
        }
    }
}
