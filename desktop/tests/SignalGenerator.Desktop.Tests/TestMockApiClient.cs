using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.Tests;

public class TestMockApiClient : IApiClient
{
    public string BaseUrl { get; set; } = "http://127.0.0.1:8000";
    public void SetBaseUrl(string baseUrl) => BaseUrl = baseUrl;

    public virtual Task<ApiResult<SystemStatusDto>> GetStatusAsync(CancellationToken ct = default) =>
        Task.FromResult(ApiResult<SystemStatusDto>.Success(new SystemStatusDto()));

    public virtual Task<ApiResult<LiveSessionEnvelopeDto>> GetLiveSessionAsync(CancellationToken ct = default) =>
        Task.FromResult(ApiResult<LiveSessionEnvelopeDto>.Success(new LiveSessionEnvelopeDto()));

    public virtual Task<ApiResult<JournalSummaryDto>> GetJournalSummaryAsync(CancellationToken ct = default) =>
        Task.FromResult(ApiResult<JournalSummaryDto>.Success(new JournalSummaryDto()));

    public virtual Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(
        string? search = null,
        int page = 1,
        int pageSize = 25,
        string? exchanges = null,
        string? sortBy = "volume_desc",
        CancellationToken ct = default) =>
        Task.FromResult(ApiResult<ScreenerResponseDto>.Success(new ScreenerResponseDto()));

    public virtual Task<ApiResult<List<string>>> GetSymbolsAsync(CancellationToken ct = default) =>
        Task.FromResult(ApiResult<List<string>>.Success(new List<string>()));

