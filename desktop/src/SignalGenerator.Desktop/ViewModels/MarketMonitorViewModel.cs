using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Linq;
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
    private readonly List<MarketAssetTickerDto> _rawMarkets = new();

    // Mode Switcher: MONITOR (Real-time Market Matrix) or SCREENER (Ranked Opportunity Finder)
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsMonitorMode))]
    [NotifyPropertyChangedFor(nameof(IsScreenerMode))]
    private string _workspaceMode = "MONITOR";

    public bool IsMonitorMode => WorkspaceMode == "MONITOR";
    public bool IsScreenerMode => WorkspaceMode == "SCREENER";

    // Market Type Filter: ALL, SPOT, PERPETUAL
    [ObservableProperty]
    private string _selectedMarketType = "ALL";

    // Venue Filter: ALL, ASTERDEX, BINANCE, BYBIT, HYPERLIQUID
    [ObservableProperty]
    private string _selectedVenue = "ALL";

    [ObservableProperty]
    private string _searchFilter = string.Empty;

    [ObservableProperty]
    private string _sortBy = "volume_desc";

    [ObservableProperty]
    private ObservableCollection<MarketAssetTickerDto> _markets = new();

    [ObservableProperty]
    private MarketAssetTickerDto? _selectedMarket;

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

    [ObservableProperty]
    private string _emptyMessage = "NO INSTRUMENTS FOUND MATCHING CRITERIA";

    [ObservableProperty]
    private bool _isSpotUnavailable;

    [ObservableProperty]
    private string _spotUnavailableMessage = "SPOT MARKET DATA IS NOT PROVIDED BY THE CURRENT DATA SOURCE (ASTERDEX PERPETUAL & DERIVATIVES VENUES ONLY).";

    // Screener-specific pagination properties
    [ObservableProperty]
    private int _currentPage = 1;

    [ObservableProperty]
    private int _totalPages = 1;

    [ObservableProperty]
    private int _pageSize = 25;

    [ObservableProperty]
    private int _totalMatchedAssets;

    public MarketMonitorViewModel(IApiClient apiClient, IPollingCoordinator pollingCoordinator)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _pollingCoordinator = pollingCoordinator ?? throw new ArgumentNullException(nameof(pollingCoordinator));
    }

    [RelayCommand]
    public void SetMonitorMode()
    {
        WorkspaceMode = "MONITOR";
        ApplyFiltersAndSort();
    }

    [RelayCommand]
    public void SetScreenerMode()
    {
        WorkspaceMode = "SCREENER";
        _ = LoadMarketMatrixAsync();
    }

    [RelayCommand]
    public void SelectMarketType(string type)
    {
        SelectedMarketType = type.ToUpperInvariant();
        ApplyFiltersAndSort();
    }

    [RelayCommand]
    public void SelectVenue(string venue)
    {
        SelectedVenue = venue.ToUpperInvariant();
        if (IsScreenerMode)
        {
            CurrentPage = 1;
            _ = LoadMarketMatrixAsync();
        }
        else
        {
            ApplyFiltersAndSort();
        }
    }

    [RelayCommand]
    public void SetSort(string sortField)
    {
        SortBy = sortField;
        if (IsScreenerMode)
        {
            _ = LoadMarketMatrixAsync();
        }
        else
        {
            ApplyFiltersAndSort();
        }
    }

    partial void OnSearchFilterChanged(string value)
    {
        ApplyFiltersAndSort();
    }

    [RelayCommand]
    public async Task NextPageAsync()
    {
        if (CurrentPage < TotalPages)
        {
            CurrentPage++;
            await LoadMarketMatrixAsync();
        }
    }

    [RelayCommand]
    public async Task PrevPageAsync()
    {
        if (CurrentPage > 1)
        {
            CurrentPage--;
            await LoadMarketMatrixAsync();
        }
    }

    [RelayCommand]
    public async Task FirstPageAsync()
    {
        if (CurrentPage != 1)
        {
            CurrentPage = 1;
            await LoadMarketMatrixAsync();
        }
    }

    [RelayCommand]
    public async Task LoadMarketMatrixAsync()
    {
        if (IsLoading) return;
        IsLoading = true;
        ErrorMessage = null;

        try
        {
            string? exchangeParam = SelectedVenue != "ALL" ? SelectedVenue.ToLowerInvariant() : null;
            var res = await _apiClient.GetScreenerMarketsAsync(
                search: string.IsNullOrWhiteSpace(SearchFilter) ? null : SearchFilter.Trim(),
                page: IsScreenerMode ? CurrentPage : 1,
                pageSize: IsScreenerMode ? PageSize : 50,
                exchanges: exchangeParam,
                sortBy: SortBy);

            if (res.IsSuccess && res.Value != null)
            {
                _rawMarkets.Clear();
                _rawMarkets.AddRange(res.Value.Items);
                TotalMarketsCount = res.Value.TotalCount;
                TotalMatchedAssets = res.Value.TotalCount;
                TotalPages = Math.Max(1, res.Value.TotalPages);

                // Compute telemetry leaders
                ComputeMarketLeaders(_rawMarkets);
                LastRefreshUtc = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";

                ApplyFiltersAndSort();
            }
            else
            {
                ErrorMessage = res.ErrorMessage ?? "Failed to load market intelligence data.";
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

    private void ComputeMarketLeaders(IEnumerable<MarketAssetTickerDto> items)
    {
        decimal maxVol = 0;
        string topVolSym = "--";
        decimal maxChg = decimal.MinValue;
        string topChgSym = "--";

        foreach (var item in items)
        {
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

        HighestVolumeSymbol = topVolSym;
        HighestVolumeText = maxVol > 0 ? string.Create(CultureInfo.InvariantCulture, $"${maxVol:N0} USDT") : "--";
        TopGainerSymbol = topChgSym;
        TopGainerChange = maxChg != decimal.MinValue ? string.Create(CultureInfo.InvariantCulture, $"{maxChg:+0.00;-0.00}%") : "--";
    }

    public void ApplyFiltersAndSort()
    {
        Markets.Clear();
        IsSpotUnavailable = false;

        // Spot Market Verification Rule:
        // Do not infer spot solely from ticker suffix. Check actual instrument metadata.
        if (SelectedMarketType == "SPOT")
        {
            var spotItems = _rawMarkets.Where(m => m.InstrumentType.Equals("SPOT", StringComparison.OrdinalIgnoreCase)).ToList();
            if (spotItems.Count == 0)
            {
                IsSpotUnavailable = true;
                EmptyMessage = "NO SPOT INSTRUMENTS AVAILABLE ON CONNECTED VENUES.";
                return;
            }
        }

        IEnumerable<MarketAssetTickerDto> query = _rawMarkets;

        // Market type filter
        if (SelectedMarketType == "PERPETUAL")
        {
            query = query.Where(m => m.InstrumentType.Equals("PERPETUAL", StringComparison.OrdinalIgnoreCase));
        }

        // Venue filter (for local filtering if not handled on backend)
        if (SelectedVenue != "ALL")
        {
            var vKey = SelectedVenue.ToLowerInvariant();
            query = query.Where(m => m.Venues == null || m.Venues.ContainsKey(vKey));
        }

        // Search filter
        if (!string.IsNullOrWhiteSpace(SearchFilter))
        {
            var q = SearchFilter.Trim().ToUpperInvariant();
            query = query.Where(m => m.NormalizedSymbol.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                                     m.BaseAsset.Contains(q, StringComparison.OrdinalIgnoreCase));
        }

        // Sorting
        query = SortBy switch
        {
            "volume_desc" => query.OrderByDescending(m => m.TotalVolume24hUsdt),
            "volume_asc" => query.OrderBy(m => m.TotalVolume24hUsdt),
            "price_desc" => query.OrderByDescending(m => m.BenchmarkPrice ?? 0),
            "price_asc" => query.OrderBy(m => m.BenchmarkPrice ?? 0),
            "change_desc" => query.OrderByDescending(m => m.PriceChange24hPct ?? decimal.MinValue),
            "change_asc" => query.OrderBy(m => m.PriceChange24hPct ?? decimal.MaxValue),
            "spread_asc" => query.OrderBy(m => m.BestSpreadBps ?? decimal.MaxValue),
            "oi_desc" => query.OrderByDescending(m => m.TotalOpenInterestUsdt),
            _ => query.OrderByDescending(m => m.TotalVolume24hUsdt)
        };

        foreach (var item in query)
        {
            Markets.Add(item);
        }

        if (Markets.Count == 0)
        {
            EmptyMessage = !string.IsNullOrWhiteSpace(SearchFilter)
                ? $"NO INSTRUMENTS MATCHING '{SearchFilter.ToUpperInvariant()}'"
                : "NO ACTIVE MARKET INSTRUMENTS FOUND.";
        }
    }
}
