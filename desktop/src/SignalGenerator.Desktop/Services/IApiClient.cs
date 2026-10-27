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
    Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(string? search = null, int page = 1, int pageSize = 25, CancellationToken ct = default);
    Task<ApiResult<List<string>>> GetSymbolsAsync(CancellationToken ct = default);
    Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default);
    Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(CancellationToken ct = default);
    Task<ApiResult<RetailPositioningResponseDto>> GetRetailPositioningAsync(string symbol = "BTCUSDT", CancellationToken ct = default);
}