    public virtual Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default) =>
        Task.FromResult(ApiResult<AutotraderScreeningLogResponseDto>.Success(new AutotraderScreeningLogResponseDto()));

    public virtual Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(string? symbol = null, bool? isActive = null, CancellationToken ct = default) =>
        Task.FromResult(ApiResult<List<AlertItemDto>>.Success(new List<AlertItemDto>()));

    public virtual Task<ApiResult<AlertItemDto>> CreateAlertAsync(AlertCreateRequestDto req, CancellationToken ct = default) =>
        Task.FromResult(ApiResult<AlertItemDto>.Success(new AlertItemDto
        {
            AlertId = "pa_mock_" + Guid.NewGuid().ToString("N")[..8],
            Symbol = req.Symbol,
            TargetPrice = req.TargetPrice,
            Direction = req.Direction,
            PriceSource = req.PriceSource,
            AlertType = req.AlertType,
            Note = req.Note,
            IsActive = true,
            Status = "ACTIVE"
        }));

    public virtual Task<ApiResult<AlertItemDto>> UpdateAlertAsync(string alertId, AlertUpdateRequestDto req, CancellationToken ct = default) =>
        Task.FromResult(ApiResult<AlertItemDto>.Success(new AlertItemDto
        {
            AlertId = alertId,
            Symbol = "BTCUSDT",
            TargetPrice = req.TargetPrice ?? 80000m,
            Direction = req.Direction ?? "PRICE_ABOVE",
            PriceSource = req.PriceSource ?? "MARK_PRICE",
            AlertType = req.AlertType ?? "ONE_OFF",
            Note = req.Note ?? string.Empty,
            IsActive = req.IsActive ?? true,
            Status = "ACTIVE"
        }));

    public virtual Task<ApiResult<AlertItemDto>> ToggleAlertAsync(string alertId, CancellationToken ct = default) =>
        Task.FromResult(ApiResult<AlertItemDto>.Success(new AlertItemDto
        {
            AlertId = alertId,
            Symbol = "BTCUSDT",
            TargetPrice = 80000m,
            IsActive = false,
            Status = "DISABLED"
        }));

    public virtual Task<ApiResult<bool>> DeleteAlertAsync(string alertId, CancellationToken ct = default) =>
        Task.FromResult(ApiResult<bool>.Success(true));

    public virtual Task<ApiResult<List<AlertEventDto>>> GetAlertHistoryAsync(int limit = 50, bool unackOnly = false, CancellationToken ct = default) =>
        Task.FromResult(ApiResult<List<AlertEventDto>>.Success(new List<AlertEventDto>()));

    public virtual Task<ApiResult<AlertStatusDto>> GetAlertStatusAsync(CancellationToken ct = default) =>
        Task.FromResult(ApiResult<AlertStatusDto>.Success(new AlertStatusDto()));

    public virtual Task<ApiResult<List<SymbolCatalogItemDto>>> GetAlertSymbolsAsync(string? query = null, CancellationToken ct = default) =>
        Task.FromResult(ApiResult<List<SymbolCatalogItemDto>>.Success(new List<SymbolCatalogItemDto>()));

    public virtual Task<ApiResult<RetailPositioningResponseDto>> GetRetailPositioningAsync(string symbol = "BTCUSDT", CancellationToken ct = default) =>
        Task.FromResult(ApiResult<RetailPositioningResponseDto>.Success(new RetailPositioningResponseDto()));

    public virtual Task<ApiResult<JournalTradesResponseDto>> GetJournalTradesAsync(int limit = 50, CancellationToken ct = default) =>
        Task.FromResult(ApiResult<JournalTradesResponseDto>.Success(new JournalTradesResponseDto()));

    public virtual Task<ApiResult<ChartSymbolsResponseDto>> GetChartSymbolsAsync(CancellationToken ct = default) =>
        Task.FromResult(ApiResult<ChartSymbolsResponseDto>.Success(new ChartSymbolsResponseDto()));

    public virtual Task<ApiResult<LiquidityLayersResponseDto>> GetLiquidityLayersAsync(string symbol, string timeframe = "1h", CancellationToken ct = default) =>
        Task.FromResult(ApiResult<LiquidityLayersResponseDto>.Success(new LiquidityLayersResponseDto()));

    public virtual Task<ApiResult<List<KlineDto>>> GetKlinesAsync(string symbol, string timeframe = "1h", int limit = 100, CancellationToken ct = default) =>
        Task.FromResult(ApiResult<List<KlineDto>>.Success(new List<KlineDto>()));

    public virtual Task<ApiResult<ScannerControlResponseDto>> StartScannerAsync(CancellationToken ct = default) =>
        Task.FromResult(ApiResult<ScannerControlResponseDto>.Success(new ScannerControlResponseDto
        {
            Status = "SUCCESS",
            Message = "Background scanner resumed.",
            ScannerState = "IDLE"
        }));

    public virtual Task<ApiResult<ScannerControlResponseDto>> StopScannerAsync(CancellationToken ct = default) =>
        Task.FromResult(ApiResult<ScannerControlResponseDto>.Success(new ScannerControlResponseDto
        {
            Status = "SUCCESS",
            Message = "Background scanner paused.",
            ScannerState = "STOPPED"
        }));

    public virtual Task<ApiResult<LiveSessionEnvelopeDto>> ReauthorizeSessionAsync(string operatorId = "OPERATOR", CancellationToken ct = default) =>
        Task.FromResult(ApiResult<LiveSessionEnvelopeDto>.Success(new LiveSessionEnvelopeDto
        {
            Status = "SUCCESS",
            Data = new LiveSessionDataDto
            {
                State = "ACTIVE",
                IsActive = true,
                AuthorizedBy = operatorId,
                LastStatusMessage = "Session reauthorized."
            }
        }));

    public virtual Task<ApiResult<GroqAutoSetupResponseDto>> TriggerSingleScanAsync(CancellationToken ct = default) =>
        Task.FromResult(ApiResult<GroqAutoSetupResponseDto>.Success(new GroqAutoSetupResponseDto
        {
            Status = "SUCCESS",
            TimestampUtc = DateTime.UtcNow.ToString("o")
        }));
}
