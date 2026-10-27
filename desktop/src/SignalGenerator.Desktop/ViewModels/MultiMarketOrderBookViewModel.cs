using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class MultiMarketOrderBookViewModel : ViewModelBase
{
    private readonly IApiClient _apiClient;
    private readonly IPaperTradingService _paperTradingService;
    private CancellationTokenSource? _pollingCts;

    public ObservableCollection<MarketBookTileModel> MarketTiles { get; } = new();

    public ObservableCollection<string> AvailableSymbols { get; } = new()
    {
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LINKUSDT", "DOGEUSDT", "AVAXUSDT", "ASTERUSDT"
    };

    [ObservableProperty]
    private bool _isRefreshing;

    [ObservableProperty]
    private string _lastGlobalSyncText = "--:--:--";

    [ObservableProperty]
    private string _globalFeedStatus = "SYNCED";

    // Callbacks to MainViewModel for inter-view coordination
    public Action<string>? OnNavigateToTradingWorkstation { get; set; }
    public Action<decimal>? OnPrefillLimitPrice { get; set; }

    public MultiMarketOrderBookViewModel(IApiClient apiClient, IPaperTradingService paperTradingService)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _paperTradingService = paperTradingService ?? throw new ArgumentNullException(nameof(paperTradingService));

        InitializeTiles();
    }

    private void InitializeTiles()
    {
        var defaultSymbols = new[]
        {
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
            "XRPUSDT", "LINKUSDT", "DOGEUSDT", "AVAXUSDT"
        };

        MarketTiles.Clear();
        foreach (var sym in defaultSymbols)
        {
            var tile = new MarketBookTileModel
            {
                Symbol = sym,
                MarketType = "PERP",
                ConnectionState = "LIVE",
                LastPrice = GetDefaultInitialPrice(sym)
            };
            GenerateInitialLadder(tile);
            MarketTiles.Add(tile);
        }
    }

    private decimal GetDefaultInitialPrice(string symbol) => symbol.ToUpperInvariant() switch
    {
        "BTCUSDT" => 80685.50m,
        "ETHUSDT" => 2345.60m,
        "SOLUSDT" => 101.10m,
        "BNBUSDT" => 580.40m,
        "XRPUSDT" => 1.3225m,
        "LINKUSDT" => 14.50m,
        "DOGEUSDT" => 0.1650m,
        "AVAXUSDT" => 28.20m,
        _ => 10.00m
    };

    private void GenerateInitialLadder(MarketBookTileModel tile)
    {
        tile.Bids.Clear();
        tile.Asks.Clear();

        decimal px = tile.LastPrice;
        decimal tick = px > 100 ? 0.50m : px > 1 ? 0.01m : 0.0005m;

        tile.BestBid = px - tick;
        tile.BestAsk = px + tick;

        decimal bidCum = 0m;
        for (int i = 0; i < 5; i++)
        {
            decimal p = tile.BestBid - (i * tick);
            decimal sz = px > 1000 ? 0.25m * (i + 1) : 25m * (i + 1);
            decimal notional = p * sz;
            bidCum += notional;
            tile.Bids.Add(new OrderBookLevelItem
            {
                Price = p,
                Size = sz,
                CumulativeNotional = bidCum,
                DepthPct = Math.Min(100, 20 + i * 15),
                IsWall = i == 3 // Simulated wall at level 4
            });
        }

        decimal askCum = 0m;
        for (int i = 0; i < 5; i++)
        {
            decimal p = tile.BestAsk + (i * tick);
            decimal sz = px > 1000 ? 0.30m * (i + 1) : 30m * (i + 1);
            decimal notional = p * sz;
            askCum += notional;
            tile.Asks.Add(new OrderBookLevelItem
            {
                Price = p,
                Size = sz,
                CumulativeNotional = askCum,
                DepthPct = Math.Min(100, 25 + i * 15),
                IsWall = i == 2 // Simulated wall at level 3
            });
        }

        tile.BidDepthNotional = bidCum;
        tile.AskDepthNotional = askCum;
        tile.MajorBidWallPrice = tile.Bids.FirstOrDefault(b => b.IsWall)?.Price ?? tile.BestBid;
        tile.MajorBidWallNotional = tile.Bids.FirstOrDefault(b => b.IsWall)?.Notional ?? 0m;
        tile.MajorAskWallPrice = tile.Asks.FirstOrDefault(a => a.IsWall)?.Price ?? tile.BestAsk;
        tile.MajorAskWallNotional = tile.Asks.FirstOrDefault(a => a.IsWall)?.Notional ?? 0m;
        tile.LastUpdatedText = DateTime.UtcNow.ToString("HH:mm:ss");
    }

    [RelayCommand]
    public async Task RefreshAllTilesAsync()
    {
        if (IsRefreshing) return;

        try
        {
            IsRefreshing = true;
            _pollingCts?.Cancel();
            _pollingCts = new CancellationTokenSource();
            var ct = _pollingCts.Token;

            var tasks = MarketTiles.Select(tile => UpdateTileAsync(tile, ct)).ToList();
            await Task.WhenAll(tasks);

            LastGlobalSyncText = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";
            GlobalFeedStatus = "SYNCED";
        }
        catch (OperationCanceledException) { }
        catch
        {
            GlobalFeedStatus = "DEGRADED";
        }
        finally
        {
            IsRefreshing = false;
        }
    }

    private async Task UpdateTileAsync(MarketBookTileModel tile, CancellationToken ct)
    {
        try
        {
            var res = await _apiClient.GetModelBOrderBookAsync(tile.Symbol, ct);
            if (res.IsSuccess && res.Value?.Books != null && res.Value.Books.TryGetValue("asterdex", out var book))
            {
                if (book.BestBid.HasValue && book.BestAsk.HasValue)
                {
                    tile.BestBid = book.BestBid.Value;
                    tile.BestAsk = book.BestAsk.Value;
                    tile.LastPrice = book.MidPrice ?? tile.BestBid;
                    tile.ConnectionState = "LIVE";
                    tile.LastUpdatedText = DateTime.UtcNow.ToString("HH:mm:ss");

                    // Populate bids
                    tile.Bids.Clear();
                    decimal bidCum = 0m;
                    if (book.BidsTop5 != null && book.BidsTop5.Count > 0)
                    {
                        foreach (var b in book.BidsTop5)
                        {
                            decimal notional = b.Price * b.Size;
                            bidCum += notional;
                            tile.Bids.Add(new OrderBookLevelItem
                            {
                                Price = b.Price,
                                Size = b.Size,
                                CumulativeNotional = bidCum,
                                DepthPct = 50.0,
                                IsWall = notional > 50000m
                            });
                        }
                    }

                    // Populate asks
                    tile.Asks.Clear();
                    decimal askCum = 0m;
                    if (book.AsksTop5 != null && book.AsksTop5.Count > 0)
                    {
                        foreach (var a in book.AsksTop5)
                        {
                            decimal notional = a.Price * a.Size;
                            askCum += notional;
                            tile.Asks.Add(new OrderBookLevelItem
                            {
                                Price = a.Price,
                                Size = a.Size,
                                CumulativeNotional = askCum,
                                DepthPct = 50.0,
                                IsWall = notional > 50000m
                            });
                        }
                    }

                    tile.BidDepthNotional = bidCum;
                    tile.AskDepthNotional = askCum;
                    tile.MajorBidWallPrice = tile.Bids.FirstOrDefault(b => b.IsWall)?.Price ?? tile.BestBid;
                    tile.MajorBidWallNotional = tile.Bids.FirstOrDefault(b => b.IsWall)?.Notional ?? 0m;
                    tile.MajorAskWallPrice = tile.Asks.FirstOrDefault(a => a.IsWall)?.Price ?? tile.BestAsk;
                    tile.MajorAskWallNotional = tile.Asks.FirstOrDefault(a => a.IsWall)?.Notional ?? 0m;

                    // Update simulator reference prices
                    _paperTradingService.UpdateMarketPrices(tile.Symbol, tile.LastPrice, tile.BestBid, tile.BestAsk);
                }
            }
            else
            {
                tile.ConnectionState = "STALE";
            }
        }
        catch
        {
            tile.ConnectionState = "DISCONNECTED";
        }
    }

    [RelayCommand]
    public void OpenInTradingWorkstation(string symbol)
    {
        if (string.IsNullOrWhiteSpace(symbol)) return;
        OnNavigateToTradingWorkstation?.Invoke(symbol);
    }

    [RelayCommand]
    public void PrefillOrderPrice(decimal price)
    {
        if (price <= 0) return;
        OnPrefillLimitPrice?.Invoke(price);
    }

    [RelayCommand]
    public void ChangeSymbol(Tuple<MarketBookTileModel, string> tuple)
    {
        if (tuple?.Item1 == null || string.IsNullOrWhiteSpace(tuple.Item2)) return;
        tuple.Item1.Symbol = tuple.Item2;
        tuple.Item1.LastPrice = GetDefaultInitialPrice(tuple.Item2);
        GenerateInitialLadder(tuple.Item1);
        _ = UpdateTileAsync(tuple.Item1, CancellationToken.None);
    }
}
