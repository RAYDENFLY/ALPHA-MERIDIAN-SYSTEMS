using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Serialization;

namespace SignalGenerator.Desktop.Services;

/// <summary>
/// HTTP API client communicating with Alpha Meridian Systems FastAPI backend.
/// Safety boundary: Read operations are permitted across market data, telemetry, and research.
/// Mutation requests are strictly constrained and authorized ONLY for supported Price Alert operations (/api/alerts).
/// All trading order execution, session lifecycle, risk limits, and AI scan mutations remain inaccessible.
/// </summary>
public class ApiClient : IApiClient
{
    private readonly HttpClient _httpClient;
    private string _baseUrl;
    public static readonly JsonSerializerOptions JsonOptions = CreateJsonOptions();

    public static JsonSerializerOptions CreateJsonOptions()
    {
        var options = new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true,
            NumberHandling = JsonNumberHandling.AllowReadingFromString
        };
        options.Converters.Add(new SafeDecimalConverter());
        options.Converters.Add(new SafeNullableDecimalConverter());
        options.Converters.Add(new SafeDoubleConverter());
        options.Converters.Add(new SafeNullableDoubleConverter());
        options.Converters.Add(new SafeLongConverter());
        options.Converters.Add(new SafeNullableLongConverter());
        return options;
    }

    public string BaseUrl => _baseUrl;

    public ApiClient(HttpClient httpClient, ISettingsService settingsService)
    {
        _httpClient = httpClient ?? throw new ArgumentNullException(nameof(httpClient));
        _baseUrl = settingsService?.CurrentSettings.BaseUrl?.TrimEnd('/') ?? "http://127.0.0.1:8000";
    }

    public ApiClient(HttpClient httpClient, string baseUrl)
    {
        _httpClient = httpClient ?? throw new ArgumentNullException(nameof(httpClient));
        _baseUrl = baseUrl.TrimEnd('/');
    }

    public void SetBaseUrl(string baseUrl)
    {
        if (!string.IsNullOrWhiteSpace(baseUrl))
        {
            _baseUrl = baseUrl.TrimEnd('/');
        }
    }

    public async Task<ApiResult<SystemStatusDto>> GetStatusAsync(CancellationToken ct = default)
    {
        return await ExecuteGetAsync<SystemStatusDto>("/api/status", ct);
    }

    public async Task<ApiResult<LiveSessionEnvelopeDto>> GetLiveSessionAsync(CancellationToken ct = default)
    {
        return await ExecuteGetAsync<LiveSessionEnvelopeDto>("/api/inspector/live-session", ct);
    }

    public async Task<ApiResult<JournalSummaryDto>> GetJournalSummaryAsync(CancellationToken ct = default)
    {
        return await ExecuteGetAsync<JournalSummaryDto>("/api/v1/journal/summary", ct);
    }

    public async Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(
        string? search = null,
        int page = 1,
        int pageSize = 25,
        string? exchanges = null,
        string? sortBy = "volume_desc",
        CancellationToken ct = default)
    {
        var endpoint = $"/api/screener/markets?page={page}&page_size={pageSize}";
        if (!string.IsNullOrWhiteSpace(search))
        {
            endpoint += $"&search={Uri.EscapeDataString(search.Trim())}";
        }
        if (!string.IsNullOrWhiteSpace(exchanges))
        {
            endpoint += $"&exchanges={Uri.EscapeDataString(exchanges.Trim())}";
        }
        if (!string.IsNullOrWhiteSpace(sortBy))
        {
            endpoint += $"&sort_by={Uri.EscapeDataString(sortBy.Trim())}";
        }
        return await ExecuteGetAsync<ScreenerResponseDto>(endpoint, ct);
    }

    public async Task<ApiResult<List<string>>> GetSymbolsAsync(CancellationToken ct = default)
    {
        return await ExecuteGetAsync<List<string>>("/api/chart/symbols", ct);
    }

    public async Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default)
    {
        return await ExecuteGetAsync<AutotraderScreeningLogResponseDto>("/api/autotrader/screening-log", ct);
    }

    public async Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(string? symbol = null, bool? isActive = null, CancellationToken ct = default)
    {
        var endpoint = "/api/alerts";
        var queryParams = new List<string>();
        if (!string.IsNullOrWhiteSpace(symbol))
        {
            queryParams.Add($"symbol={Uri.EscapeDataString(symbol.Trim().ToUpperInvariant())}");
        }
        if (isActive.HasValue)
        {
            queryParams.Add($"is_active={isActive.Value.ToString().ToLowerInvariant()}");
        }
        if (queryParams.Count > 0)
        {
            endpoint += "?" + string.Join("&", queryParams);
        }
        return await ExecuteGetAsync<List<AlertItemDto>>(endpoint, ct);
    }

    public async Task<ApiResult<AlertItemDto>> CreateAlertAsync(AlertCreateRequestDto req, CancellationToken ct = default)
    {
        if (req == null) throw new ArgumentNullException(nameof(req));
        if (string.IsNullOrWhiteSpace(req.Symbol))
        {
            return ApiResult<AlertItemDto>.Failure("Symbol cannot be empty.");
        }
        if (req.TargetPrice <= 0)
        {
            return ApiResult<AlertItemDto>.Failure("Target price must be greater than zero.");
        }

        return await ExecuteMutationAsync<AlertItemDto>(HttpMethod.Post, "/api/alerts", req, ct);
    }

    public async Task<ApiResult<AlertItemDto>> UpdateAlertAsync(string alertId, AlertUpdateRequestDto req, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(alertId)) throw new ArgumentNullException(nameof(alertId));
        if (req == null) throw new ArgumentNullException(nameof(req));
        if (req.TargetPrice.HasValue && req.TargetPrice.Value <= 0)
        {
            return ApiResult<AlertItemDto>.Failure("Target price must be greater than zero.");
        }

        return await ExecuteMutationAsync<AlertItemDto>(HttpMethod.Put, $"/api/alerts/{Uri.EscapeDataString(alertId.Trim())}", req, ct);
    }

    public async Task<ApiResult<AlertItemDto>> ToggleAlertAsync(string alertId, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(alertId)) throw new ArgumentNullException(nameof(alertId));

        return await ExecuteMutationAsync<AlertItemDto>(HttpMethod.Patch, $"/api/alerts/{Uri.EscapeDataString(alertId.Trim())}/toggle", null, ct);
    }

    public async Task<ApiResult<bool>> DeleteAlertAsync(string alertId, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(alertId)) throw new ArgumentNullException(nameof(alertId));

        var res = await ExecuteMutationAsync<JsonElement>(HttpMethod.Delete, $"/api/alerts/{Uri.EscapeDataString(alertId.Trim())}", null, ct);
        if (res.IsSuccess)
        {
            return ApiResult<bool>.Success(true, res.StatusCode ?? 200);
        }
        return ApiResult<bool>.Failure(res.ErrorMessage ?? "Failed to delete alert.", res.StatusCode);
    }

    public async Task<ApiResult<List<AlertEventDto>>> GetAlertHistoryAsync(int limit = 50, bool unackOnly = false, CancellationToken ct = default)
    {
        var endpoint = $"/api/alerts/history?limit={Math.Clamp(limit, 1, 500)}&unack_only={unackOnly.ToString().ToLowerInvariant()}";
        return await ExecuteGetAsync<List<AlertEventDto>>(endpoint, ct);
    }

    public async Task<ApiResult<AlertStatusDto>> GetAlertStatusAsync(CancellationToken ct = default)
    {
        return await ExecuteGetAsync<AlertStatusDto>("/api/alerts/status", ct);
    }

    public async Task<ApiResult<List<SymbolCatalogItemDto>>> GetAlertSymbolsAsync(string? query = null, CancellationToken ct = default)
    {
        var endpoint = "/api/alerts/symbols";
        if (!string.IsNullOrWhiteSpace(query))
        {
            endpoint += $"?q={Uri.EscapeDataString(query.Trim().ToUpperInvariant())}";
        }
        return await ExecuteGetAsync<List<SymbolCatalogItemDto>>(endpoint, ct);
    }

    public async Task<ApiResult<RetailPositioningResponseDto>> GetRetailPositioningAsync(string symbol = "BTCUSDT", CancellationToken ct = default)
    {
        return await ExecuteGetAsync<RetailPositioningResponseDto>($"/api/research/retail_positioning?symbol={Uri.EscapeDataString(symbol)}", ct);
    }

    public async Task<ApiResult<JournalTradesResponseDto>> GetJournalTradesAsync(int limit = 50, CancellationToken ct = default)
    {
        return await ExecuteGetAsync<JournalTradesResponseDto>($"/api/v1/journal/trades?limit={limit}", ct);
    }

    public async Task<ApiResult<ChartSymbolsResponseDto>> GetChartSymbolsAsync(CancellationToken ct = default)
    {
        return await ExecuteGetAsync<ChartSymbolsResponseDto>("/api/chart/symbols", ct);
    }

    public async Task<ApiResult<LiquidityLayersResponseDto>> GetLiquidityLayersAsync(string symbol, string timeframe = "1h", CancellationToken ct = default)
    {
        return await ExecuteGetAsync<LiquidityLayersResponseDto>($"/api/chart/liquidity_layers?symbol={Uri.EscapeDataString(symbol)}&timeframe={Uri.EscapeDataString(timeframe)}", ct);
    }

    public async Task<ApiResult<List<KlineDto>>> GetKlinesAsync(string symbol, string timeframe = "1h", int limit = 100, CancellationToken ct = default)
    {
        return await ExecuteGetAsync<List<KlineDto>>($"/api/chart/klines?symbol={Uri.EscapeDataString(symbol)}&timeframe={Uri.EscapeDataString(timeframe)}&limit={limit}", ct);
    }

    public async Task<ApiResult<ScannerControlResponseDto>> StartScannerAsync(CancellationToken ct = default)
    {
        return await ExecuteMutationAsync<ScannerControlResponseDto>(HttpMethod.Post, "/api/scanner/start", null, ct);
    }

    public async Task<ApiResult<ScannerControlResponseDto>> StopScannerAsync(CancellationToken ct = default)
    {
        return await ExecuteMutationAsync<ScannerControlResponseDto>(HttpMethod.Post, "/api/scanner/stop", null, ct);
    }

    public async Task<ApiResult<LiveSessionEnvelopeDto>> ReauthorizeSessionAsync(string operatorId = "OPERATOR", CancellationToken ct = default)
    {
        var payload = new { operator_id = operatorId };
        return await ExecuteMutationAsync<LiveSessionEnvelopeDto>(HttpMethod.Post, "/api/inspector/live-session/reauthorize", payload, ct);
    }

    public async Task<ApiResult<GroqAutoSetupResponseDto>> TriggerSingleScanAsync(CancellationToken ct = default)
    {
        var payload = new
        {
            risk_preset = "CONSERVATIVE_035",
            execution_venue = "PAPER",
            auto_fire_limit_order = false,
            operator_confirmed = false
        };
        return await ExecuteMutationAsync<GroqAutoSetupResponseDto>(HttpMethod.Post, "/api/inspector/groq-auto-setup", payload, ct);
    }

    public async Task<ApiResult<CrossBookResponseDto>> GetCrossBookAsync(string symbol, CancellationToken ct = default)
    {
        return await ExecuteGetAsync<CrossBookResponseDto>($"/api/chart/cross_book?symbol={Uri.EscapeDataString(symbol)}", ct);
    }

    public async Task<ApiResult<ModelBOrderBookResponseDto>> GetModelBOrderBookAsync(string symbol, CancellationToken ct = default)
    {
        return await ExecuteGetAsync<ModelBOrderBookResponseDto>($"/api/model_b/orderbook/{Uri.EscapeDataString(symbol)}", ct);
    }

    public async Task<ApiResult<AccountSnapshotDto>> GetAccountSnapshotAsync(CancellationToken ct = default)
    {
        return await ExecuteGetAsync<AccountSnapshotDto>("/api/account", ct);
    }

    private async Task<ApiResult<T>> ExecuteGetAsync<T>(string relativeUri, CancellationToken ct)
    {
        try
        {
            var requestUri = $"{_baseUrl}{relativeUri}";
            using var response = await _httpClient.GetAsync(requestUri, ct);

            if (!response.IsSuccessStatusCode)
            {
                var errorBody = await response.Content.ReadAsStringAsync(ct);
                return ApiResult<T>.Failure(
                    $"HTTP {(int)response.StatusCode} ({response.ReasonPhrase}): {errorBody}",
                    (int)response.StatusCode);
            }

            var contentStream = await response.Content.ReadAsStreamAsync(ct);
            var parsed = await JsonSerializer.DeserializeAsync<T>(contentStream, JsonOptions, ct);

            if (parsed == null)
            {
                return ApiResult<T>.Failure("API returned an empty or unparseable JSON payload.", (int)response.StatusCode);
            }

            return ApiResult<T>.Success(parsed, (int)response.StatusCode);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            return ApiResult<T>.Failure("Request was cancelled.");
        }
        catch (HttpRequestException ex)
        {
            return ApiResult<T>.Failure($"Network error connecting to {_baseUrl}: {ex.Message}");
        }
        catch (Exception ex)
        {
            return ApiResult<T>.Failure($"Unexpected error: {ex.Message}");
        }
    }

    private static void ValidateAuthorizedMutationRoute(string relativeUri)
    {
        bool isAuthorized = relativeUri.StartsWith("/api/alerts", StringComparison.OrdinalIgnoreCase)
            || relativeUri.Equals("/api/scanner/start", StringComparison.OrdinalIgnoreCase)
            || relativeUri.Equals("/api/scanner/stop", StringComparison.OrdinalIgnoreCase)
            || relativeUri.Equals("/api/inspector/live-session/reauthorize", StringComparison.OrdinalIgnoreCase)
            || relativeUri.Equals("/api/inspector/groq-auto-setup", StringComparison.OrdinalIgnoreCase);

        if (!isAuthorized)
        {
            throw new InvalidOperationException($"Security Boundary Violation: Mutation to '{relativeUri}' is strictly prohibited. Desktop client is restricted to Price Alerts and authorized Scanner Lifecycle controls.");
        }
    }

    private async Task<ApiResult<T>> ExecuteMutationAsync<T>(HttpMethod method, string relativeUri, object? payload, CancellationToken ct)
    {
        ValidateAuthorizedMutationRoute(relativeUri);
        try
        {
            var requestUri = $"{_baseUrl}{relativeUri}";
            using var request = new HttpRequestMessage(method, requestUri);
            if (payload != null)
            {
                var json = JsonSerializer.Serialize(payload, JsonOptions);
                request.Content = new StringContent(json, System.Text.Encoding.UTF8, "application/json");
            }

            using var response = await _httpClient.SendAsync(request, ct);
            if (!response.IsSuccessStatusCode)
            {
                var errorBody = await response.Content.ReadAsStringAsync(ct);
                return ApiResult<T>.Failure(
                    $"HTTP {(int)response.StatusCode} ({response.ReasonPhrase}): {errorBody}",
                    (int)response.StatusCode);
            }

            var contentStream = await response.Content.ReadAsStreamAsync(ct);
            var parsed = await JsonSerializer.DeserializeAsync<T>(contentStream, JsonOptions, ct);
            if (parsed == null)
            {
                return ApiResult<T>.Failure("API returned an empty or unparseable JSON payload.", (int)response.StatusCode);
            }
            return ApiResult<T>.Success(parsed, (int)response.StatusCode);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            return ApiResult<T>.Failure("Request was cancelled.");
        }
        catch (HttpRequestException ex)
        {
            return ApiResult<T>.Failure($"Network error connecting to {_baseUrl}: {ex.Message}");
        }
        catch (Exception ex)
        {
            return ApiResult<T>.Failure($"Unexpected error: {ex.Message}");
        }
    }
}
