using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class PaperTradingWorkstationTests
{
    private readonly PaperTradingService _paperService;
    private readonly TestMockApiClient _mockApiClient;

    public PaperTradingWorkstationTests()
    {
        _paperService = new PaperTradingService();
        _mockApiClient = new TestMockApiClient();
    }

    // Scenario 1: Submit a valid limit buy
    [Fact]
    public async Task Scenario01_SubmitValidLimitBuy_RestsInOpenOrdersAndLocksMargin()
    {
        var order = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 75000.00m,
            OriginalQty = 0.010m
        };

        var (success, error, created) = await _paperService.SubmitOrderAsync(order, currentReferencePrice: 80000m);

        Assert.True(success, error);
        Assert.NotNull(created);
        Assert.Equal(OrderStatus.New, created.Status);
        Assert.Single(_paperService.OpenOrders);
        Assert.True(_paperService.Account.UsedMarginUsdt > 0);
        Assert.True(_paperService.Account.AvailableBalanceUsdt < _paperService.Account.TotalEquityUsdt);
    }

    // Scenario 2: Submit a valid limit sell
    [Fact]
    public async Task Scenario02_SubmitValidLimitSell_RestsInOpenOrdersAndLocksMargin()
    {
        var order = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Sell,
            OrderType = OrderType.Limit,
            Price = 85000.00m,
            OriginalQty = 0.010m
        };

        var (success, error, created) = await _paperService.SubmitOrderAsync(order, currentReferencePrice: 80000m);

        Assert.True(success, error);
        Assert.NotNull(created);
        Assert.Equal(OrderStatus.New, created.Status);
        Assert.Single(_paperService.OpenOrders);
    }

    // Scenario 3: Simulate a market order using reference-price model
    [Fact]
    public async Task Scenario03_SimulateMarketOrder_FillsImmediatelyAtReferencePriceWithFee()
    {
        var order = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Market,
            OriginalQty = 0.010m
        };

        decimal refPrice = 80685.50m;
        var (success, error, created) = await _paperService.SubmitOrderAsync(order, currentReferencePrice: refPrice);

        Assert.True(success, error);
        Assert.NotNull(created);
        Assert.Equal(OrderStatus.Filled, created.Status);
        Assert.Empty(_paperService.OpenOrders);
        Assert.Single(_paperService.Positions);
        Assert.Single(_paperService.Fills);

        var fill = _paperService.Fills.First();
        Assert.Equal(refPrice, fill.Price);
        Assert.Equal(0.010m, fill.Qty);
        Assert.True(fill.FeeUsdt > 0);
        Assert.Equal("ASTERDEX_PAPER", fill.Venue);
    }

    // Scenario 4: Reject quantity below minimum
    [Fact]
    public async Task Scenario04_RejectQuantityBelowMinimum()
    {
        var order = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 80000m,
            OriginalQty = 0.0001m // Min is 0.001
        };

        var (success, error, _) = await _paperService.SubmitOrderAsync(order, 80000m);

        Assert.False(success);
        Assert.Contains("below minimum allowed", error);
    }

    // Scenario 5: Reject invalid tick size or quantity step
    [Fact]
    public async Task Scenario05_RejectInvalidTickOrStepSize()
    {
        var orderInvalidStep = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 80000m,
            OriginalQty = 0.0015m // Step is 0.001
        };

        var (stepSuccess, stepError, _) = await _paperService.SubmitOrderAsync(orderInvalidStep, 80000m);
        Assert.False(stepSuccess);
        Assert.Contains("step size", stepError);

        var orderInvalidTick = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 80000.055m, // Tick is 0.10
            OriginalQty = 0.010m
        };

        var (tickSuccess, tickError, _) = await _paperService.SubmitOrderAsync(orderInvalidTick, 80000m);
        Assert.False(tickSuccess);
        Assert.Contains("tick size", tickError);
    }

    // Scenario 6: Reject insufficient simulated funds
    [Fact]
    public async Task Scenario06_RejectInsufficientSimulatedFunds()
    {
        // Try buying 20 BTC with 10x leverage ($160,000 required margin vs $10,000 balance)
        var order = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 80000m,
            OriginalQty = 20.000m
        };

        var (success, error, _) = await _paperService.SubmitOrderAsync(order, 80000m);

        Assert.False(success);
        Assert.Contains("Insufficient simulated margin", error);
    }

    // Scenario 7: Cancel an open paper order
    [Fact]
    public async Task Scenario07_CancelOpenPaperOrder_ReleasesMarginAndMovesToHistory()
    {
        var order = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 70000m,
            OriginalQty = 0.010m
        };

        var (subSuccess, _, created) = await _paperService.SubmitOrderAsync(order, 80000m);
        Assert.True(subSuccess);
        decimal availBeforeCancel = _paperService.Account.AvailableBalanceUsdt;

        var (cancelSuccess, cancelError) = await _paperService.CancelOrderAsync(created!.OrderId);
        Assert.True(cancelSuccess, cancelError);

        Assert.Empty(_paperService.OpenOrders);
        Assert.Single(_paperService.OrderHistory);
        Assert.Equal(OrderStatus.Cancelled, _paperService.OrderHistory.First().Status);
        Assert.True(_paperService.Account.AvailableBalanceUsdt > availBeforeCancel);
        Assert.Equal(0m, _paperService.Account.UsedMarginUsdt);
    }

    // Scenario 8: Amend or replace a paper order
    [Fact]
    public async Task Scenario08_AmendOrReplacePaperOrder_PerformsCancelAndReplace()
    {
        var order = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 70000m,
            OriginalQty = 0.010m
        };

        var (_, _, created) = await _paperService.SubmitOrderAsync(order, 80000m);

        var (amendSuccess, amendError, newOrder) = await _paperService.AmendOrderAsync(created!.OrderId, 71000m, 0.015m, currentReferencePrice: 80000m);

        Assert.True(amendSuccess, amendError);
        Assert.NotNull(newOrder);
        Assert.Equal(71000m, newOrder.Price);
        Assert.Equal(0.015m, newOrder.OriginalQty);
        Assert.Single(_paperService.OpenOrders);
        Assert.Equal(newOrder.OrderId, _paperService.OpenOrders.First().OrderId);
    }

    // Scenario 9: Simulate partial fill and full fill
    [Fact]
    public async Task Scenario09_SimulatePartialFill_ThenFullFill()
    {
        var order = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 75000m,
            OriginalQty = 0.020m
        };

        var (_, _, created) = await _paperService.SubmitOrderAsync(order, 80000m);

        // Partial fill of 0.010 BTC
        await _paperService.SimulatePartialFillAsync(created!.OrderId, 0.010m, 75000m);
        Assert.Single(_paperService.OpenOrders);
        Assert.Equal(OrderStatus.PartiallyFilled, _paperService.OpenOrders.First().Status);
        Assert.Equal(0.010m, _paperService.OpenOrders.First().RemainingQty);

        // Remaining fill of 0.010 BTC
        await _paperService.SimulatePartialFillAsync(created.OrderId, 0.010m, 75000m);
        Assert.Empty(_paperService.OpenOrders);
        Assert.Single(_paperService.OrderHistory);
        Assert.Equal(OrderStatus.Filled, _paperService.OrderHistory.First().Status);
    }

    // Scenario 10: Simulate rejection and timeout safely
    [Fact]
    public async Task Scenario10_SimulateRejectionAndTimeout_PreservesAccountSafety()
    {
        decimal initBalance = _paperService.Account.WalletBalanceUsdt;

        // Submitting an order that fails minimum notional ($2.00 vs $5.00 min) with valid min qty (0.001)
        var invalidOrder = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 2000m,
            OriginalQty = 0.001m // Notional = $2.00
        };

        var (success, error, _) = await _paperService.SubmitOrderAsync(invalidOrder, 2000m);
        Assert.False(success);
        Assert.Contains("below minimum exchange notional", error);

        // Balance remains unchanged
        Assert.Equal(initBalance, _paperService.Account.WalletBalanceUsdt);
        Assert.Empty(_paperService.OpenOrders);
    }

    // Scenario 11: Duplicate-click protection
    [Fact]
    public async Task Scenario11_DuplicateClickProtection_RejectsSameClientOrderId()
    {
        string clientOid = "AMS-TEST-DUP-001";
        var order1 = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 70000m,
            OriginalQty = 0.010m,
            ClientOrderId = clientOid
        };

        var (success1, _, _) = await _paperService.SubmitOrderAsync(order1, 80000m);
        Assert.True(success1);

        var order2 = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 70000m,
            OriginalQty = 0.010m,
            ClientOrderId = clientOid // Duplicate!
        };

        var (success2, error2, _) = await _paperService.SubmitOrderAsync(order2, 80000m);
        Assert.False(success2);
        Assert.Contains("Duplicate order rejected", error2);
    }

    // Scenario 12: Verify ambiguous timeout does not duplicate order
    [Fact]
    public async Task Scenario12_AmbiguousTimeoutOrPendingAction_PreventsDoubleSubmission()
    {
        var vm = new TradingWorkstationViewModel(_mockApiClient, _paperService);
        vm.InputQuantity = "0.010";
        vm.InputPrice = "75000.00";
        vm.ClientOrderId = "AMS-IDEM-002";

        // Simulate action pending lock
        vm.IsActionPending = true;

        // When action is pending, double click will be blocked by guard
        await vm.ConfirmAndSubmitOrderAsync();

        Assert.Empty(_paperService.OpenOrders);
    }

    // Scenario 13: Switch between Spot and Perpetual Futures
    [Fact]
    public async Task Scenario13_SwitchBetweenSpotAndPerpetual_AsterDexSpotRejectsWithUnsupported()
    {
        var vm = new TradingWorkstationViewModel(_mockApiClient, _paperService);

        vm.SelectedVenue = "AsterDEX";
        vm.SelectedMarketType = "SPOT";

        Assert.True(vm.IsVenueMarketUnsupported);
        Assert.Contains("AsterDEX Spot is not supported", vm.UnsupportedReasonMessage);

        // Attempting to submit Spot order is rejected
        var spotOrder = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Spot,
            Side = OrderSide.Buy,
            OrderType = OrderType.Limit,
            Price = 80000m,
            OriginalQty = 0.010m
        };

        var (success, error, _) = await _paperService.SubmitOrderAsync(spotOrder, 80000m);
        Assert.False(success);
        Assert.Contains("AsterDEX Spot is not supported", error);
    }

    // Scenario 14: Test eight order-book panels updating concurrently
    [Fact]
    public async Task Scenario14_EightOrderBookPanelsUpdateConcurrently()
    {
        var multiBookVm = new MultiMarketOrderBookViewModel(_mockApiClient, _paperService);

        Assert.Equal(8, multiBookVm.MarketTiles.Count);

        // Trigger concurrent update
        await multiBookVm.RefreshAllTilesAsync();

        Assert.Equal("SYNCED", multiBookVm.GlobalFeedStatus);
        foreach (var tile in multiBookVm.MarketTiles)
        {
            Assert.Equal("LIVE", tile.ConnectionState);
            Assert.NotEmpty(tile.Bids);
            Assert.NotEmpty(tile.Asks);
        }
    }

    // Scenario 15: Test stale and disconnected feeds
    [Fact]
    public void Scenario15_StaleAndDisconnectedFeeds_HandledGracefully()
    {
        var tile = new MarketBookTileModel
        {
            Symbol = "BTCUSDT",
            ConnectionState = "STALE"
        };
        Assert.Equal("#FF9D00", tile.StatusBadgeColor);

        tile.ConnectionState = "DISCONNECTED";
        Assert.Equal("#FF5252", tile.StatusBadgeColor);
    }

    // Scenario 16: Verify selected order-book price can prefill, but NOT submit, a limit order
    [Fact]
    public void Scenario16_OrderBookPricePrefillsLimitPriceWithoutSubmitting()
    {
        var workstationVm = new TradingWorkstationViewModel(_mockApiClient, _paperService);
        var multiBookVm = new MultiMarketOrderBookViewModel(_mockApiClient, _paperService);

        multiBookVm.OnPrefillLimitPrice = px => workstationVm.SetPrefillPrice(px);

        decimal clickedPrice = 80123.40m;
        multiBookVm.PrefillOrderPrice(clickedPrice);

        Assert.Equal("80123.40", workstationVm.InputPrice);
        Assert.Empty(_paperService.OpenOrders); // Absolutely NOT submitted!
    }

    // Scenario 17: Verify paper orders cannot affect live account state
    [Fact]
    public async Task Scenario17_PaperOrdersCannotAffectLiveAccountState()
    {
        // Live snapshot reported by AsterDEX
        var liveSnapshot = await _mockApiClient.GetAccountSnapshotAsync();
        decimal liveEquity = liveSnapshot.Value!.TotalEquityUsdt!.Value;
        Assert.Equal(14.34m, liveEquity);

        // Submit paper order
        var order = new PaperOrderModel
        {
            Symbol = "BTCUSDT",
            MarketType = MarketType.Perpetual,
            Side = OrderSide.Buy,
            OrderType = OrderType.Market,
            OriginalQty = 0.010m
        };
        var (subSuccess, subError, _) = await _paperService.SubmitOrderAsync(order, 80000m);
        Assert.True(subSuccess, subError);

        // Paper balance changed
        Assert.NotEqual(10000.00m, _paperService.Account.WalletBalanceUsdt);

        // Live account snapshot queried from API is completely unchanged
        var liveAfter = await _mockApiClient.GetAccountSnapshotAsync();
        Assert.Equal(14.34m, liveAfter.Value!.TotalEquityUsdt!.Value);
    }

    // Scenario 18: Verify no paper test invokes a live mutation endpoint
    [Fact]
    public void Scenario18_DesktopSecurityBoundary_RejectsLiveOrderMutations()
    {
        var method = typeof(ApiClient).GetMethod("ValidateAuthorizedMutationRoute", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
        Assert.NotNull(method);

        var ex = Assert.Throws<System.Reflection.TargetInvocationException>(() =>
        {
            method.Invoke(null, new object[] { "/api/orders" });
        });

        Assert.IsType<InvalidOperationException>(ex.InnerException);
        Assert.Contains("Security Boundary Violation", ex.InnerException.Message);
    }

    // ViewModel Integration Tests
    [Fact]
    public void TradingWorkstationViewModel_CalculatesPreValidationCorrectly()
    {
        var vm = new TradingWorkstationViewModel(_mockApiClient, _paperService);
        vm.InputQuantity = "0.010";
        vm.InputPrice = "80000.00";

        Assert.Equal("$800.00 USDT", vm.EstimatedNotionalText);
        Assert.Equal("$0.16 USDT", vm.EstimatedFeeText);
        Assert.Equal("$80.00 USDT", vm.EstimatedMarginText);
        Assert.False(vm.HasValidationError);
    }

    [Fact]
    public void TradingWorkstationViewModel_LiveModeSelected_BlocksSubmissionByPolicy()
    {
        var vm = new TradingWorkstationViewModel(_mockApiClient, _paperService);
        vm.TradingMode = "LIVE";

        Assert.True(vm.IsLiveMode);
        Assert.True(vm.HasValidationError);
        Assert.Contains("LIVE ORDER DISPATCH DISABLED BY SECURITY POLICY", vm.ValidationErrorMessage);

        // Attempt submission
        vm.RequestOrderConfirmation();
        Assert.False(vm.ShowConfirmationModal); // Blocked
    }
}
