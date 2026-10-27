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

public partial class MarketMonitorViewModel : ViewModelBase
{
    private readonly IApiClient _apiClient;
    private readonly IPollingCoordinator _pollingCoordinator;
    private readonly List<AlertItemDto> _allAlerts = new();

    [ObservableProperty]
    private ObservableCollection<MarketAssetTickerDto> _markets = new();

    [ObservableProperty]
    private MarketAssetTickerDto? _selectedMarket;

    [ObservableProperty]
    private ObservableCollection<AlertItemDto> _alerts = new();

    [ObservableProperty]
    private bool _filterAlertsBySymbol = true;

    [ObservableProperty]
    private string _alertsHeaderTitle = "■ ACTIVE PRICE ALERTS";

    [ObservableProperty]
    private string _alertsEmptyMessage = "NO ACTIVE ALERTS CONFIGURED";

    [ObservableProperty]
    private string _searchFilter = string.Empty;

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private int _totalMarketsCount;

    [ObservableProperty]
    private string _topGainerSymbol = "--";

    [ObservableProperty]
    private string _topGainerChange = "--";

    [ObservableProperty]
    private string _highestVolumeSymbol = "--";

    [ObservableProperty]
    private string _highestVolumeText = "--";

    [ObservableProperty]
    private string _lastRefreshUtc = "--:--:--";

    [ObservableProperty]
    private string? _errorMessage;

    public MarketMonitorViewModel(IApiClient apiClient, IPollingCoordinator pollingCoordinator)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _pollingCoordinator = pollingCoordinator ?? throw new ArgumentNullException(nameof(pollingCoordinator));
    }

    partial void OnSelectedMarketChanged(MarketAssetTickerDto? value)
    {
        UpdateAlertsFilter();
    }

    partial void OnFilterAlertsBySymbolChanged(bool value)
    {
        UpdateAlertsFilter();
    }

    [RelayCommand]
    public void ToggleAlertFilter()
    {
        FilterAlertsBySymbol = !FilterAlertsBySymbol;
    }

    [RelayCommand]
    public void ClearSelectedMarket()
    {
        SelectedMarket = null;
    }

    [RelayCommand]
    public async Task LoadMarketMatrixAsync()
    {
        if (IsLoading) return;
        IsLoading = true;
        ErrorMessage = null;

        try
        {
            var marketTask = _apiClient.GetScreenerMarketsAsync(SearchFilter, page: 1, pageSize: 50);
            var alertsTask = _apiClient.GetAlertsAsync();

            await Task.WhenAll(marketTask, alertsTask);

            var res = await marketTask;
            if (res.IsSuccess && res.Value != null)
            {
                Markets.Clear();
                decimal maxVol = 0;
                string topVolSym = "--";
                decimal maxChg = decimal.MinValue;
                string topChgSym = "--";

                foreach (var item in res.Value.Items)
                {
                    Markets.Add(item);
                    if (item.TotalVolume24hUsdt > maxVol)
                    {
                        maxVol = item.TotalVolume24hUsdt;
                        topVolSym = item.NormalizedSymbol;
                    }
                    if (item.PriceChange24hPct.HasValue && item.PriceChange24hPct.Value > maxChg)
                    {
                        maxChg = item.PriceChange24hPct.Value;
                        topChgSym = item.NormalizedSymbol;
                    }
                }

                TotalMarketsCount = res.Value.TotalCount;
                HighestVolumeSymbol = topVolSym;
                HighestVolumeText = maxVol > 0 ? string.Create(CultureInfo.InvariantCulture, $"${maxVol:N0} USDT") : "--";
                TopGainerSymbol = topChgSym;
                TopGainerChange = maxChg != decimal.MinValue ? string.Create(CultureInfo.InvariantCulture, $"{maxChg:+0.00;-0.00}%") : "--";
                LastRefreshUtc = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";
            }
            else
            {
                ErrorMessage = res.ErrorMessage ?? "Failed to load market matrix.";
            }

            var alertRes = await alertsTask;
            if (alertRes.IsSuccess && alertRes.Value != null)
            {
                _allAlerts.Clear();
                _allAlerts.AddRange(alertRes.Value);
                UpdateAlertsFilter();
            }
        }
        catch (Exception ex)
        {
            ErrorMessage = ex.Message;
        }
        finally
        {
            IsLoading = false;
        }
    }

    public void UpdateAlertsFilter()
    {
        Alerts.Clear();

        if (SelectedMarket != null && FilterAlertsBySymbol)
        {
            var sym = SelectedMarket.NormalizedSymbol;
            foreach (var a in _allAlerts)
            {
                if (a.Symbol.Equals(sym, StringComparison.OrdinalIgnoreCase))
                {
                    Alerts.Add(a);
                }
            }

            AlertsHeaderTitle = $"■ PRICE ALERTS // {sym} ({Alerts.Count})";
            AlertsEmptyMessage = $"NO ACTIVE ALERTS FOR {sym}";
        }
        else
        {
            foreach (var a in _allAlerts)
            {
                Alerts.Add(a);
            }

            AlertsHeaderTitle = $"■ ALL ACTIVE ALERTS ({Alerts.Count})";
            AlertsEmptyMessage = "NO ACTIVE ALERTS CONFIGURED";
        }
    }
}
