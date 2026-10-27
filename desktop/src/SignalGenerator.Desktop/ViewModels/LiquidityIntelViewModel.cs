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

public partial class LiquidityIntelViewModel : ViewModelBase
{
    private readonly IApiClient _apiClient;
    private CancellationTokenSource? _cts;

    [ObservableProperty]
    private string _symbol = "BTCUSDT";

    [ObservableProperty]
    private string _timeframe = "1h";

    [ObservableProperty]
    private ObservableCollection<string> _availableSymbols = new() { "BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT", "BZUSDT", "ASTERUSDT" };

    [ObservableProperty]
    private ObservableCollection<string> _availableTimeframes = new() { "15m", "1h", "4h", "1d" };

    [ObservableProperty]
    private string _status = "AVAILABLE";

    [ObservableProperty]
    private string _feedProvenance = "LIVE /api/chart/liquidity_layers";

    [ObservableProperty]
    private string _orderBookStatus = "AVAILABLE";

    [ObservableProperty]
    private string _spreadBpsText = "0.01 bps";

    [ObservableProperty]
    private decimal? _spreadBps;

    [ObservableProperty]
    private decimal? _midPrice;

    [ObservableProperty]
    private double? _orderBookAgeSeconds;

    [ObservableProperty]
    private string _bidDepthText = "$0 USDT";

    [ObservableProperty]
    private string _askDepthText = "$0 USDT";

    [ObservableProperty]
    private string _bidWallLevel = "--";

    [ObservableProperty]
    private string _askWallLevel = "--";

    [ObservableProperty]
    private string _orderBookAgeText = "0.0s";

    [ObservableProperty]
    private string _whaleEventSummary = "Order flow cross-exchange aggregation active. Bids/Asks monitored across Aster, Binance, Bybit.";

    [ObservableProperty]
    private ObservableCollection<KlineDto> _candles = new();

    [ObservableProperty]
    private ObservableCollection<StructuralLevelDto> _structuralLevels = new();

    [ObservableProperty]
    private ObservableCollection<OrderBookWallDto> _orderBookWalls = new();

    [ObservableProperty]
    private ObservableCollection<OrderBookBucketDto> _bidsBuckets = new();

    [ObservableProperty]
    private ObservableCollection<OrderBookBucketDto> _asksBuckets = new();

    [ObservableProperty]
    private ObservableCollection<SweepEventDto> _sweepEvents = new();

    [ObservableProperty]
    private OrderBookWallDto? _selectedWall;

    [ObservableProperty]
    private string _selectedWallDetails = "Select a liquidity wall from the table or chart to inspect order flow depth.";

    // Indicator & Overlay Toggles
    [ObservableProperty]
    private bool _showEma20 = true;

    [ObservableProperty]
    private bool _showEma50 = true;

    [ObservableProperty]
    private bool _showEma100 = true;

    [ObservableProperty]
    private bool _showEma200 = true;

    [ObservableProperty]
    private bool _showStochastic = true;

    [ObservableProperty]
    private bool _showBidWalls = true;

    [ObservableProperty]
    private bool _showAskWalls = true;

    [ObservableProperty]
    private string _statusColor = "#00E676";

    [ObservableProperty]
    private bool _isLoading;

