using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

/// <summary>
/// Strictly read-only HTTP API client communicating with SignalGenerator FastAPI backend.
/// Invariant: Only HTTP GET requests are issued.
/// </summary>
public class ApiClient : IApiClient
{
    private readonly HttpClient _httpClient;
    private string _baseUrl;
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        NumberHandling = JsonNumberHandling.AllowReadingFromString
    };

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
        CancellationToken ct = default)
    {
        var endpoint = $"/api/screener/markets?page={page}&page_size={pageSize}";
        if (!string.IsNullOrWhiteSpace(search))
        {
            endpoint += $"&search={Uri.EscapeDataString(search.Trim())}";
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

    public async Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(CancellationToken ct = default)
    {
        return await ExecuteGetAsync<List<AlertItemDto>>("/api/alerts", ct);
    }

    public async Task<ApiResult<RetailPositioningResponseDto>> GetRetailPositioningAsync(string symbol = "BTCUSDT", CancellationToken ct = default)
    {
        return await ExecuteGetAsync<RetailPositioningResponseDto>($"/api/research/retail_positioning?symbol={Uri.EscapeDataString(symbol)}", ct);
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
}
