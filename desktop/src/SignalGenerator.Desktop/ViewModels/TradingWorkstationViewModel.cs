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

public partial class TradingWorkstationViewModel : ViewModelBase
{
    private readonly IApiClient _apiClient;
    private readonly IPaperTradingService _paperTradingService;

    // Venue & Market
    [ObservableProperty]
    private string _selectedVenue = "AsterDEX";

    [ObservableProperty]
    private string _selectedMarketType = "PERPETUAL";

    [ObservableProperty]
    private string _selectedSymbol = "BTCUSDT";

    [ObservableProperty]
    private bool _isSpotSelected;

    [ObservableProperty]
    private bool _isPerpSelected = true;

    [ObservableProperty]
    private bool _isVenueMarketUnsupported;

    [ObservableProperty]
    private string _unsupportedReasonMessage = string.Empty;

    public ObservableCollection<string> Venues { get; } = new() { "AsterDEX" };
    public ObservableCollection<string> MarketTypes { get; } = new() { "PERPETUAL", "SPOT" };
    public ObservableCollection<string> Symbols { get; } = new()
    {
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LINKUSDT", "DOGEUSDT", "AVAXUSDT", "ASTERUSDT"
    };

    // Session & Mode
    [ObservableProperty]
    private string _tradingMode = "PAPER"; // "PAPER" or "LIVE"

    [ObservableProperty]
    private bool _isLiveMode;

    [ObservableProperty]
    private bool _isLiveBlockedByPolicy = true;

    // Market Context
    [ObservableProperty]
    private decimal _lastPrice = 80685.50m;

    [ObservableProperty]
    private decimal _bestBid = 80685.50m;

    [ObservableProperty]
    private decimal _bestAsk = 80685.60m;

    [ObservableProperty]
    private decimal _spread = 0.10m;

    [ObservableProperty]
    private decimal _spreadBps = 0.01m;

    [ObservableProperty]
    private string _marketFreshness = "LIVE";

    [ObservableProperty]
    private string _lastPriceText = "$80,685.50";

    [ObservableProperty]
    private string _bestBidText = "$80,685.50";

    [ObservableProperty]
    private string _bestAskText = "$80,685.60";

    [ObservableProperty]
    private string _spreadText = "$0.10 (0.01 bps)";

    // Account Context
    [ObservableProperty]
    private string _totalEquityText = "$10,000.00 USDT";

    [ObservableProperty]
    private string _availableBalanceText = "$10,000.00 USDT";

    [ObservableProperty]
    private string _usedMarginText = "$0.00 USDT";

    [ObservableProperty]
    private string _unrealizedPnlText = "$0.00 (0.00%)";

    [ObservableProperty]
    private string _accountStatusBadge = "CONNECTED (SIMULATED)";

    // Order Entry Form
    [ObservableProperty]
    private OrderSide _orderSide = OrderSide.Buy;

    [ObservableProperty]
    private OrderType _orderType = OrderType.Limit;

    [ObservableProperty]
    private string _inputQuantity = "0.010";

    [ObservableProperty]
    private string _inputPrice = "80685.50";

    [ObservableProperty]
    private string _inputTriggerPrice = string.Empty;

    [ObservableProperty]
    private TimeInForce _timeInForce = TimeInForce.Gtc;

    [ObservableProperty]
    private bool _reduceOnly;

    [ObservableProperty]
    private bool _postOnly;

    [ObservableProperty]
    private string _clientOrderId = string.Empty;

    // Real-Time Pre-Validation Info
    [ObservableProperty]
    private string _estimatedNotionalText = "$806.86 USDT";

    [ObservableProperty]
    private string _estimatedFeeText = "$0.16 USDT";

    [ObservableProperty]
    private string _estimatedMarginText = "$80.69 USDT";

    [ObservableProperty]
    private string? _validationErrorMessage;

    [ObservableProperty]
    private bool _hasValidationError;

    [ObservableProperty]
    private bool _isActionPending;

    // Confirmation Modal
    [ObservableProperty]
    private bool _showConfirmationModal;

    [ObservableProperty]
    private string _confirmSummary = string.Empty;

    [ObservableProperty]
    private string _confirmSideText = string.Empty;

    [ObservableProperty]
    private string _confirmSideColor = "#00E676";

