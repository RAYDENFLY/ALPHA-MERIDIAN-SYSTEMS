using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public class PaperTradingService : IPaperTradingService
{
    private readonly object _lock = new();
    private readonly PaperAccountModel _account = new();
    private readonly List<PaperOrderModel> _openOrders = new();
    private readonly List<PaperOrderModel> _orderHistory = new();
    private readonly List<PaperPositionModel> _positions = new();
    private readonly List<PaperFillModel> _fills = new();
    private readonly Dictionary<string, (decimal Last, decimal Bid, decimal Ask)> _marketPrices = new(StringComparer.OrdinalIgnoreCase);

    public PaperAccountModel Account
    {
        get
        {
            lock (_lock)
            {
                return new PaperAccountModel
                {
                    StartingBalanceUsdt = _account.StartingBalanceUsdt,
                    WalletBalanceUsdt = _account.WalletBalanceUsdt,
                    TotalEquityUsdt = _account.TotalEquityUsdt,
                    AvailableBalanceUsdt = _account.AvailableBalanceUsdt,
                    UsedMarginUsdt = _account.UsedMarginUsdt,
                    UnrealizedPnlUsdt = _account.UnrealizedPnlUsdt,
                    RealizedPnlUsdt = _account.RealizedPnlUsdt,
                    IsSimulated = true,
                    LastUpdatedUtc = _account.LastUpdatedUtc
                };
            }
        }
    }

    public IReadOnlyList<PaperOrderModel> OpenOrders
    {
        get
        {
            lock (_lock) { return _openOrders.ToList(); }
        }
    }

    public IReadOnlyList<PaperOrderModel> OrderHistory
    {
        get
        {
            lock (_lock) { return _orderHistory.ToList(); }
        }
    }

    public IReadOnlyList<PaperPositionModel> Positions
    {
        get
        {
            lock (_lock) { return _positions.ToList(); }
        }
    }

    public IReadOnlyList<PaperFillModel> Fills
    {
        get
        {
            lock (_lock) { return _fills.ToList(); }
        }
    }

    public event Action<PaperAccountModel>? OnAccountUpdated;
    public event Action<PaperOrderModel>? OnOrderUpdated;
    public event Action<PaperPositionModel>? OnPositionUpdated;

    public PaperTradingService()
    {
        ResetAccountInternal(10000m);
    }

    public Task<OrderValidationResult> ValidateOrderAsync(PaperOrderModel order, decimal currentReferencePrice = 0m, CancellationToken ct = default)
    {
        lock (_lock)
        {
            var res = new OrderValidationResult();
            if (order == null)
            {
                res.IsValid = false;
                res.ErrorMessage = "Order cannot be null.";
                return Task.FromResult(res);
            }

            if (order.MarketType == MarketType.Spot)
            {
                res.IsValid = false;
                res.ErrorMessage = "UNSUPPORTED: AsterDEX Spot is not supported by the exchange adapter. Perpetual Futures only.";
                return Task.FromResult(res);
            }

            if (string.IsNullOrWhiteSpace(order.Symbol))
            {
                res.IsValid = false;
                res.ErrorMessage = "Symbol is required.";
                return Task.FromResult(res);
            }

            if (order.OriginalQty <= 0)
            {
                res.IsValid = false;
                res.ErrorMessage = "Quantity must be greater than zero.";
                return Task.FromResult(res);
            }

            // Reference price lookup
            decimal refPrice = order.Price;
            if (currentReferencePrice > 0)
            {
                refPrice = currentReferencePrice;
            }
            else if (order.OrderType == OrderType.Market)
            {
                if (_marketPrices.TryGetValue(order.Symbol, out var quotes))
                {
                    refPrice = order.Side == OrderSide.Buy ? (quotes.Ask > 0 ? quotes.Ask : quotes.Last) : (quotes.Bid > 0 ? quotes.Bid : quotes.Last);
                }
                if (refPrice <= 0) refPrice = order.Price > 0 ? order.Price : 100m;
            }
            else
            {
                if (order.Price <= 0)
                {
                    res.IsValid = false;
                    res.ErrorMessage = "Limit price must be greater than zero.";
                    return Task.FromResult(res);
                }
            }

            // Precision checks (Step size & Tick size)
            var (minQty, stepSize, tickSize) = GetSymbolSpecs(order.Symbol);
            if (order.OriginalQty < minQty)
            {
                res.IsValid = false;
                res.ErrorMessage = $"Quantity {order.OriginalQty.ToString(System.Globalization.CultureInfo.InvariantCulture)} is below minimum allowed {minQty.ToString(System.Globalization.CultureInfo.InvariantCulture)} for {order.Symbol}.";
                return Task.FromResult(res);
            }

            if (order.OriginalQty % stepSize != 0)
            {
                res.IsValid = false;
                res.ErrorMessage = $"Quantity {order.OriginalQty.ToString(System.Globalization.CultureInfo.InvariantCulture)} does not conform to step size {stepSize.ToString(System.Globalization.CultureInfo.InvariantCulture)}.";
                return Task.FromResult(res);
            }

            if (order.OrderType != OrderType.Market && order.Price % tickSize != 0)
            {
                res.IsValid = false;
                res.ErrorMessage = $"Price {order.Price.ToString(System.Globalization.CultureInfo.InvariantCulture)} does not conform to tick size {tickSize.ToString(System.Globalization.CultureInfo.InvariantCulture)}.";
                return Task.FromResult(res);
            }

            decimal notional = refPrice * order.OriginalQty;
            res.EstimatedNotional = notional;
            if (notional < 5.00m)
            {
                res.IsValid = false;
                res.ErrorMessage = $"Notional value (${notional.ToString("N2", System.Globalization.CultureInfo.InvariantCulture)}) is below minimum exchange notional of $5.00 USDT.";
                return Task.FromResult(res);
            }

            // Fee estimate (Taker 0.05%, Maker 0.02%)
            decimal feeRate = order.OrderType == OrderType.Market ? 0.0005m : 0.0002m;
            res.EstimatedFee = notional * feeRate;

            // Margin estimate (10x default)
            int leverage = 10;
            decimal reqMargin = notional / leverage;
            res.EstimatedRequiredMargin = reqMargin;

            if (!order.ReduceOnly && _account.AvailableBalanceUsdt < (reqMargin + res.EstimatedFee))
            {
                res.IsValid = false;
                res.ErrorMessage = $"Insufficient simulated margin. Available: ${_account.AvailableBalanceUsdt.ToString("N2", System.Globalization.CultureInfo.InvariantCulture)} USDT, Required: ${(reqMargin + res.EstimatedFee).ToString("N2", System.Globalization.CultureInfo.InvariantCulture)} USDT.";
                return Task.FromResult(res);
            }

            res.IsValid = true;
            return Task.FromResult(res);
        }
    }

    public async Task<(bool Success, string? ErrorMessage, PaperOrderModel? Order)> SubmitOrderAsync(
        PaperOrderModel order, 
        decimal currentReferencePrice = 0m, 
        CancellationToken ct = default)
    {
        var validation = await ValidateOrderAsync(order, currentReferencePrice, ct);
        if (!validation.IsValid)
        {
            return (false, validation.ErrorMessage, null);
        }

        lock (_lock)
        {
            // Idempotency / Duplicate check
            if (!string.IsNullOrWhiteSpace(order.ClientOrderId) && 
                _openOrders.Any(o => o.ClientOrderId.Equals(order.ClientOrderId, StringComparison.OrdinalIgnoreCase)))
            {
                return (false, $"Duplicate order rejected. ClientOrderId '{order.ClientOrderId}' already exists in active orders.", null);
            }

            if (string.IsNullOrWhiteSpace(order.OrderId))
            {
                order.OrderId = $"PAP-ORD-{DateTime.UtcNow:yyyyMMddHHmmssfff}-{Guid.NewGuid().ToString("N")[..4]}";
            }
            if (string.IsNullOrWhiteSpace(order.ClientOrderId))
            {
                order.ClientOrderId = $"AMS-PAP-{Guid.NewGuid().ToString("N")[..8].ToUpperInvariant()}";
            }

            order.CreatedAtUtc = DateTime.UtcNow;
            order.UpdatedAtUtc = DateTime.UtcNow;
            order.IsSimulated = true;

            decimal executionPrice = order.Price;
            if (currentReferencePrice > 0)
            {
                executionPrice = currentReferencePrice;
            }
            else if (_marketPrices.TryGetValue(order.Symbol, out var quotes))
            {
                executionPrice = order.Side == OrderSide.Buy 
                    ? (quotes.Ask > 0 ? quotes.Ask : quotes.Last) 
                    : (quotes.Bid > 0 ? quotes.Bid : quotes.Last);
            }
            if (executionPrice <= 0) executionPrice = order.Price > 0 ? order.Price : 100m;

            // Immediate execution conditions:
            // 1. Market order
            // 2. Marketable Limit order: Buy with Price > executionPrice, or Sell with Price < executionPrice
            bool isMarketable = order.OrderType == OrderType.Market ||
                                (order.OrderType == OrderType.Limit && (
                                    (order.Side == OrderSide.Buy && order.Price > executionPrice) ||
                                    (order.Side == OrderSide.Sell && order.Price < executionPrice)
                                ));

            if (isMarketable)
            {
                ExecuteFillInternal(order, order.OriginalQty, executionPrice);
                order.Status = OrderStatus.Filled;
                order.FilledQty = order.OriginalQty;
                _orderHistory.Insert(0, order);
                NotifyOrderUpdated(order);
                RecalculateAccountInternal();
                return (true, null, order);
            }
            else
            {
                // Resting limit order
                order.Status = OrderStatus.New;
                int leverage = 10;
                decimal reservedMargin = (order.Price * order.OriginalQty) / leverage;
                _account.AvailableBalanceUsdt = Math.Max(0m, _account.AvailableBalanceUsdt - reservedMargin);
                _account.UsedMarginUsdt += reservedMargin;

                _openOrders.Add(order);
                NotifyOrderUpdated(order);
                RecalculateAccountInternal();
                return (true, null, order);
            }
        }
    }

    public Task<(bool Success, string? ErrorMessage)> CancelOrderAsync(string orderId, CancellationToken ct = default)
    {
        lock (_lock)
        {
            var existing = _openOrders.FirstOrDefault(o => o.OrderId.Equals(orderId, StringComparison.OrdinalIgnoreCase));
            if (existing == null)
            {
                return Task.FromResult((false, (string?)$"Order '{orderId}' not found in active open orders."));
            }

            _openOrders.Remove(existing);
            existing.Status = OrderStatus.Cancelled;
            existing.UpdatedAtUtc = DateTime.UtcNow;

            // Release reserved margin
            int leverage = 10;
            decimal remainingNotional = existing.Price * existing.RemainingQty;
            decimal releasedMargin = remainingNotional / leverage;
            _account.AvailableBalanceUsdt += releasedMargin;
            _account.UsedMarginUsdt = Math.Max(0m, _account.UsedMarginUsdt - releasedMargin);

            _orderHistory.Insert(0, existing);
            NotifyOrderUpdated(existing);
            RecalculateAccountInternal();

            return Task.FromResult((true, (string?)null));
        }
    }

    public async Task<(bool Success, string? ErrorMessage, PaperOrderModel? NewOrder)> AmendOrderAsync(
        string orderId, 
        decimal newPrice, 
        decimal newQty, 
        decimal currentReferencePrice = 0m,
        CancellationToken ct = default)
    {
        // Cancel-and-Replace Workflow
        var (cancelSuccess, cancelError) = await CancelOrderAsync(orderId, ct);
        if (!cancelSuccess)
        {
            return (false, $"Amend failed: unable to cancel existing order ({cancelError})", null);
        }

        var oldOrder = _orderHistory.FirstOrDefault(o => o.OrderId.Equals(orderId, StringComparison.OrdinalIgnoreCase));
        if (oldOrder == null)
        {
            return (false, "Amend failed: original order not found.", null);
        }

        var replacementOrder = new PaperOrderModel
        {
            Symbol = oldOrder.Symbol,
            MarketType = oldOrder.MarketType,
            Side = oldOrder.Side,
            OrderType = oldOrder.OrderType,
            Price = newPrice,
            OriginalQty = newQty,
            TimeInForce = oldOrder.TimeInForce,
            ReduceOnly = oldOrder.ReduceOnly,
            PostOnly = oldOrder.PostOnly,
            ClientOrderId = $"AMS-PAP-REPL-{Guid.NewGuid().ToString("N")[..6].ToUpperInvariant()}"
        };

        var (submitSuccess, submitError, createdOrder) = await SubmitOrderAsync(replacementOrder, currentReferencePrice, ct);
        if (!submitSuccess)
        {
            return (false, $"Order cancelled, but replacement rejected: {submitError}", null);
        }

        return (true, null, createdOrder);
    }

    public Task<(bool Success, string? ErrorMessage)> ClosePositionAsync(string symbol, decimal? closePrice = null, CancellationToken ct = default)
    {
        lock (_lock)
        {
            var pos = _positions.FirstOrDefault(p => p.Symbol.Equals(symbol, StringComparison.OrdinalIgnoreCase));
            if (pos == null)
            {
                return Task.FromResult((false, (string?)$"No active position found for symbol {symbol}."));
            }

            decimal exitPrice = closePrice ?? pos.MarkPrice;
            if (exitPrice <= 0)
            {
                if (_marketPrices.TryGetValue(symbol, out var quotes))
                {
                    exitPrice = pos.Side.Equals("LONG", StringComparison.OrdinalIgnoreCase) ? quotes.Bid : quotes.Ask;
                }
            }
            if (exitPrice <= 0) exitPrice = pos.EntryPrice;

            // Create closing fill
            var fill = new PaperFillModel
            {
                FillId = $"PAP-FILL-{Guid.NewGuid().ToString("N")[..8].ToUpperInvariant()}",
                OrderId = $"PAP-CLOSE-{symbol}-{Guid.NewGuid().ToString("N")[..4]}",
                Symbol = symbol,
                Side = pos.Side.Equals("LONG", StringComparison.OrdinalIgnoreCase) ? OrderSide.Sell : OrderSide.Buy,
                Price = exitPrice,
                Qty = pos.Size,
                FeeUsdt = exitPrice * pos.Size * 0.0005m,
                RealizedPnlUsdt = pos.Side.Equals("LONG", StringComparison.OrdinalIgnoreCase)
                    ? (exitPrice - pos.EntryPrice) * pos.Size
                    : (pos.EntryPrice - exitPrice) * pos.Size,
                TimestampUtc = DateTime.UtcNow,
                Venue = "ASTERDEX_PAPER",
                IsSimulated = true
            };

            _fills.Insert(0, fill);

            // Reconcile account
            _account.WalletBalanceUsdt += (fill.RealizedPnlUsdt - fill.FeeUsdt);
            _account.RealizedPnlUsdt += fill.RealizedPnlUsdt;
            _account.AvailableBalanceUsdt += pos.Margin + (fill.RealizedPnlUsdt - fill.FeeUsdt);
            _account.UsedMarginUsdt = Math.Max(0m, _account.UsedMarginUsdt - pos.Margin);

            _positions.Remove(pos);
            NotifyPositionUpdated(pos);
            RecalculateAccountInternal();

            return Task.FromResult((true, (string?)null));
        }
    }

    public Task ResetAccountAsync(decimal startingBalance = 10000m, CancellationToken ct = default)
    {
        lock (_lock)
        {
            ResetAccountInternal(startingBalance);
        }
        return Task.CompletedTask;
    }

    public void UpdateMarketPrices(string symbol, decimal lastPrice, decimal bestBid, decimal bestAsk)
    {
        lock (_lock)
        {
            _marketPrices[symbol] = (lastPrice, bestBid, bestAsk);

            // 1. Update positions mark price & unrealized PnL
            foreach (var pos in _positions.Where(p => p.Symbol.Equals(symbol, StringComparison.OrdinalIgnoreCase)))
            {
                pos.MarkPrice = lastPrice;
                pos.UpdatedAtUtc = DateTime.UtcNow;

                if (pos.Side.Equals("LONG", StringComparison.OrdinalIgnoreCase))
                {
                    pos.UnrealizedPnl = (lastPrice - pos.EntryPrice) * pos.Size;
                    pos.UnrealizedPnlPct = pos.EntryPrice > 0 ? ((lastPrice - pos.EntryPrice) / pos.EntryPrice) * 100m * pos.Leverage : 0m;
                }
                else
                {
                    pos.UnrealizedPnl = (pos.EntryPrice - lastPrice) * pos.Size;
                    pos.UnrealizedPnlPct = pos.EntryPrice > 0 ? ((pos.EntryPrice - lastPrice) / pos.EntryPrice) * 100m * pos.Leverage : 0m;
                }

                NotifyPositionUpdated(pos);
            }

            // 2. Check resting limit orders
            var toFill = new List<(PaperOrderModel Order, decimal FillPrice)>();
            foreach (var order in _openOrders.Where(o => o.Symbol.Equals(symbol, StringComparison.OrdinalIgnoreCase)))
            {
                if (order.Side == OrderSide.Buy && (bestAsk <= order.Price || (bestAsk == 0 && lastPrice <= order.Price)))
                {
                    toFill.Add((order, order.Price));
                }
                else if (order.Side == OrderSide.Sell && (bestBid >= order.Price || (bestBid == 0 && lastPrice >= order.Price)))
                {
                    toFill.Add((order, order.Price));
                }
            }

            foreach (var (ord, px) in toFill)
            {
                _openOrders.Remove(ord);
                ExecuteFillInternal(ord, ord.RemainingQty, px);
                ord.Status = OrderStatus.Filled;
                ord.FilledQty = ord.OriginalQty;
                _orderHistory.Insert(0, ord);
                NotifyOrderUpdated(ord);
            }

            RecalculateAccountInternal();
        }
    }

    public Task SimulatePartialFillAsync(string orderId, decimal fillQty, decimal fillPrice, CancellationToken ct = default)
    {
        lock (_lock)
        {
            var order = _openOrders.FirstOrDefault(o => o.OrderId.Equals(orderId, StringComparison.OrdinalIgnoreCase));
            if (order == null) return Task.CompletedTask;

            decimal actualFill = Math.Min(fillQty, order.RemainingQty);
            if (actualFill <= 0) return Task.CompletedTask;

            ExecuteFillInternal(order, actualFill, fillPrice);
            order.FilledQty += actualFill;
            order.Status = order.RemainingQty == 0 ? OrderStatus.Filled : OrderStatus.PartiallyFilled;
            order.UpdatedAtUtc = DateTime.UtcNow;

            if (order.Status == OrderStatus.Filled)
            {
                _openOrders.Remove(order);
                _orderHistory.Insert(0, order);
            }

            NotifyOrderUpdated(order);
            RecalculateAccountInternal();
            return Task.CompletedTask;
        }
    }

    private void ExecuteFillInternal(PaperOrderModel order, decimal fillQty, decimal fillPrice)
    {
        decimal notional = fillPrice * fillQty;
        decimal feeRate = 0.0005m; // 0.05%
        decimal fee = notional * feeRate;
        int leverage = 10;
        decimal reqMargin = notional / leverage;

        var fill = new PaperFillModel
        {
            FillId = $"PAP-FILL-{Guid.NewGuid().ToString("N")[..8].ToUpperInvariant()}",
            OrderId = order.OrderId,
            Symbol = order.Symbol,
            Side = order.Side,
            Price = fillPrice,
            Qty = fillQty,
            FeeUsdt = fee,
            RealizedPnlUsdt = 0m,
            TimestampUtc = DateTime.UtcNow,
            Venue = "ASTERDEX_PAPER",
            IsSimulated = true
        };
        _fills.Insert(0, fill);

        // Position management (isolated margin)
        var pos = _positions.FirstOrDefault(p => p.Symbol.Equals(order.Symbol, StringComparison.OrdinalIgnoreCase));
        string orderSideStr = order.Side == OrderSide.Buy ? "LONG" : "SHORT";

        if (pos == null)
        {
            // Open new position
            pos = new PaperPositionModel
            {
                Symbol = order.Symbol,
                MarketType = order.MarketType,
                Side = orderSideStr,
                Size = fillQty,
                EntryPrice = fillPrice,
                MarkPrice = fillPrice,
                Margin = reqMargin,
                Leverage = leverage,
                LiquidationPrice = orderSideStr == "LONG" ? fillPrice * 0.905m : fillPrice * 1.095m,
                UpdatedAtUtc = DateTime.UtcNow,
                IsSimulated = true
            };
            _positions.Add(pos);
            _account.UsedMarginUsdt += reqMargin;
            _account.AvailableBalanceUsdt = Math.Max(0m, _account.AvailableBalanceUsdt - reqMargin - fee);
            _account.WalletBalanceUsdt -= fee;
        }
        else if (pos.Side.Equals(orderSideStr, StringComparison.OrdinalIgnoreCase))
        {
            // Add to existing position
            decimal totalQty = pos.Size + fillQty;
            pos.EntryPrice = ((pos.EntryPrice * pos.Size) + (fillPrice * fillQty)) / totalQty;
            pos.Size = totalQty;
            pos.Margin += reqMargin;
            pos.LiquidationPrice = orderSideStr == "LONG" ? pos.EntryPrice * 0.905m : pos.EntryPrice * 1.095m;
            pos.UpdatedAtUtc = DateTime.UtcNow;

            _account.UsedMarginUsdt += reqMargin;
            _account.AvailableBalanceUsdt = Math.Max(0m, _account.AvailableBalanceUsdt - reqMargin - fee);
            _account.WalletBalanceUsdt -= fee;
        }
        else
        {
            // Opposite side -> reduce or flip
            if (fillQty <= pos.Size)
            {
                decimal realizedPnl = pos.Side == "LONG" 
                    ? (fillPrice - pos.EntryPrice) * fillQty 
                    : (pos.EntryPrice - fillPrice) * fillQty;

                fill.RealizedPnlUsdt = realizedPnl;
                _account.RealizedPnlUsdt += realizedPnl;
                _account.WalletBalanceUsdt += (realizedPnl - fee);

                decimal marginReleased = pos.Margin * (fillQty / pos.Size);
                pos.Margin = Math.Max(0m, pos.Margin - marginReleased);
                pos.Size -= fillQty;

                _account.AvailableBalanceUsdt += (marginReleased + realizedPnl - fee);
                _account.UsedMarginUsdt = Math.Max(0m, _account.UsedMarginUsdt - marginReleased);

                if (pos.Size == 0)
                {
                    _positions.Remove(pos);
                }
            }
            else
            {
                // Flips side
                decimal closeQty = pos.Size;
                decimal remQty = fillQty - closeQty;

                decimal realizedPnl = pos.Side == "LONG" 
                    ? (fillPrice - pos.EntryPrice) * closeQty 
                    : (pos.EntryPrice - fillPrice) * closeQty;

                fill.RealizedPnlUsdt = realizedPnl;
                _account.RealizedPnlUsdt += realizedPnl;
                _account.WalletBalanceUsdt += (realizedPnl - fee);

                _account.AvailableBalanceUsdt += (pos.Margin + realizedPnl - fee);
                _account.UsedMarginUsdt = Math.Max(0m, _account.UsedMarginUsdt - pos.Margin);

                pos.Side = orderSideStr;
                pos.Size = remQty;
                pos.EntryPrice = fillPrice;
                pos.Margin = (fillPrice * remQty) / leverage;
                pos.LiquidationPrice = orderSideStr == "LONG" ? fillPrice * 0.905m : fillPrice * 1.095m;
                pos.UpdatedAtUtc = DateTime.UtcNow;

                _account.UsedMarginUsdt += pos.Margin;
                _account.AvailableBalanceUsdt = Math.Max(0m, _account.AvailableBalanceUsdt - pos.Margin);
            }
        }

        NotifyPositionUpdated(pos);
    }

    private void ResetAccountInternal(decimal startingBalance)
    {
        _account.StartingBalanceUsdt = startingBalance;
        _account.WalletBalanceUsdt = startingBalance;
        _account.TotalEquityUsdt = startingBalance;
        _account.AvailableBalanceUsdt = startingBalance;
        _account.UsedMarginUsdt = 0m;
        _account.UnrealizedPnlUsdt = 0m;
        _account.RealizedPnlUsdt = 0m;
        _account.IsSimulated = true;
        _account.LastUpdatedUtc = DateTime.UtcNow;

        _openOrders.Clear();
        _orderHistory.Clear();
        _positions.Clear();
        _fills.Clear();

        NotifyAccountUpdated();
    }

    private void RecalculateAccountInternal()
    {
        decimal totalUnrealized = _positions.Sum(p => p.UnrealizedPnl);
        _account.UnrealizedPnlUsdt = totalUnrealized;
        _account.TotalEquityUsdt = _account.WalletBalanceUsdt + totalUnrealized;
        _account.LastUpdatedUtc = DateTime.UtcNow;

        NotifyAccountUpdated();
    }

    private (decimal minQty, decimal stepSize, decimal tickSize) GetSymbolSpecs(string symbol)
    {
        var sym = symbol.ToUpperInvariant();
        if (sym.StartsWith("BTC"))
            return (0.001m, 0.001m, 0.10m);
        if (sym.StartsWith("ETH"))
            return (0.01m, 0.01m, 0.01m);
        if (sym.StartsWith("SOL") || sym.StartsWith("BNB") || sym.StartsWith("AVAX"))
            return (0.1m, 0.01m, 0.01m);
        if (sym.StartsWith("XRP") || sym.StartsWith("DOGE") || sym.StartsWith("ASTER"))
            return (1.0m, 0.1m, 0.0001m);
        
        return (0.01m, 0.01m, 0.001m);
    }

    private void NotifyAccountUpdated()
    {
        var copy = Account;
        OnAccountUpdated?.Invoke(copy);
    }

    private void NotifyOrderUpdated(PaperOrderModel order)
    {
        OnOrderUpdated?.Invoke(order);
    }

    private void NotifyPositionUpdated(PaperPositionModel position)
    {
        OnPositionUpdated?.Invoke(position);
    }
}
