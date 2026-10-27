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

    public int FilteredMarketsCount => Markets.Count;

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

    // Mutually Exclusive UI State Discriminators
    public bool HasError => !string.IsNullOrEmpty(ErrorMessage);
    public bool IsDataMappingError => HasError && (ErrorMessage?.Contains("JSON", StringComparison.OrdinalIgnoreCase) == true ||
                                                   ErrorMessage?.Contains("deserializ", StringComparison.OrdinalIgnoreCase) == true ||
                                                   ErrorMessage?.Contains("format", StringComparison.OrdinalIgnoreCase) == true);
    public bool IsApiUnavailableError => HasError && !IsDataMappingError;
    public bool ShowSpotUnavailable => !IsLoading && !HasError && IsSpotUnavailable;
    public bool ShowGenericEmptyNotice => !IsLoading && !HasError && !IsSpotUnavailable && Markets.Count == 0;
    public bool ShowDataTable => !IsLoading && !HasError && !IsSpotUnavailable && Markets.Count > 0;

    public string ActiveFilterSummary => $"VENUE: {SelectedVenue} | TYPE: {SelectedMarketType}" +
                                         (!string.IsNullOrWhiteSpace(SearchFilter) ? $" | SEARCH: '{SearchFilter.Trim().ToUpperInvariant()}'" : "");

    // Dynamic Filter Button Styling (Active vs Inactive)
    public string TypeAllBackground => SelectedMarketType == "ALL" ? "#1F2836" : "#0A0D10";
    public string TypeAllForeground => SelectedMarketType == "ALL" ? "#FF9D00" : "#8B949E";
    public string TypeAllBorder => SelectedMarketType == "ALL" ? "#FF9D00" : "#242A34";

    public string TypePerpBackground => SelectedMarketType == "PERPETUAL" ? "#1F2836" : "#0A0D10";
    public string TypePerpForeground => SelectedMarketType == "PERPETUAL" ? "#448AFF" : "#8B949E";
    public string TypePerpBorder => SelectedMarketType == "PERPETUAL" ? "#448AFF" : "#242A34";

    public string TypeSpotBackground => SelectedMarketType == "SPOT" ? "#1F2836" : "#0A0D10";
    public string TypeSpotForeground => SelectedMarketType == "SPOT" ? "#FFB300" : "#8B949E";
    public string TypeSpotBorder => SelectedMarketType == "SPOT" ? "#FFB300" : "#242A34";

    public string VenueAllBackground => SelectedVenue == "ALL" ? "#1F2836" : "#0A0D10";
    public string VenueAllForeground => SelectedVenue == "ALL" ? "#FF9D00" : "#8B949E";
    public string VenueAllBorder => SelectedVenue == "ALL" ? "#FF9D00" : "#242A34";

    public string VenueAsterBackground => SelectedVenue == "ASTERDEX" ? "#1F2836" : "#0A0D10";
    public string VenueAsterForeground => SelectedVenue == "ASTERDEX" ? "#00E676" : "#8B949E";
    public string VenueAsterBorder => SelectedVenue == "ASTERDEX" ? "#00E676" : "#242A34";

    public string VenueBinanceBackground => SelectedVenue == "BINANCE" ? "#1F2836" : "#0A0D10";
    public string VenueBinanceForeground => SelectedVenue == "BINANCE" ? "#F0B90B" : "#8B949E";
    public string VenueBinanceBorder => SelectedVenue == "BINANCE" ? "#F0B90B" : "#242A34";

    public string VenueBybitBackground => SelectedVenue == "BYBIT" ? "#1F2836" : "#0A0D10";
    public string VenueBybitForeground => SelectedVenue == "BYBIT" ? "#F7A600" : "#8B949E";
    public string VenueBybitBorder => SelectedVenue == "BYBIT" ? "#F7A600" : "#242A34";

    public string VenueHyperliquidBackground => SelectedVenue == "HYPERLIQUID" ? "#1F2836" : "#0A0D10";
    public string VenueHyperliquidForeground => SelectedVenue == "HYPERLIQUID" ? "#50E3C2" : "#8B949E";
    public string VenueHyperliquidBorder => SelectedVenue == "HYPERLIQUID" ? "#50E3C2" : "#242A34";

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
    public void ResetFilters()
    {
        SelectedMarketType = "ALL";
        SelectedVenue = "ALL";
        SearchFilter = string.Empty;
        ApplyFiltersAndSort();
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

    partial void OnIsLoadingChanged(bool value)
    {
        NotifyFilterStateChanged();
    }

    partial void OnErrorMessageChanged(string? value)
    {
        NotifyFilterStateChanged();
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

    public void SeedMarkets(IEnumerable<MarketAssetTickerDto> items, int totalCount = 0)
    {
        _rawMarkets.Clear();
        _rawMarkets.AddRange(items);
        TotalMarketsCount = totalCount > 0 ? totalCount : _rawMarkets.Count;
        TotalMatchedAssets = TotalMarketsCount;
        ComputeMarketLeaders(_rawMarkets);
        LastRefreshUtc = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";
        ApplyFiltersAndSort();
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

    public static string GetSpotUnavailableExplanation(string venue)
    {
        return venue.ToUpperInvariant() switch
        {
            "ASTERDEX" => "SPOT MARKET DATA IS NOT PROVIDED: AsterDEX is an on-chain decentralized perpetual exchange on Arbitrum/Berachain. Spot trading is not supported by the AsterDEX protocol.",
            "BINANCE" => "SPOT MARKET DATA IS NOT PROVIDED: Binance USD-M Futures adapter is active (fapi.binance.com). Binance Spot market feeds (api.binance.com) are not configured in the screener engine.",
            "BYBIT" => "SPOT MARKET DATA IS NOT PROVIDED: Bybit Linear Perpetuals adapter is active (category=linear). Bybit Spot market feeds (category=spot) are not configured in the screener engine.",
            "HYPERLIQUID" => "SPOT MARKET DATA IS NOT PROVIDED: Hyperliquid Perpetuals adapter is active (metaAndAssetCtxs). Hyperliquid Spot order books (spotMetaAndAssetCtxs) are not queried by the screener adapter.",
            _ => "SPOT MARKET DATA IS NOT PROVIDED BY CONNECTED VENUES: Connected exchange adapters (AsterDEX, Binance, Bybit, Hyperliquid) provide perpetual futures exclusively. No spot market feeds are configured."
        };
    }

    public void ApplyFiltersAndSort()
    {
        Markets.Clear();
        IsSpotUnavailable = false;

        // Scope to venue if specific venue chosen
        IEnumerable<MarketAssetTickerDto> venueScoped = _rawMarkets;
        if (SelectedVenue != "ALL")
        {
            var vKey = SelectedVenue.ToLowerInvariant();
            venueScoped = venueScoped.Where(m => m.Venues != null && m.Venues.Keys.Any(k => k.Equals(vKey, StringComparison.OrdinalIgnoreCase)));
        }

        // Spot Market Verification Rule:
        // Do not infer spot solely from ticker suffix. Check actual instrument metadata.
        if (SelectedMarketType == "SPOT")
        {
            var spotItems = venueScoped.Where(m => m.InstrumentType.Equals("SPOT", StringComparison.OrdinalIgnoreCase)).ToList();
            if (spotItems.Count == 0)
            {
                IsSpotUnavailable = true;
                SpotUnavailableMessage = GetSpotUnavailableExplanation(SelectedVenue);
                EmptyMessage = $"NO SPOT INSTRUMENTS AVAILABLE ON {(SelectedVenue == "ALL" ? "CONNECTED VENUES" : SelectedVenue)}.";
                NotifyFilterStateChanged();
                return;
            }
        }

        IEnumerable<MarketAssetTickerDto> query = venueScoped;

        // Market type filter
        if (SelectedMarketType == "SPOT")
        {
            query = query.Where(m => m.InstrumentType.Equals("SPOT", StringComparison.OrdinalIgnoreCase));
        }
        else if (SelectedMarketType == "PERPETUAL")
        {
            query = query.Where(m => m.InstrumentType.Equals("PERPETUAL", StringComparison.OrdinalIgnoreCase));
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
                : $"NO RESULTS MATCH ACTIVE FILTERS (VENUE: {SelectedVenue}, TYPE: {SelectedMarketType}).";
        }

        NotifyFilterStateChanged();
    }

    private void NotifyFilterStateChanged()
    {
        OnPropertyChanged(nameof(HasError));
        OnPropertyChanged(nameof(IsDataMappingError));
        OnPropertyChanged(nameof(IsApiUnavailableError));
        OnPropertyChanged(nameof(ShowSpotUnavailable));
        OnPropertyChanged(nameof(ShowGenericEmptyNotice));
        OnPropertyChanged(nameof(ShowDataTable));
        OnPropertyChanged(nameof(ActiveFilterSummary));
        OnPropertyChanged(nameof(FilteredMarketsCount));

        OnPropertyChanged(nameof(TypeAllBackground));
        OnPropertyChanged(nameof(TypeAllForeground));
        OnPropertyChanged(nameof(TypeAllBorder));
        OnPropertyChanged(nameof(TypePerpBackground));
        OnPropertyChanged(nameof(TypePerpForeground));
        OnPropertyChanged(nameof(TypePerpBorder));
        OnPropertyChanged(nameof(TypeSpotBackground));
        OnPropertyChanged(nameof(TypeSpotForeground));
        OnPropertyChanged(nameof(TypeSpotBorder));

        OnPropertyChanged(nameof(VenueAllBackground));
        OnPropertyChanged(nameof(VenueAllForeground));
        OnPropertyChanged(nameof(VenueAllBorder));
        OnPropertyChanged(nameof(VenueAsterBackground));
        OnPropertyChanged(nameof(VenueAsterForeground));
        OnPropertyChanged(nameof(VenueAsterBorder));
        OnPropertyChanged(nameof(VenueBinanceBackground));
        OnPropertyChanged(nameof(VenueBinanceForeground));
        OnPropertyChanged(nameof(VenueBinanceBorder));
        OnPropertyChanged(nameof(VenueBybitBackground));
        OnPropertyChanged(nameof(VenueBybitForeground));
        OnPropertyChanged(nameof(VenueBybitBorder));
        OnPropertyChanged(nameof(VenueHyperliquidBackground));
        OnPropertyChanged(nameof(VenueHyperliquidForeground));
        OnPropertyChanged(nameof(VenueHyperliquidBorder));
    }
}
