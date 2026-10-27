using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class OverviewViewModel : ViewModelBase
{
    private readonly IPollingCoordinator _pollingCoordinator;
    private readonly IApiClient _apiClient;
    private readonly IFxRateService _fxRateService;
    private readonly IRssNewsService _rssNewsService;

    private DateTime? _lastAuxRefreshUtc;

    #region 1. Header & Account Status Observables

    [ObservableProperty]
    private string _mode = "UNVERIFIED";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(SessionStateColor))]
    private string _sessionState = "UNVERIFIED";

    [ObservableProperty]
    private string _sessionId = "--";

    [ObservableProperty]
    private bool _isLiveTradingLocked = true;

    [ObservableProperty]
    private string _entriesPlacedDisplay = "-- / --";

    [ObservableProperty]
    private int _dailyEntriesUsed = 0;

    [ObservableProperty]
    private int _dailyEntriesAllowance = 3;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(PreflightVerdictColor))]
    private string _preflightVerdict = "PENDING";

    [ObservableProperty]
    private string _globalLockState = "IDLE";

    [ObservableProperty]
    private string _globalLockSymbol = "--";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(AccountStatusColor))]
    private string _accountStatus = "UNAVAILABLE";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(TotalEquityDisplay))]
    [NotifyPropertyChangedFor(nameof(HasTotalEquity))]
    private decimal? _totalEquity;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(WalletBalanceDisplay))]
    private decimal? _walletBalance;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(AvailableBalanceDisplay))]
    private decimal? _availableBalance;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(UsedMarginDisplay))]
    private decimal? _usedMargin;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(UnrealizedPnlDisplay))]
    private decimal? _unrealizedPnl;

    [ObservableProperty]
    private int _openPositionsCount = 0;

    [ObservableProperty]
    private int _openOrdersCount = 0;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(FreshnessColor))]
    private string _freshnessStatus = "UNAVAILABLE";

    [ObservableProperty]
    private string _lastRefreshTimeDisplay = "--:--:-- UTC";

    [ObservableProperty]
    private bool _isAccountStale;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(DailyLossUsedDisplay))]
    [NotifyPropertyChangedFor(nameof(HasDailyLossMetrics))]
    private decimal? _dailyLossUsed;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(DailyLossLimitDisplay))]
    [NotifyPropertyChangedFor(nameof(HasDailyLossMetrics))]
    private decimal? _dailyLossLimit;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(DailyLossStatusColor))]
    private string _dailyLossStatus = "UNVERIFIED";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(EffectiveTradeRiskDisplay))]
    [NotifyPropertyChangedFor(nameof(EffectiveTradeRiskDisplayWithUnit))]
    private decimal? _effectiveTradeRisk;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanOpenPositionDisplay))]
    [NotifyPropertyChangedFor(nameof(CanOpenPositionColor))]
    private bool _canOpenPosition = false;

    [ObservableProperty]
    private string _blockReason = "--";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(ReconciliationStatusColor))]
    private string _reconciliationStatus = "UNVERIFIED";

    [ObservableProperty]
    private string _scannerState = "IDLE";

    [ObservableProperty]
    private string _scannerProcessStatus = "IDLE";

    [ObservableProperty]
    private int _scanCount = 0;

    [ObservableProperty]
    private string _activeStrategy = "15M_SWEEP_RECLAIM_SMC";

    [ObservableProperty]
    private string _lastScanTime = "--";

    [ObservableProperty]
    private string _lastStatusMessage = "Waiting for live session telemetry...";

    [ObservableProperty]
    private bool _isRefreshing;

    #endregion

    #region 2. Open Positions & Open Orders Collections

    [ObservableProperty]
    private ObservableCollection<OpenPositionDto> _openPositions = new();

    [ObservableProperty]
    private bool _hasOpenPositions;

    [ObservableProperty]
    private ObservableCollection<OpenOrderDto> _openOrders = new();

    [ObservableProperty]
    private bool _hasOpenOrders;

    public string OpenOrdersGapNotice => "API GAP NOTICE: Standalone unassociated limit orders endpoint (GET /api/account/orders) is unavailable in current backend REST API. Displaying verified protective TP/SL orders attached to open positions.";

    #endregion

    #region 3. Multi-Pair FX Monitor Observables

    [ObservableProperty]
    private ObservableCollection<string> _availableFxPairs = new()
    {
        "USD/IDR",
        "USD/JPY",
        "IDR/JPY",
        "EUR/USD",
        "GBP/USD",
        "AUD/USD"
    };

    [ObservableProperty]
    private string _selectedFxPair = "USD/IDR";

    public string FxMonitorTitle => $"■ {SelectedFxPair} FX MONITOR";

    [ObservableProperty]
    private FxHistoricalSeriesDto? _fxSeries;

    [ObservableProperty]
    private string _selectedFxPeriod = "1M";

    [ObservableProperty]
    private bool _isFxLoading;

    [ObservableProperty]
    private string? _fxErrorMessage;

    #endregion

    #region 4. Market Pulse Observables

    [ObservableProperty]
    private ObservableCollection<MarketPulseItemDto> _marketPulseItems = new();

    [ObservableProperty]
    private bool _isMarketPulseLoading;

    #endregion

    #region 5. AI Signal & Execution Monitor Observables

    [ObservableProperty]
    private ScreeningCycleDto? _latestScreeningCycle;

    [ObservableProperty]
    private bool _hasScreeningCycle;

    [ObservableProperty]
    private string _screeningTimestamp = "--:--:-- UTC";

    [ObservableProperty]
    private int _scannedPairsCount;

    [ObservableProperty]
    private ObservableCollection<Top10CandidateDto> _stage1Top10 = new();

    [ObservableProperty]
    private ObservableCollection<Top3CandidateDto> _stage2Top3 = new();

    [ObservableProperty]
    private Stage3ScreeningDto? _stage3Candidate;

    [ObservableProperty]
    private bool _hasStage3Candidate;

    [ObservableProperty]
    private string _aiRiskGateDecision = "AWAITING TELEMETRY";

    [ObservableProperty]
    private string _aiExecutionState = "INACTIVE";

    [ObservableProperty]
    private string _executionStateColor = "#8B949E";

    #endregion

    #region 6. Global News Observables

    [ObservableProperty]
    private ObservableCollection<NewsArticleDto> _recentNews = new();

    [ObservableProperty]
    private bool _isNewsLoading;

    #endregion

    #region 7. Recent Operational Activity Observables

    [ObservableProperty]
    private ObservableCollection<RecentOperationalActivityDto> _recentActivities = new();

    [ObservableProperty]
    private bool _hasRecentActivities;

    #endregion

    #region Display & Color Helpers

    public string TotalEquityDisplay => TotalEquity.HasValue ? $"${TotalEquity.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";
    public bool HasTotalEquity => TotalEquity.HasValue;

    public string AvailableBalanceDisplay => AvailableBalance.HasValue ? $"${AvailableBalance.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";
    public string UsedMarginDisplay => UsedMargin.HasValue ? $"${UsedMargin.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";
    public string WalletBalanceDisplay => WalletBalance.HasValue ? $"${WalletBalance.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";
    public string UnrealizedPnlDisplay => UnrealizedPnl.HasValue ? $"${UnrealizedPnl.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unavailable";

    public string DailyLossUsedDisplay => DailyLossUsed.HasValue ? $"${DailyLossUsed.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "--";
    public string DailyLossLimitDisplay => DailyLossLimit.HasValue ? $"${DailyLossLimit.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "--";
    public bool HasDailyLossMetrics => DailyLossUsed.HasValue && DailyLossLimit.HasValue;

    public string EffectiveTradeRiskDisplay => EffectiveTradeRisk.HasValue ? $"${EffectiveTradeRisk.Value.ToString("N2", CultureInfo.InvariantCulture)}" : "Unverified";
    public string EffectiveTradeRiskDisplayWithUnit => EffectiveTradeRisk.HasValue ? $"${EffectiveTradeRisk.Value.ToString("N2", CultureInfo.InvariantCulture)} USDT" : "Unverified";

    public string CanOpenPositionDisplay => CanOpenPosition ? "ALLOWED" : "DISALLOWED";
    public string CanOpenPositionColor => CanOpenPosition ? "#00E676" : "#FF5252";

    public string SessionStateColor => SessionState.ToUpperInvariant() switch
    {
        "ACTIVE" => "#00E676",
        "KILL_SWITCH_HALTED" or "HALTED" => "#FF5252",
        "PAUSED" => "#FFB300",
        "INACTIVE" => "#8B949E",
        _ => "#8B949E"
    };

    public string AccountStatusColor => AccountStatus.ToUpperInvariant() switch
    {
        "CONNECTED" => "#00E676",
        "STALE" or "STALE_ACCOUNT_DATA" => "#FFB300",
        "UNAUTHENTICATED" or "DISCONNECTED" or "ERROR" => "#FF5252",
        _ => "#8B949E"
    };

    public string FreshnessColor => FreshnessStatus.ToUpperInvariant() switch
    {
        var s when s.StartsWith("LIVE") => "#00E676",
        var s when s.StartsWith("STALE") => "#FFB300",
        _ => "#FF5252"
    };

    public string PreflightVerdictColor => PreflightVerdict.ToUpperInvariant() switch
    {
        "PASS" => "#00E676",
        "FAIL" => "#FF5252",
        "WARN" => "#FFB300",
        _ => "#8B949E"
    };

    public string DailyLossStatusColor => DailyLossStatus.ToUpperInvariant() switch
    {
        "VERIFIED" or "OK" => "#00E676",
        "LIMIT_EXCEEDED" => "#FF5252",
        _ => "#FFB300"
    };

    public string ReconciliationStatusColor => ReconciliationStatus.ToUpperInvariant() switch
    {
        "OK" or "SUCCESS" => "#00E676",
        "MISMATCH" or "OUT_OF_SYNC" or "ERROR" => "#FF5252",
        _ => "#FFB300"
    };

    #endregion

    public OverviewViewModel(
        IPollingCoordinator pollingCoordinator,
        IApiClient apiClient,
        IFxRateService fxRateService,
        IRssNewsService rssNewsService)
    {
        _pollingCoordinator = pollingCoordinator ?? throw new ArgumentNullException(nameof(pollingCoordinator));
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _fxRateService = fxRateService ?? throw new ArgumentNullException(nameof(fxRateService));
        _rssNewsService = rssNewsService ?? throw new ArgumentNullException(nameof(rssNewsService));

        _pollingCoordinator.OnStatusUpdated += HandleStatusUpdated;
        _pollingCoordinator.OnLiveSessionUpdated += HandleLiveSessionUpdated;

        _ = InitialLoadAuxiliaryAsync();
    }

    /// <summary>
    /// Design-time fallback constructor for Avalonia XAML previews and standalone tests.
    /// </summary>
    public OverviewViewModel()
        : this(
            new PollingCoordinator(new ApiClient(new System.Net.Http.HttpClient(), "http://127.0.0.1:8000"), new SettingsService()),
            new ApiClient(new System.Net.Http.HttpClient(), "http://127.0.0.1:8000"),
            new FxRateService(new System.Net.Http.HttpClient()),
            new RssNewsService(new System.Net.Http.HttpClient()))
    {
    }

    private async Task InitialLoadAuxiliaryAsync()
    {
        await LoadFxHistoricalAsync(SelectedFxPeriod);
        await RefreshMarketPulseAsync();
        await RefreshNewsAsync();
        await RefreshRecentActivityAsync();
    }

    [RelayCommand]
    public async Task RefreshNowAsync()
    {
        if (IsRefreshing) return;
        IsRefreshing = true;
        try
        {
            await _pollingCoordinator.TriggerPollNowAsync();
            await RefreshAllAuxiliaryAsync();
        }
        finally
        {
            IsRefreshing = false;
        }
    }

    partial void OnSelectedFxPairChanged(string value)
    {
        OnPropertyChanged(nameof(FxMonitorTitle));
        _ = LoadFxHistoricalAsync(SelectedFxPeriod);
    }

    [RelayCommand]
    public async Task SelectFxPairAsync(string pair)
    {
        if (string.IsNullOrWhiteSpace(pair) || pair == SelectedFxPair) return;
        SelectedFxPair = pair;
        OnPropertyChanged(nameof(FxMonitorTitle));
        await LoadFxHistoricalAsync(SelectedFxPeriod);
    }

    [RelayCommand]
    public async Task SelectFxPeriodAsync(string period)
    {
        if (string.IsNullOrWhiteSpace(period) || period == SelectedFxPeriod) return;
        SelectedFxPeriod = period;
        await LoadFxHistoricalAsync(period);
    }

    private async Task LoadFxHistoricalAsync(string period)
    {
        IsFxLoading = true;
        FxErrorMessage = null;
        try
        {
            var series = await _fxRateService.GetHistoricalSeriesAsync(SelectedFxPair, period);
            FxSeries = series;
            if (series.IsStale && !string.IsNullOrWhiteSpace(series.ErrorMessage))
            {
                FxErrorMessage = series.ErrorMessage;
            }
        }
        catch (Exception ex)
        {
            FxErrorMessage = $"Failed to load FX rate for {SelectedFxPair}: {ex.Message}";
        }
        finally
        {
            IsFxLoading = false;
        }
    }

    private async Task RefreshAllAuxiliaryAsync()
    {
        var now = DateTime.UtcNow;
        if (_lastAuxRefreshUtc.HasValue && (now - _lastAuxRefreshUtc.Value).TotalSeconds < 10)
        {
            return; // Throttle auxiliary requests
        }
        _lastAuxRefreshUtc = now;

        await Task.WhenAll(
            RefreshMarketPulseAsync(),
            RefreshNewsAsync(),
            RefreshRecentActivityAsync()
        );
    }

    private async Task RefreshMarketPulseAsync()
    {
        IsMarketPulseLoading = true;
        try
        {
            var result = await _apiClient.GetScreenerMarketsAsync(search: null, page: 1, pageSize: 25);
            if (result.IsSuccess && result.Value?.Items != null)
            {
                var targetSymbols = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
                {
                    "BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT"
                };

                var items = new List<MarketPulseItemDto>();
                foreach (var market in result.Value.Items)
                {
                    if (targetSymbols.Contains(market.NormalizedSymbol))
                    {
                        items.Add(new MarketPulseItemDto
                        {
                            Symbol = market.NormalizedSymbol,
                            Price = market.BenchmarkPrice ?? 0m,
                            Change24hPct = market.PriceChange24hPct ?? 0m,
                            Volume24hUsdt = market.TotalVolume24hUsdt,
                            OpenInterestUsdt = market.TotalOpenInterestUsdt,
                            FundingRatePct = null, // Venue funding rate mapped from detail or default
                            Venue = market.CrossListed ? "Multi-Venue" : "AsterDEX",
                            TimestampDisplay = DateTime.UtcNow.ToString("HH:mm:ss")
                        });
                    }
                }

                MarketPulseItems = new ObservableCollection<MarketPulseItemDto>(items);
            }
        }
        catch
        {
            // Graceful fallback: maintain existing items
        }
        finally
        {
            IsMarketPulseLoading = false;
        }
    }

    private async Task RefreshNewsAsync()
    {
        IsNewsLoading = true;
        try
        {
            var articles = await _rssNewsService.FetchLatestNewsAsync();
            var topArticles = articles.Take(5).ToList();
            RecentNews = new ObservableCollection<NewsArticleDto>(topArticles);
        }
        catch
        {
            // Graceful fallback
        }
        finally
        {
            IsNewsLoading = false;
        }
    }

    private async Task RefreshRecentActivityAsync()
    {
        try
        {
            var activities = new List<RecentOperationalActivityDto>();

            // 1. Fetch recent journal trades (verified AsterDEX ledger)
            var tradesRes = await _apiClient.GetJournalTradesAsync(limit: 5);
            if (tradesRes.IsSuccess && tradesRes.Value?.Trades != null)
            {
                foreach (var t in tradesRes.Value.Trades)
                {
                    activities.Add(new RecentOperationalActivityDto
                    {
                        TimestampDisplay = DateTimeOffset.TryParse(t.ExitTimestampUtc ?? t.FirstFillTimestampUtc ?? t.SignalTimestampUtc, out var dt) ? dt.UtcDateTime.ToString("HH:mm:ss") : "--:--:--",
                        Category = "JOURNAL_TRADE",
                        Symbol = t.Symbol,
                        Status = t.Status,
                        Explanation = $"{t.Side} filled @ {t.AverageEntryPrice:F4}. PnL: {t.FormattedNetPnl}",
                        Source = "AsterDEX Journal (/api/v1/journal/trades)"
                    });
                }
            }

            // 2. Fetch alerts
            var alertsRes = await _apiClient.GetAlertsAsync();
            if (alertsRes.IsSuccess && alertsRes.Value != null)
            {
                foreach (var a in alertsRes.Value.Take(3))
                {
                    activities.Add(new RecentOperationalActivityDto
                    {
                        TimestampDisplay = DateTimeOffset.TryParse(a.LastTriggeredAt ?? a.CreatedAt, out var dt) ? dt.UtcDateTime.ToString("HH:mm:ss") : "--:--:--",
                        Category = "PRICE_ALERT",
                        Symbol = a.Symbol,
                        Status = a.Status,
                        Explanation = !string.IsNullOrWhiteSpace(a.Note) ? a.Note : $"Target: {a.TargetPrice:F4} ({a.Direction})",
                        Source = "Alert Engine (/api/alerts)"
                    });
                }
            }

            if (activities.Count > 0)
            {
                RecentActivities = new ObservableCollection<RecentOperationalActivityDto>(activities.OrderByDescending(a => a.TimestampDisplay));
                HasRecentActivities = true;
            }
        }
        catch
        {
            // Non-critical background telemetry
        }
    }

    private void HandleStatusUpdated(SystemStatusDto status)
    {
        if (status == null) return;

        Mode = !string.IsNullOrWhiteSpace(status.Mode) ? status.Mode : "PAPER";
        SessionState = !string.IsNullOrWhiteSpace(status.SessionState) ? status.SessionState : "INACTIVE";
        SessionId = status.SessionId ?? "None";
        IsLiveTradingLocked = status.LiveTradingLocked;
        LastRefreshTimeDisplay = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";

        if (status.Account != null)
        {
            AccountStatus = !string.IsNullOrWhiteSpace(status.Account.Status)
                ? status.Account.Status
                : (status.Account.IsConnected ? "CONNECTED" : "UNAVAILABLE");

            TotalEquity = status.Account.TotalEquityUsdt;
            WalletBalance = status.Account.WalletBalanceUsdt;
            AvailableBalance = status.Account.AvailableBalanceUsdt;
            UsedMargin = status.Account.UsedMarginUsdt;
            UnrealizedPnl = status.Account.UnrealizedPnlUsdt;
            OpenPositionsCount = status.Account.OpenPositionsCount;

            IsAccountStale = status.Account.IsStale || (status.Account.LastSyncAgeSeconds.HasValue && status.Account.LastSyncAgeSeconds.Value > 120);
            FreshnessStatus = IsAccountStale ? $"STALE ({status.Account.LastSyncAgeSeconds ?? 120}s)" : "LIVE (SYNCED)";

            // Populate Open Positions
            if (status.Account.PositionsSummary != null && status.Account.PositionsSummary.Count > 0)
            {
                OpenPositions = new ObservableCollection<OpenPositionDto>(status.Account.PositionsSummary);
                HasOpenPositions = true;

                // Extract Open Orders (protective TP/SL brackets attached to open positions)
                var orders = new List<OpenOrderDto>();
                foreach (var pos in status.Account.PositionsSummary)
                {
                    string counterSide = pos.Side.Equals("LONG", StringComparison.OrdinalIgnoreCase) ? "SELL" : "BUY";

                    foreach (var tp in pos.TpOrders)
                    {
                        orders.Add(new OpenOrderDto
                        {
                            Symbol = pos.Symbol,
                            Side = counterSide,
                            OrderType = tp.Type,
                            Price = tp.Price,
                            OrigQty = tp.OrigQty,
                            RemainingQty = tp.OrigQty,
                            Status = "OPEN",
                            ReduceOnly = tp.ReduceOnly,
                            AssociatedPosition = $"{pos.Symbol} {pos.Side} (Take-Profit Target)",
                            TimestampUtc = pos.LastSyncUtc ?? "--"
                        });
                    }

                    foreach (var sl in pos.SlOrders)
                    {
                        orders.Add(new OpenOrderDto
                        {
                            Symbol = pos.Symbol,
                            Side = counterSide,
                            OrderType = sl.Type,
                            Price = sl.Price,
                            OrigQty = sl.OrigQty,
                            RemainingQty = sl.OrigQty,
                            Status = "OPEN",
                            ReduceOnly = sl.ReduceOnly,
                            AssociatedPosition = $"{pos.Symbol} {pos.Side} (Dynamic Structural SL)",
                            TimestampUtc = pos.LastSyncUtc ?? "--"
                        });
                    }
                }

                OpenOrders = new ObservableCollection<OpenOrderDto>(orders);
                HasOpenOrders = orders.Count > 0;
                OpenOrdersCount = orders.Count;
            }
            else
            {
                OpenPositions.Clear();
                HasOpenPositions = false;
                OpenOrders.Clear();
                HasOpenOrders = false;
                OpenOrdersCount = 0;
            }
        }
        else
        {
            AccountStatus = "UNAVAILABLE";
            TotalEquity = null;
            WalletBalance = null;
            AvailableBalance = null;
            UsedMargin = null;
            UnrealizedPnl = null;
            OpenPositionsCount = 0;
            OpenOrdersCount = 0;
            FreshnessStatus = "UNAVAILABLE";
            OpenPositions.Clear();
            HasOpenPositions = false;
            OpenOrders.Clear();
            HasOpenOrders = false;
        }

        if (status.RiskLimits != null)
        {
            DailyLossUsed = status.RiskLimits.DailyLossUsedUsdt;
            DailyLossLimit = status.RiskLimits.DailyLossLimitUsdt;
            DailyLossStatus = status.RiskLimits.DailyLossStatus ?? "UNVERIFIED";
            EffectiveTradeRisk = status.RiskLimits.EffectiveTradeRiskUsdt;
            CanOpenPosition = status.RiskLimits.CanOpenPosition;
            BlockReason = !string.IsNullOrWhiteSpace(status.RiskLimits.BlockReason)
                ? status.RiskLimits.BlockReason
                : (status.RiskLimits.CanOpenPosition ? "None" : "Position Open Disallowed");
            ReconciliationStatus = status.RiskLimits.ReconciliationStatus ?? "OK";

            if (status.RiskLimits.LockState != null)
            {
                GlobalLockState = status.RiskLimits.LockState;
            }
            if (status.RiskLimits.LockSymbol != null)
            {
                GlobalLockSymbol = status.RiskLimits.LockSymbol;
            }
        }
        else
        {
            DailyLossUsed = null;
            DailyLossLimit = null;
            DailyLossStatus = "UNVERIFIED";
            EffectiveTradeRisk = null;
            CanOpenPosition = false;
            BlockReason = "UNAVAILABLE";
            ReconciliationStatus = "UNAVAILABLE";
        }

        if (status.Scanner != null)
        {
            ScannerState = status.Scanner.State;
            ScannerProcessStatus = status.Scanner.ProcessStatus;
            ScanCount = status.Scanner.ScanCount;
            ActiveStrategy = status.Scanner.ConfiguredLiveStrategyId ?? "15M_SWEEP_RECLAIM_SMC";
            LastScanTime = status.Scanner.LastScanUtc ?? "--";
        }
    }

    private void HandleLiveSessionUpdated(LiveSessionEnvelopeDto envelope)
    {
        if (envelope == null) return;

        if (envelope.Preflight != null)
        {
            PreflightVerdict = envelope.Preflight.Verdict;
        }

        if (envelope.Data != null)
        {
            DailyEntriesUsed = envelope.Data.DailyFilledEntries;
            DailyEntriesAllowance = envelope.Data.EntryLimit;
            EntriesPlacedDisplay = $"{DailyEntriesUsed} / {DailyEntriesAllowance}";

            if (!string.IsNullOrWhiteSpace(envelope.Data.GlobalLockState))
            {
                GlobalLockState = envelope.Data.GlobalLockState;
            }
            if (!string.IsNullOrWhiteSpace(envelope.Data.GlobalLockSymbol))
            {
                GlobalLockSymbol = envelope.Data.GlobalLockSymbol;
            }

            if (!string.IsNullOrWhiteSpace(envelope.Data.LastStatusMessage))
            {
                LastStatusMessage = envelope.Data.LastStatusMessage;
            }

            // Bind AI Signal & Execution Monitor from live screening cycle
            if (envelope.Data.LatestScreening != null)
            {
                var cycle = envelope.Data.LatestScreening;
                LatestScreeningCycle = cycle;
                HasScreeningCycle = true;
                ScreeningTimestamp = DateTimeOffset.TryParse(cycle.TimestampUtc, out var sDt) ? sDt.UtcDateTime.ToString("HH:mm:ss") + " UTC" : "--:--:-- UTC";
                ScannedPairsCount = cycle.Stage1?.TotalScreenedPairs ?? 0;

                if (cycle.Stage1?.Top10 != null)
                {
                    Stage1Top10 = new ObservableCollection<Top10CandidateDto>(cycle.Stage1.Top10);
                }

                if (cycle.Stage2?.Top3 != null)
                {
                    Stage2Top3 = new ObservableCollection<Top3CandidateDto>(cycle.Stage2.Top3);
                }

                Stage3Candidate = cycle.Stage3;
                HasStage3Candidate = cycle.Stage3 != null && !string.IsNullOrWhiteSpace(cycle.Stage3.Symbol);

                AiRiskGateDecision = !string.IsNullOrWhiteSpace(cycle.DecisionReason) ? cycle.DecisionReason : cycle.Status;

                // Clarify AI proposal vs Exchange Execution State
                if (cycle.Status.Equals("APPROVED", StringComparison.OrdinalIgnoreCase))
                {
                    AiExecutionState = "APPROVED -> ORDER PLACED";
                    ExecutionStateColor = "#00E676";
                }
                else if (cycle.Status.Equals("REJECTED", StringComparison.OrdinalIgnoreCase))
                {
                    AiExecutionState = "PROPOSAL REJECTED -> NO ORDER";
                    ExecutionStateColor = "#FF5252";
                }
                else
                {
                    AiExecutionState = cycle.Status;
                    ExecutionStateColor = "#FFB300";
                }
            }

            // Append Protection Reconciler event to Operational Activity if available
            if (envelope.Data.ProtectionReconciliationWorker?.LastCycleResult != null)
            {
                var workerResult = envelope.Data.ProtectionReconciliationWorker.LastCycleResult;
                var reconcilerActivity = new RecentOperationalActivityDto
                {
                    TimestampDisplay = DateTimeOffset.TryParse(workerResult.TimestampUtc, out var dt) ? dt.UtcDateTime.ToString("HH:mm:ss") : "--:--:--",
                    Category = "PROTECTION_WORKER",
                    Symbol = envelope.Data.GlobalLockSymbol ?? "--",
                    Status = workerResult.Status,
                    Explanation = $"Reconciled {workerResult.ReconciledTrades} trades, {workerResult.NewFillsImported} new fills.",
                    Source = "LiveAutoSessionManager Protection Worker"
                };

                // Insert at front if not already present
                if (RecentActivities.Count == 0 || RecentActivities[0].Explanation != reconcilerActivity.Explanation)
                {
                    var updated = new List<RecentOperationalActivityDto> { reconcilerActivity };
                    updated.AddRange(RecentActivities.Take(9));
                    RecentActivities = new ObservableCollection<RecentOperationalActivityDto>(updated);
                    HasRecentActivities = true;
                }
            }
        }
    }
}
