using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
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
    private readonly IPersonalJournalService _personalJournalService;

    [ObservableProperty]
    private int _selectedJournalTab = 0; // 0: AI Trades, 1: AI Calendar, 2: Personal Trades, 3: Personal Calendar

    #region AI Journal State
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
    private ObservableCollection<JournalTradeDto> _aiTrades = new();

    [ObservableProperty]
    private JournalTradeDto? _selectedAiTrade;

    [ObservableProperty]
    private string _aiCalendarMonthLabel = DateTime.UtcNow.ToString("MMMM yyyy", CultureInfo.InvariantCulture).ToUpperInvariant();

    [ObservableProperty]
    private decimal _aiMonthlyNetPnl;

    [ObservableProperty]
    private ObservableCollection<JournalTradeDto> _selectedDateAiTrades = new();

    [ObservableProperty]
    private string _selectedDateLabel = "SELECT A CALENDAR DATE";
    #endregion

    #region Personal Journal State
    [ObservableProperty]
    private ObservableCollection<PersonalTradeRecord> _personalTrades = new();

    [ObservableProperty]
    private PersonalTradeRecord? _selectedPersonalTrade;

    // Personal Trade Form
    [ObservableProperty]
    private string _inputSymbol = "BTCUSDT";

    [ObservableProperty]
    private string _inputDirection = "LONG";

    [ObservableProperty]
    private string _inputExchange = "Aster DEX";

    [ObservableProperty]
    private string _inputEntryPrice = "";

    [ObservableProperty]
    private string _inputExitPrice = "";

    [ObservableProperty]
    private string _inputQuantity = "";

    [ObservableProperty]
    private string _inputFees = "0.01";

    [ObservableProperty]
    private string _inputStrategy = "Manual SMC Reclaim";

    [ObservableProperty]
    private string _inputRationale = "";

    [ObservableProperty]
    private string _inputNotes = "";

    [ObservableProperty]
    private string _personalFormMessage = "";

    [ObservableProperty]
    private bool _showDeleteConfirmation;

    [ObservableProperty]
    private string? _deleteTargetTradeId;

    [ObservableProperty]
    private decimal _personalTotalNetPnl;

    [ObservableProperty]
    private decimal _personalMonthlyNetPnl;
    #endregion

    [ObservableProperty]
    private bool _isLoading;

    public JournalViewModel(IPollingCoordinator pollingCoordinator, IApiClient apiClient, IPersonalJournalService personalJournalService)
    {
        _pollingCoordinator = pollingCoordinator ?? throw new ArgumentNullException(nameof(pollingCoordinator));
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _personalJournalService = personalJournalService ?? throw new ArgumentNullException(nameof(personalJournalService));

        _pollingCoordinator.OnJournalUpdated += HandleJournalUpdated;

        _ = RefreshAllJournalsAsync();
    }

    [RelayCommand]
    public void SelectTab(int tabIndex)
    {
        SelectedJournalTab = tabIndex;
    }

    [RelayCommand]
    public async Task RefreshAllJournalsAsync()
    {
        if (IsLoading) return;
        IsLoading = true;

        try
        {
            await RefreshSummaryAsync();
            await RefreshAiTradesAsync();
            await RefreshPersonalTradesAsync();
        }
        finally
        {
            IsLoading = false;
        }
    }

    [RelayCommand]
    public async Task RefreshSummaryAsync()
    {
        try
        {
            var res = await _apiClient.GetJournalSummaryAsync();
            if (res.IsSuccess && res.Value != null)
            {
                HandleJournalUpdated(res.Value);
            }
        }
        catch { }
    }

    [RelayCommand]
    public async Task RefreshAiTradesAsync()
    {
        try
        {
            var res = await _apiClient.GetJournalTradesAsync(50);
            if (res.IsSuccess && res.Value != null)
            {
                AiTrades.Clear();
                decimal monthlySum = 0;
                var now = DateTime.UtcNow;

                foreach (var t in res.Value.Trades)
                {
                    AiTrades.Add(t);
                    if (t.IsClosed && t.ExitDateUtc.Year == now.Year && t.ExitDateUtc.Month == now.Month)
                    {
                        monthlySum += t.NetPnlUsd;
                    }
                }
                AiMonthlyNetPnl = monthlySum;
            }
        }
        catch { }
    }

    [RelayCommand]
    public async Task RefreshPersonalTradesAsync()
    {
        try
        {
            var trades = await _personalJournalService.GetAllTradesAsync();
            PersonalTrades.Clear();
            decimal totalNet = 0;
            var now = DateTime.UtcNow;

            foreach (var t in trades)
            {
                PersonalTrades.Add(t);
                if (t.NetPnl.HasValue)
                {
                    totalNet += t.NetPnl.Value;
                }
            }

            PersonalTotalNetPnl = totalNet;
            PersonalMonthlyNetPnl = await _personalJournalService.GetMonthlyNetPnlAsync(now.Year, now.Month);
        }
        catch { }
    }

    [RelayCommand]
    public async Task SavePersonalTradeAsync()
    {
        if (string.IsNullOrWhiteSpace(InputSymbol) || !decimal.TryParse(InputEntryPrice, NumberStyles.Any, CultureInfo.InvariantCulture, out var entryPrice))
        {
            PersonalFormMessage = "ERROR: Valid Symbol and Entry Price required.";
            return;
        }

        decimal.TryParse(InputExitPrice, NumberStyles.Any, CultureInfo.InvariantCulture, out var exitPriceVal);
        decimal? exitPrice = !string.IsNullOrWhiteSpace(InputExitPrice) ? exitPriceVal : null;

        decimal.TryParse(InputQuantity, NumberStyles.Any, CultureInfo.InvariantCulture, out var qty);
        if (qty <= 0) qty = 1.0m;

        decimal.TryParse(InputFees, NumberStyles.Any, CultureInfo.InvariantCulture, out var fees);

        var record = new PersonalTradeRecord
        {
            TradeId = Guid.NewGuid().ToString("N"),
            TradeDateUtc = DateTime.UtcNow,
            Symbol = InputSymbol.Trim().ToUpperInvariant(),
            Direction = InputDirection,
            Exchange = InputExchange,
            EntryPrice = entryPrice,
            ExitPrice = exitPrice,
            Quantity = qty,
            Fees = fees,
            Strategy = InputStrategy,
            EntryRationale = InputRationale,
            Notes = InputNotes
        };
        record.CalculatePnl();

        await _personalJournalService.SaveTradeAsync(record);
        PersonalFormMessage = $"SUCCESS: Saved personal trade {record.Symbol} ({record.Direction})";

        // Reset inputs
        InputEntryPrice = "";
        InputExitPrice = "";
        InputRationale = "";
        InputNotes = "";

        await RefreshPersonalTradesAsync();
    }

    [RelayCommand]
    public void RequestDeletePersonalTrade(string tradeId)
    {
        DeleteTargetTradeId = tradeId;
        ShowDeleteConfirmation = true;
    }

    [RelayCommand]
    public async Task ConfirmDeletePersonalTradeAsync()
    {
        if (!string.IsNullOrWhiteSpace(DeleteTargetTradeId))
        {
            await _personalJournalService.DeleteTradeAsync(DeleteTargetTradeId);
            DeleteTargetTradeId = null;
            ShowDeleteConfirmation = false;
            await RefreshPersonalTradesAsync();
        }
    }

    [RelayCommand]
    public void CancelDeletePersonalTrade()
    {
        DeleteTargetTradeId = null;
        ShowDeleteConfirmation = false;
    }

    [RelayCommand]
    public void SelectAiCalendarDate(DateTime date)
    {
        SelectedDateLabel = date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) + " TRADES (UTC)";
        SelectedDateAiTrades.Clear();

        foreach (var t in AiTrades)
        {
            if (t.ExitDateUtc.Date == date.Date)
            {
                SelectedDateAiTrades.Add(t);
            }
        }
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
}