    // Order Management Collections
    public ObservableCollection<PaperOrderModel> OpenOrders { get; } = new();
    public ObservableCollection<PaperPositionModel> Positions { get; } = new();
    public ObservableCollection<PaperOrderModel> ProtectiveOrders { get; } = new();
    public ObservableCollection<PaperFillModel> FillsHistory { get; } = new();
    public ObservableCollection<PaperOrderModel> ClosedOrdersHistory { get; } = new();

    public TradingWorkstationViewModel(IApiClient apiClient, IPaperTradingService paperTradingService)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _paperTradingService = paperTradingService ?? throw new ArgumentNullException(nameof(paperTradingService));

        _paperTradingService.OnAccountUpdated += HandleAccountUpdated;
        _paperTradingService.OnOrderUpdated += HandleOrderUpdated;
        _paperTradingService.OnPositionUpdated += HandlePositionUpdated;

        SyncAccountUi(_paperTradingService.Account);
        RefreshOrderLists();
        RecalculateEstimates();
    }

    partial void OnSelectedVenueChanged(string value) => CheckVenueMarketSupport();

    partial void OnSelectedMarketTypeChanged(string value)
    {
        IsPerpSelected = value.Equals("PERPETUAL", StringComparison.OrdinalIgnoreCase);
        IsSpotSelected = value.Equals("SPOT", StringComparison.OrdinalIgnoreCase);
        CheckVenueMarketSupport();
    }

    partial void OnSelectedSymbolChanged(string value)
    {
        _ = LoadMarketContextAsync(value);
        RecalculateEstimates();
    }

    partial void OnTradingModeChanged(string value)
    {
        IsLiveMode = value.Equals("LIVE", StringComparison.OrdinalIgnoreCase);
        if (IsLiveMode)
        {
            ValidationErrorMessage = "LIVE ORDER DISPATCH DISABLED BY SECURITY POLICY (READ-ONLY).";
            HasValidationError = true;
        }
        else
        {
            RecalculateEstimates();
        }
    }

    partial void OnOrderSideChanged(OrderSide value) => RecalculateEstimates();
    partial void OnOrderTypeChanged(OrderType value) => RecalculateEstimates();
    partial void OnInputQuantityChanged(string value) => RecalculateEstimates();
    partial void OnInputPriceChanged(string value) => RecalculateEstimates();

    private void CheckVenueMarketSupport()
    {
        if (SelectedVenue.Equals("AsterDEX", StringComparison.OrdinalIgnoreCase) &&
            SelectedMarketType.Equals("SPOT", StringComparison.OrdinalIgnoreCase))
        {
            IsVenueMarketUnsupported = true;
            UnsupportedReasonMessage = "UNSUPPORTED: AsterDEX Spot is not supported by exchange adapter. Perpetual Futures only.";
            ValidationErrorMessage = UnsupportedReasonMessage;
            HasValidationError = true;
        }
        else
        {
            IsVenueMarketUnsupported = false;
            UnsupportedReasonMessage = string.Empty;
            RecalculateEstimates();
        }
    }

    public async Task LoadMarketContextAsync(string symbol)
    {
        try
        {
            var res = await _apiClient.GetModelBOrderBookAsync(symbol);
            if (res.IsSuccess && res.Value?.Books != null && res.Value.Books.TryGetValue("asterdex", out var book))
            {
                if (book.BestBid.HasValue && book.BestAsk.HasValue)
                {
                    BestBid = book.BestBid.Value;
                    BestAsk = book.BestAsk.Value;
                    LastPrice = book.MidPrice ?? BestBid;
                    Spread = Math.Max(0m, BestAsk - BestBid);
                    SpreadBps = book.SpreadBps ?? (LastPrice > 0 ? (Spread / LastPrice) * 10000m : 0m);

                    LastPriceText = $"${LastPrice:N2}";
                    BestBidText = $"${BestBid:N2}";
                    BestAskText = $"${BestAsk:N2}";
                    SpreadText = $"${Spread:N2} ({SpreadBps:N2} bps)";
                    MarketFreshness = "LIVE";

                    _paperTradingService.UpdateMarketPrices(symbol, LastPrice, BestBid, BestAsk);
                }
            }
        }
        catch
        {
            MarketFreshness = "CACHED";
        }
    }

    public void SetPrefillPrice(decimal price)
    {
        InputPrice = price > 100 ? price.ToString("F2", CultureInfo.InvariantCulture) : price.ToString("F4", CultureInfo.InvariantCulture);
        RecalculateEstimates();
    }

    [RelayCommand]
    public void SelectBuySide()
    {
        OrderSide = OrderSide.Buy;
    }

    [RelayCommand]
    public void SelectSellSide()
    {
        OrderSide = OrderSide.Sell;
    }

    [RelayCommand]
    public void RequestOrderConfirmation()
    {
        if (IsLiveMode)
        {
            ValidationErrorMessage = "SECURITY BARRIER: Live order submission is disabled in Phase 5. Paper simulation mode only.";
            HasValidationError = true;
            return;
        }

        if (IsVenueMarketUnsupported)
        {
            ValidationErrorMessage = UnsupportedReasonMessage;
            HasValidationError = true;
            return;
        }

        if (!decimal.TryParse(InputQuantity, NumberStyles.Any, CultureInfo.InvariantCulture, out var qty) || qty <= 0)
        {
            ValidationErrorMessage = "Please enter a valid quantity greater than zero.";
            HasValidationError = true;
            return;
        }

        decimal px = LastPrice;
        if (OrderType != OrderType.Market)
        {
            if (!decimal.TryParse(InputPrice, NumberStyles.Any, CultureInfo.InvariantCulture, out px) || px <= 0)
            {
                ValidationErrorMessage = "Please enter a valid limit price.";
                HasValidationError = true;
                return;
            }
        }

        ConfirmSideText = OrderSide == OrderSide.Buy ? "BUY / LONG" : "SELL / SHORT";
        ConfirmSideColor = OrderSide == OrderSide.Buy ? "#00E676" : "#FF5252";

        decimal notional = px * qty;
        ConfirmSummary = $"{ConfirmSideText} {qty} {SelectedSymbol} @ ${(OrderType == OrderType.Market ? "MARKET" : px.ToString("N2"))}\n" +
                         $"Est. Notional: ${notional:N2} USDT | Order Type: {OrderType} | Venue: {SelectedVenue} {SelectedMarketType}\n" +
                         $"EXECUTION ENVIRONMENT: [SIMULATED - PAPER TRADING]";

        ShowConfirmationModal = true;
    }

    [RelayCommand]
    public void CancelConfirmation()
    {
        ShowConfirmationModal = false;
    }

    [RelayCommand]
    public async Task ConfirmAndSubmitOrderAsync()
    {
        if (IsActionPending) return;

        try
        {
            IsActionPending = true;
            ShowConfirmationModal = false;

            decimal.TryParse(InputQuantity, NumberStyles.Any, CultureInfo.InvariantCulture, out var qty);
            decimal px = LastPrice;
            if (OrderType != OrderType.Market)
            {
                decimal.TryParse(InputPrice, NumberStyles.Any, CultureInfo.InvariantCulture, out px);
            }

            var order = new PaperOrderModel
            {
                Symbol = SelectedSymbol,
                MarketType = IsPerpSelected ? MarketType.Perpetual : MarketType.Spot,
                Side = OrderSide,
                OrderType = OrderType,
                Price = px,
                OriginalQty = qty,
                TimeInForce = TimeInForce,
                ReduceOnly = ReduceOnly,
                PostOnly = PostOnly,
                ClientOrderId = string.IsNullOrWhiteSpace(ClientOrderId) ? string.Empty : ClientOrderId
            };

            var (success, error, created) = await _paperTradingService.SubmitOrderAsync(order, LastPrice);
            if (!success)
            {
                ValidationErrorMessage = error;
                HasValidationError = true;
            }
            else
            {
                ValidationErrorMessage = null;
                HasValidationError = false;
                ClientOrderId = string.Empty;
                RefreshOrderLists();
            }
        }
        finally
        {
            IsActionPending = false;
        }
    }

    [RelayCommand]
    public async Task CancelOrderAsync(string orderId)
    {
        if (string.IsNullOrWhiteSpace(orderId) || IsActionPending) return;

        try
        {
            IsActionPending = true;
            var (success, error) = await _paperTradingService.CancelOrderAsync(orderId);
            if (!success)
            {
                ValidationErrorMessage = error;
                HasValidationError = true;
            }
            else
            {
                RefreshOrderLists();
            }
        }
        finally
        {
            IsActionPending = false;
        }
    }

    [RelayCommand]
    public async Task ReplaceOrderAsync(PaperOrderModel order)
    {
        if (order == null || IsActionPending) return;

        try
        {
            IsActionPending = true;
            decimal newPrice = order.Price * 1.001m; // slight tick adjust
            var (success, error, _) = await _paperTradingService.AmendOrderAsync(order.OrderId, newPrice, order.OriginalQty);
            if (!success)
            {
                ValidationErrorMessage = error;
                HasValidationError = true;
            }
            else
            {
                RefreshOrderLists();
            }
        }
        finally
        {
            IsActionPending = false;
        }
    }

    [RelayCommand]
    public async Task ClosePositionAsync(string symbol)
    {
        if (string.IsNullOrWhiteSpace(symbol) || IsActionPending) return;

        try
        {
            IsActionPending = true;
            var (success, error) = await _paperTradingService.ClosePositionAsync(symbol);
            if (!success)
            {
                ValidationErrorMessage = error;
                HasValidationError = true;
            }
            else
            {
                RefreshOrderLists();
            }
        }
        finally
        {
            IsActionPending = false;
        }
    }

    [RelayCommand]
    public async Task ResetPaperAccountAsync()
    {
        if (IsActionPending) return;

        try
        {
            IsActionPending = true;
            await _paperTradingService.ResetAccountAsync(10000m);
            RefreshOrderLists();
        }
        finally
        {
            IsActionPending = false;
        }
    }

    private void RecalculateEstimates()
    {
        if (IsVenueMarketUnsupported) return;

        if (!decimal.TryParse(InputQuantity, NumberStyles.Any, CultureInfo.InvariantCulture, out var qty) || qty <= 0)
        {
            EstimatedNotionalText = "$0.00 USDT";
            EstimatedFeeText = "$0.00 USDT";
            EstimatedMarginText = "$0.00 USDT";
            return;
        }

        decimal px = LastPrice;
        if (OrderType != OrderType.Market)
        {
            if (decimal.TryParse(InputPrice, NumberStyles.Any, CultureInfo.InvariantCulture, out var enteredPx) && enteredPx > 0)
            {
                px = enteredPx;
            }
        }

        decimal notional = px * qty;
        decimal feeRate = OrderType == OrderType.Market ? 0.0005m : 0.0002m;
        decimal fee = notional * feeRate;
        int leverage = 10;
        decimal reqMargin = notional / leverage;

        EstimatedNotionalText = $"${notional.ToString("N2", CultureInfo.InvariantCulture)} USDT";
        EstimatedFeeText = $"${fee.ToString("N2", CultureInfo.InvariantCulture)} USDT";
        EstimatedMarginText = $"${reqMargin.ToString("N2", CultureInfo.InvariantCulture)} USDT";

        if (notional < 5.00m)
        {
            ValidationErrorMessage = $"Notional ${notional.ToString("N2", CultureInfo.InvariantCulture)} is below minimum $5.00 USDT.";
            HasValidationError = true;
        }
        else
        {
            ValidationErrorMessage = null;
            HasValidationError = false;
        }
    }

    private void HandleAccountUpdated(PaperAccountModel acc)
    {
        SyncAccountUi(acc);
    }

    private void HandleOrderUpdated(PaperOrderModel order)
    {
        RefreshOrderLists();
    }

    private void HandlePositionUpdated(PaperPositionModel pos)
    {
        RefreshOrderLists();
    }

    private void SyncAccountUi(PaperAccountModel acc)
    {
        TotalEquityText = $"${acc.TotalEquityUsdt.ToString("N2", CultureInfo.InvariantCulture)} USDT";
        AvailableBalanceText = $"${acc.AvailableBalanceUsdt.ToString("N2", CultureInfo.InvariantCulture)} USDT";
        UsedMarginText = $"${acc.UsedMarginUsdt.ToString("N2", CultureInfo.InvariantCulture)} USDT";
        UnrealizedPnlText = $"{(acc.UnrealizedPnlUsdt >= 0 ? "+" : "")}${acc.UnrealizedPnlUsdt.ToString("N2", CultureInfo.InvariantCulture)}";
        AccountStatusBadge = "CONNECTED (SIMULATED)";
    }

    private void RefreshOrderLists()
    {
        OpenOrders.Clear();
        foreach (var ord in _paperTradingService.OpenOrders)
        {
            OpenOrders.Add(ord);
        }

        Positions.Clear();
        foreach (var pos in _paperTradingService.Positions)
        {
            Positions.Add(pos);
        }

        FillsHistory.Clear();
        foreach (var fill in _paperTradingService.Fills.Take(50))
        {
            FillsHistory.Add(fill);
        }

        ClosedOrdersHistory.Clear();
        foreach (var ord in _paperTradingService.OrderHistory.Take(50))
        {
            ClosedOrdersHistory.Add(ord);
        }
    }
}
