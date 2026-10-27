using System;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class JournalViewModel : ViewModelBase
{
    private readonly IPollingCoordinator _pollingCoordinator;
    private readonly IApiClient _apiClient;

    [ObservableProperty]
    private string _mode = "LIVE";

    [ObservableProperty]
    private int _totalClosedTrades;

    [ObservableProperty]
    private int _winsCount;

    [ObservableProperty]
    private int _lossesCount;

    [ObservableProperty]
    private int _breakevenCount;

    [ObservableProperty]
    private decimal _winRatePct;

    [ObservableProperty]
    private decimal _profitFactor;

    [ObservableProperty]
    private decimal _averageRealizedR;

    [ObservableProperty]
    private decimal _totalGrossPnlUsd;

    [ObservableProperty]
    private decimal _totalNetPnlUsd;

    [ObservableProperty]
    private decimal _totalFeesPaidUsd;

    [ObservableProperty]
    private decimal _dailyLossUsageUsd;

    [ObservableProperty]
    private decimal _dailyLossLimitUsd;

    [ObservableProperty]
    private int _dailyFilledEntries;

    [ObservableProperty]
    private int _dailyEntriesAllowance;

    [ObservableProperty]
    private int _remainingEntries;

    [ObservableProperty]
    private bool _reconciliationOk = true;

    [ObservableProperty]
    private bool _isLoading;

    public JournalViewModel(IPollingCoordinator pollingCoordinator, IApiClient apiClient)
    {
        _pollingCoordinator = pollingCoordinator ?? throw new ArgumentNullException(nameof(pollingCoordinator));
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));

        _pollingCoordinator.OnJournalUpdated += HandleJournalUpdated;
    }

    private void HandleJournalUpdated(JournalSummaryDto summary)
    {
        if (summary == null) return;

        Mode = summary.Mode;
        TotalClosedTrades = summary.TotalClosedTrades;
        WinsCount = summary.WinsCount;
        LossesCount = summary.LossesCount;
        BreakevenCount = summary.BreakevenCount;
        WinRatePct = summary.WinRatePct;
        ProfitFactor = summary.ProfitFactor;
        AverageRealizedR = summary.AverageRealizedR;
        TotalGrossPnlUsd = summary.TotalGrossPnlUsd;
        TotalNetPnlUsd = summary.TotalNetPnlUsd;
        TotalFeesPaidUsd = summary.TotalFeesPaidUsd;
        DailyLossUsageUsd = summary.DailyLossUsageUsd;
        DailyLossLimitUsd = summary.DailyLossLimitUsd;
        DailyFilledEntries = summary.DailyFilledEntries;
        DailyEntriesAllowance = summary.DailyEntriesAllowance;
        RemainingEntries = summary.RemainingEntries;
        ReconciliationOk = summary.ReconciliationOk;
    }

    [RelayCommand]
    public async Task RefreshSummaryAsync()
    {
        if (IsLoading) return;
        IsLoading = true;
        try
        {
            var res = await _apiClient.GetJournalSummaryAsync();
            if (res.IsSuccess && res.Value != null)
            {
                HandleJournalUpdated(res.Value);
            }
        }
        finally
        {
            IsLoading = false;
        }
    }
}
