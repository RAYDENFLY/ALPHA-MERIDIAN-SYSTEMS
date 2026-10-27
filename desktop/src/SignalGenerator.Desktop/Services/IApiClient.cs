using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public interface IApiClient
{
    string BaseUrl { get; }
    void SetBaseUrl(string baseUrl);

    Task<ApiResult<SystemStatusDto>> GetStatusAsync(CancellationToken ct = default);
    Task<ApiResult<LiveSessionEnvelopeDto>> GetLiveSessionAsync(CancellationToken ct = default);
    Task<ApiResult<JournalSummaryDto>> GetJournalSummaryAsync(CancellationToken ct = default);
    Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(
        string? search = null,
        int page = 1,
        int pageSize = 25,
        string? exchanges = null,
        string? sortBy = "volume_desc",
        CancellationToken ct = default);
    Task<ApiResult<List<string>>> GetSymbolsAsync(CancellationToken ct = default);
    Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default);
    Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(string? symbol = null, bool? isActive = null, CancellationToken ct = default);
    Task<ApiResult<AlertItemDto>> CreateAlertAsync(AlertCreateRequestDto req, CancellationToken ct = default);
    Task<ApiResult<AlertItemDto>> UpdateAlertAsync(string alertId, AlertUpdateRequestDto req, CancellationToken ct = default);
    Task<ApiResult<AlertItemDto>> ToggleAlertAsync(string alertId, CancellationToken ct = default);
    Task<ApiResult<bool>> DeleteAlertAsync(string alertId, CancellationToken ct = default);
    Task<ApiResult<List<AlertEventDto>>> GetAlertHistoryAsync(int limit = 50, bool unackOnly = false, CancellationToken ct = default);
    Task<ApiResult<AlertStatusDto>> GetAlertStatusAsync(CancellationToken ct = default);
    Task<ApiResult<List<SymbolCatalogItemDto>>> GetAlertSymbolsAsync(string? query = null, CancellationToken ct = default);
    Task<ApiResult<RetailPositioningResponseDto>> GetRetailPositioningAsync(string symbol = "BTCUSDT", CancellationToken ct = default);
    Task<ApiResult<JournalTradesResponseDto>> GetJournalTradesAsync(int limit = 50, CancellationToken ct = default);
    Task<ApiResult<ChartSymbolsResponseDto>> GetChartSymbolsAsync(CancellationToken ct = default);
    Task<ApiResult<LiquidityLayersResponseDto>> GetLiquidityLayersAsync(string symbol, string timeframe = "1h", CancellationToken ct = default);
    Task<ApiResult<List<KlineDto>>> GetKlinesAsync(string symbol, string timeframe = "1h", int limit = 100, CancellationToken ct = default);
    Task<ApiResult<ScannerControlResponseDto>> StartScannerAsync(CancellationToken ct = default);
    Task<ApiResult<ScannerControlResponseDto>> StopScannerAsync(CancellationToken ct = default);
    Task<ApiResult<LiveSessionEnvelopeDto>> ReauthorizeSessionAsync(string operatorId = "OPERATOR", CancellationToken ct = default);
    Task<ApiResult<GroqAutoSetupResponseDto>> TriggerSingleScanAsync(CancellationToken ct = default);
    Task<ApiResult<CrossBookResponseDto>> GetCrossBookAsync(string symbol, CancellationToken ct = default);
    Task<ApiResult<ModelBOrderBookResponseDto>> GetModelBOrderBookAsync(string symbol, CancellationToken ct = default);
    Task<ApiResult<AccountSnapshotDto>> GetAccountSnapshotAsync(CancellationToken ct = default);
}
