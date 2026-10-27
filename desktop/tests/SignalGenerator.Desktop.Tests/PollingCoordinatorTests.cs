using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class PollingCoordinatorTests
{
    private class FakeApiClient : IApiClient
    {
        public string BaseUrl { get; private set; } = "http://127.0.0.1:8000";
        public bool ShouldFailStatus { get; set; }
        public int StatusPollCount { get; private set; }

        public void SetBaseUrl(string baseUrl) => BaseUrl = baseUrl;

        public Task<ApiResult<SystemStatusDto>> GetStatusAsync(CancellationToken ct = default)
        {
            StatusPollCount++;
            if (ShouldFailStatus)
            {
                return Task.FromResult(ApiResult<SystemStatusDto>.Failure("Simulated network outage", 503));
            }

            return Task.FromResult(ApiResult<SystemStatusDto>.Success(new SystemStatusDto
            {
                Mode = "LIVE_AUTO",
                SessionState = "ACTIVE",
                SessionId = "SESS_TEST",
                Account = new AccountSnapshotDto
                {
                    Status = "CONNECTED",
                    TotalEquityUsdt = 14.50m
                }
            }));
        }

        public Task<ApiResult<LiveSessionEnvelopeDto>> GetLiveSessionAsync(CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<LiveSessionEnvelopeDto>.Success(new LiveSessionEnvelopeDto
            {
                Status = "SUCCESS",
                Data = new LiveSessionDataDto
                {
                    DailyFilledEntries = 1,
                    EntryLimit = 3
                }
            }));
        }

        public Task<ApiResult<JournalSummaryDto>> GetJournalSummaryAsync(CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<JournalSummaryDto>.Success(new JournalSummaryDto
            {
                Mode = "LIVE",
                TotalClosedTrades = 8,
                WinRatePct = 25.0m
            }));
        }

        public Task<ApiResult<ScreenerResponseDto>> GetScreenerMarketsAsync(string? search = null, int page = 1, int pageSize = 25, CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<ScreenerResponseDto>.Success(new ScreenerResponseDto()));
        }

        public Task<ApiResult<List<string>>> GetSymbolsAsync(CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<List<string>>.Success(new List<string> { "BZUSDT", "PLAYUSDT" }));
        }

        public Task<ApiResult<AutotraderScreeningLogResponseDto>> GetScreeningLogAsync(CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<AutotraderScreeningLogResponseDto>.Success(new AutotraderScreeningLogResponseDto()));
        }

        public Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<List<AlertItemDto>>.Success(new List<AlertItemDto>()));
        }

        public Task<ApiResult<RetailPositioningResponseDto>> GetRetailPositioningAsync(string symbol, CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<RetailPositioningResponseDto>.Success(new RetailPositioningResponseDto { Symbol = symbol }));
        }
    }

    private class FakeSettingsService : ISettingsService
    {
        public DesktopSettings CurrentSettings { get; set; } = new()
        {
            BaseUrl = "http://127.0.0.1:8000",
            PollingIntervalSeconds = 1,
            EnableAutoPolling = true
        };

        public void SaveSettings(DesktopSettings settings) => CurrentSettings = settings;
        public void UpdateBaseUrl(string url) => CurrentSettings.BaseUrl = url;
        public void UpdatePollingInterval(int seconds) => CurrentSettings.PollingIntervalSeconds = seconds;
    }

    [Fact]
    public async Task PollOnceAsync_DispatchesEvents_WhenSuccessful()
    {
        var fakeClient = new FakeApiClient();
        var fakeSettings = new FakeSettingsService();
        using var coordinator = new PollingCoordinator(fakeClient, fakeSettings);

        SystemStatusDto? receivedStatus = null;
        LiveSessionEnvelopeDto? receivedSession = null;
        JournalSummaryDto? receivedJournal = null;
        bool? connectionState = null;

        coordinator.OnStatusUpdated += s => receivedStatus = s;
        coordinator.OnLiveSessionUpdated += sess => receivedSession = sess;
        coordinator.OnJournalUpdated += j => receivedJournal = j;
        coordinator.OnConnectionStatusChanged += c => connectionState = c;

        await coordinator.PollOnceAsync(CancellationToken.None);

        Assert.NotNull(receivedStatus);
        Assert.Equal("LIVE_AUTO", receivedStatus.Mode);
        Assert.Equal("ACTIVE", receivedStatus.SessionState);

        Assert.NotNull(receivedSession);
        Assert.Equal(1, receivedSession.Data?.DailyFilledEntries);

        Assert.NotNull(receivedJournal);
        Assert.Equal(8, receivedJournal.TotalClosedTrades);

        Assert.True(connectionState);
        Assert.True(coordinator.IsConnected);
        Assert.NotNull(coordinator.LastPollTimeUtc);
        Assert.Null(coordinator.LastError);
    }

    [Fact]
    public async Task PollOnceAsync_DispatchesErrorAndDisconnect_WhenEndpointFails()
    {
        var fakeClient = new FakeApiClient { ShouldFailStatus = true };
        var fakeSettings = new FakeSettingsService();
        using var coordinator = new PollingCoordinator(fakeClient, fakeSettings);

        string? receivedError = null;
        bool? connectionState = null;

        coordinator.OnPollingError += err => receivedError = err;
        coordinator.OnConnectionStatusChanged += c => connectionState = c;

        await coordinator.PollOnceAsync(CancellationToken.None);

        Assert.NotNull(receivedError);
        Assert.Contains("Simulated network outage", receivedError);
        Assert.False(coordinator.IsConnected);
        Assert.Equal(false, connectionState);
    }

    [Fact]
    public void StartAndStop_ControlsAutoPollingState()
    {
        var fakeClient = new FakeApiClient();
        var fakeSettings = new FakeSettingsService();
        using var coordinator = new PollingCoordinator(fakeClient, fakeSettings);

        Assert.False(coordinator.IsAutoPollingEnabled);

        coordinator.Start();
        Assert.True(coordinator.IsAutoPollingEnabled);

        coordinator.Stop();
        Assert.False(coordinator.IsAutoPollingEnabled);
    }
}
