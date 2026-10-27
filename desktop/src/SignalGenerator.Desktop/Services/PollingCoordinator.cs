using System;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Threading;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public class PollingCoordinator : IPollingCoordinator
{
    private readonly IApiClient _apiClient;
    private readonly ISettingsService _settingsService;
    private readonly SemaphoreSlim _pollLock = new(1, 1);
    private CancellationTokenSource? _cts;
    private Task? _pollLoopTask;

    private int _intervalSeconds;
    private bool _isDisposed;
    private bool? _isConnected = null;
    private string? _lastError;
    private DateTime? _lastPollTimeUtc;

    public bool IsConnected => _isConnected == true;
    public DateTime? LastPollTimeUtc => _lastPollTimeUtc;
    public string? LastError => _lastError;
    public bool IsAutoPollingEnabled => _cts != null && !_cts.IsCancellationRequested;

    public event Action<SystemStatusDto>? OnStatusUpdated;
    public event Action<LiveSessionEnvelopeDto>? OnLiveSessionUpdated;
    public event Action<JournalSummaryDto>? OnJournalUpdated;
    public event Action<string>? OnPollingError;
    public event Action<bool>? OnConnectionStatusChanged;

    public PollingCoordinator(IApiClient apiClient, ISettingsService settingsService)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _settingsService = settingsService ?? throw new ArgumentNullException(nameof(settingsService));
        _intervalSeconds = Math.Max(1, settingsService.CurrentSettings?.PollingIntervalSeconds ?? 3);
    }

    public void Start()
    {
        if (_cts != null && !_cts.IsCancellationRequested)
        {
            return; // Already running
        }

        _cts = new CancellationTokenSource();
        _pollLoopTask = Task.Run(() => PollingLoopAsync(_cts.Token));
    }

    public void Stop()
    {
        _cts?.Cancel();
        _cts?.Dispose();
        _cts = null;
    }

    public void SetInterval(int seconds)
    {
        _intervalSeconds = Math.Max(1, seconds);
    }

    public async Task TriggerPollNowAsync()
    {
        await PollOnceAsync(CancellationToken.None);
    }

    private async Task PollingLoopAsync(CancellationToken ct)
    {
        // Initial immediate poll
        await PollOnceAsync(ct);

        while (!ct.IsCancellationRequested)
        {
            try
            {
                await Task.Delay(TimeSpan.FromSeconds(_intervalSeconds), ct);
                if (ct.IsCancellationRequested) break;
                await PollOnceAsync(ct);
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception ex)
            {
                HandleError($"Polling cycle error: {ex.Message}");
            }
        }
    }

    public async Task PollOnceAsync(CancellationToken ct)
    {
        // Concurrency guard: avoid overlapping calls if previous poll is still executing
        if (!await _pollLock.WaitAsync(0, ct))
        {
            return;
        }

        try
        {
            var statusTask = _apiClient.GetStatusAsync(ct);
            var sessionTask = _apiClient.GetLiveSessionAsync(ct);
            var journalTask = _apiClient.GetJournalSummaryAsync(ct);

            await Task.WhenAll(statusTask, sessionTask, journalTask);

            var statusRes = await statusTask;
            var sessionRes = await sessionTask;
            var journalRes = await journalTask;

            if (statusRes.IsSuccess && statusRes.Value != null)
            {
                UpdateConnectionStatus(true);
                _lastError = null;
                _lastPollTimeUtc = DateTime.UtcNow;
                DispatchSafe(OnStatusUpdated, statusRes.Value);
            }
            else
            {
                UpdateConnectionStatus(false);
                HandleError(statusRes.ErrorMessage ?? "Status poll failed");
            }

            if (sessionRes.IsSuccess && sessionRes.Value != null)
            {
                DispatchSafe(OnLiveSessionUpdated, sessionRes.Value);
            }

            if (journalRes.IsSuccess && journalRes.Value != null)
            {
                DispatchSafe(OnJournalUpdated, journalRes.Value);
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            // Expected cancellation
        }
        catch (Exception ex)
        {
            UpdateConnectionStatus(false);
            HandleError($"Poll execution error: {ex.Message}");
        }
        finally
        {
            _pollLock.Release();
        }
    }

    private void UpdateConnectionStatus(bool connected)
    {
        if (_isConnected != connected)
        {
            _isConnected = connected;
            DispatchSafe(OnConnectionStatusChanged, connected);
        }
    }

    private void HandleError(string error)
    {
        _lastError = error;
        DispatchSafe(OnPollingError, error);
    }

    private void DispatchSafe<T>(Action<T>? action, T arg)
    {
        if (action == null) return;
        try
        {
            if (Dispatcher.UIThread != null && !Dispatcher.UIThread.CheckAccess())
            {
                Dispatcher.UIThread.Post(() => action.Invoke(arg));
                return;
            }
        }
        catch
        {
            // Dispatcher may not be initialized in headless test runs
        }

        action.Invoke(arg);
    }

    public void Dispose()
    {
        if (_isDisposed) return;
        _isDisposed = true;
        Stop();
        _pollLock.Dispose();
    }
}
