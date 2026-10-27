using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public interface IPaperTradingService
{
    PaperAccountModel Account { get; }
    IReadOnlyList<PaperOrderModel> OpenOrders { get; }
    IReadOnlyList<PaperOrderModel> OrderHistory { get; }
    IReadOnlyList<PaperPositionModel> Positions { get; }
    IReadOnlyList<PaperFillModel> Fills { get; }

    event Action<PaperAccountModel>? OnAccountUpdated;
    event Action<PaperOrderModel>? OnOrderUpdated;
    event Action<PaperPositionModel>? OnPositionUpdated;

    Task<OrderValidationResult> ValidateOrderAsync(PaperOrderModel order, decimal currentReferencePrice = 0m, CancellationToken ct = default);
    Task<(bool Success, string? ErrorMessage, PaperOrderModel? Order)> SubmitOrderAsync(PaperOrderModel order, decimal currentReferencePrice = 0m, CancellationToken ct = default);
    Task<(bool Success, string? ErrorMessage)> CancelOrderAsync(string orderId, CancellationToken ct = default);
    Task<(bool Success, string? ErrorMessage, PaperOrderModel? NewOrder)> AmendOrderAsync(string orderId, decimal newPrice, decimal newQty, decimal currentReferencePrice = 0m, CancellationToken ct = default);
    Task<(bool Success, string? ErrorMessage)> ClosePositionAsync(string symbol, decimal? closePrice = null, CancellationToken ct = default);
    Task ResetAccountAsync(decimal startingBalance = 10000m, CancellationToken ct = default);
    void UpdateMarketPrices(string symbol, decimal lastPrice, decimal bestBid, decimal bestAsk);
    Task SimulatePartialFillAsync(string orderId, decimal fillQty, decimal fillPrice, CancellationToken ct = default);
}