    public LiquidityIntelViewModel(IApiClient apiClient)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _ = InitializeWorkstationAsync();
    }

    // Parameterless fallback for legacy designer
    public LiquidityIntelViewModel() : this(new ApiClient(new System.Net.Http.HttpClient(), "http://127.0.0.1:8000"))
    {
    }

    public async Task InitializeWorkstationAsync()
    {
        await LoadSymbolsAsync();
        await RefreshIntelAsync();
    }

    [RelayCommand]
    public async Task LoadSymbolsAsync()
    {
        try
        {
            var res = await _apiClient.GetChartSymbolsAsync();
            if (res.IsSuccess && res.Value?.Symbols != null && res.Value.Symbols.Count > 0)
            {
                AvailableSymbols.Clear();
                foreach (var s in res.Value.Symbols)
                {
                    AvailableSymbols.Add(s);
                }
                if (!AvailableSymbols.Contains(Symbol))
                {
                    Symbol = res.Value.DefaultSymbol ?? "BTCUSDT";
                }
            }
        }
        catch { }
    }

    [RelayCommand]
    public async Task SelectSymbolAsync(string newSymbol)
    {
        if (string.IsNullOrWhiteSpace(newSymbol) || newSymbol == Symbol) return;
        Symbol = newSymbol;
        await RefreshIntelAsync();
    }

    [RelayCommand]
    public async Task SelectTimeframeAsync(string newTf)
    {
        if (string.IsNullOrWhiteSpace(newTf) || newTf == Timeframe) return;
        Timeframe = newTf;
        await RefreshIntelAsync();
    }

    [RelayCommand]
    public void SelectWall(OrderBookWallDto? wall)
    {
        SelectedWall = wall;
        if (wall != null)
        {
            string sideText = wall.Side.ToUpperInvariant();
            string distStr = wall.DistanceBps.HasValue ? $"{wall.DistanceBps.Value.ToString("F1", CultureInfo.InvariantCulture)} bps" : "--";
            string venuesStr = wall.Venues != null && wall.Venues.Count > 0 ? string.Join(", ", wall.Venues) : (wall.Venue ?? "Combined");
            SelectedWallDetails = $"[{sideText} WALL SELECTED] Price: ${wall.Price.ToString("F4", CultureInfo.InvariantCulture)} | Depth: {wall.FormattedNotional} | Distance: {distStr} | Tier: {wall.ConfirmationTier ?? "STANDARD"} | Venues: {venuesStr}";
        }
        else
        {
            SelectedWallDetails = "Select a liquidity wall from the table or chart to inspect order flow depth.";
        }
    }

    [RelayCommand]
    public void ToggleEma20() => ShowEma20 = !ShowEma20;

    [RelayCommand]
    public void ToggleEma50() => ShowEma50 = !ShowEma50;

    [RelayCommand]
    public void ToggleEma100() => ShowEma100 = !ShowEma100;

    [RelayCommand]
    public void ToggleEma200() => ShowEma200 = !ShowEma200;

    [RelayCommand]
    public void ToggleStochastic() => ShowStochastic = !ShowStochastic;

    [RelayCommand]
    public void ToggleBidWalls() => ShowBidWalls = !ShowBidWalls;

    [RelayCommand]
    public void ToggleAskWalls() => ShowAskWalls = !ShowAskWalls;

    [RelayCommand]
    public async Task RefreshIntelAsync()
    {
        if (IsLoading) return;
        IsLoading = true;

        _cts?.Cancel();
        _cts?.Dispose();
        _cts = new CancellationTokenSource();

        try
        {
            var res = await _apiClient.GetLiquidityLayersAsync(Symbol, Timeframe, _cts.Token);
            if (res.IsSuccess && res.Value != null)
            {
                Status = res.Value.FeedStatus?.Status ?? "AVAILABLE";
                StatusColor = Status.ToUpperInvariant() switch
                {
                    "LIVE" => "#00E676",
                    "AVAILABLE" => "#00E676",
                    "STALE" => "#FFB300",
                    "UNAVAILABLE" => "#8B949E",
                    "ERROR" => "#FF5252",
                    _ => "#FF9D00"
                };
                FeedProvenance = $"LIVE /api/chart/liquidity_layers ({res.Value.FeedStatus?.Source ?? "AsterDEX + Combined L2"})";

                Candles.Clear();
                foreach (var c in res.Value.Candles)
                {
                    Candles.Add(c);
                }

                StructuralLevels.Clear();
                foreach (var lvl in res.Value.StructuralLevels)
                {
                    StructuralLevels.Add(lvl);
                }

                SweepEvents.Clear();
                foreach (var sw in res.Value.SweepEvents)
                {
                    SweepEvents.Add(sw);
                }

                var ob = res.Value.OrderBookOverlay;
                if (ob != null)
                {
                    OrderBookStatus = ob.Status;
                    SpreadBps = ob.SpreadBps;
                    MidPrice = ob.MidPrice;
                    OrderBookAgeSeconds = ob.AgeSeconds;

                    SpreadBpsText = (ob.SpreadBps ?? 0).ToString("F2", CultureInfo.InvariantCulture) + " bps";
                    BidDepthText = ob.TotalBidDepthUsdt.HasValue 
                        ? "$" + ob.TotalBidDepthUsdt.Value.ToString("N0", CultureInfo.InvariantCulture) 
                        : "--";
                    AskDepthText = ob.TotalAskDepthUsdt.HasValue 
                        ? "$" + ob.TotalAskDepthUsdt.Value.ToString("N0", CultureInfo.InvariantCulture) 
                        : "--";
                    OrderBookAgeText = ob.AgeSeconds.HasValue
                        ? ob.AgeSeconds.Value.ToString("F1", CultureInfo.InvariantCulture) + "s"
                        : "--";

                    OrderBookWalls.Clear();
                    OrderBookWallDto? topBid = null;
                    OrderBookWallDto? topAsk = null;

                    foreach (var w in ob.Walls)
                    {
                        OrderBookWalls.Add(w);
                        if (w.Side.Equals("BID", StringComparison.OrdinalIgnoreCase) && (topBid == null || w.NotionalUsdt > topBid.NotionalUsdt))
                        {
                            topBid = w;
                        }
                        else if (w.Side.Equals("ASK", StringComparison.OrdinalIgnoreCase) && (topAsk == null || w.NotionalUsdt > topAsk.NotionalUsdt))
                        {
                            topAsk = w;
                        }
                    }

                    BidWallLevel = topBid != null 
                        ? $"${topBid.Price:F4} (${topBid.NotionalUsdt:N0})" 
                        : "--";
                    AskWallLevel = topAsk != null 
                        ? $"${topAsk.Price:F4} (${topAsk.NotionalUsdt:N0})" 
                        : "--";

                    // Populate Cumulative Depth Buckets
                    BidsBuckets.Clear();
                    foreach (var b in ob.BidsBuckets)
                    {
                        BidsBuckets.Add(b);
                    }

                    AsksBuckets.Clear();
                    foreach (var a in ob.AsksBuckets)
                    {
                        AsksBuckets.Add(a);
                    }
                }
            }
            else
            {
                Status = "UNAVAILABLE";
                StatusColor = "#8B949E";
                FeedProvenance = "UNAVAILABLE: " + (res.ErrorMessage ?? "Unknown");
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            Status = "ERROR";
            StatusColor = "#FF5252";
            FeedProvenance = $"ERROR: {ex.Message}";
        }
        finally
        {
            IsLoading = false;
        }
    }
}
