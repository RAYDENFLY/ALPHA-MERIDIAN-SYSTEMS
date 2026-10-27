using System;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public interface IPollingCoordinator : IDisposable
{
    bool IsConnected { get; }
    DateTime? LastPollTimeUtc { get; }
    string? LastError { get; }
    bool IsAutoPollingEnabled { get; }

    event Action<SystemStatusDto>? OnStatusUpdated;
    event Action<LiveSessionEnvelopeDto>? OnLiveSessionUpdated;
    event Action<JournalSummaryDto>? OnJournalUpdated;
    event Action<string>? OnPollingError;
    event Action<bool>? OnConnectionStatusChanged;

    void Start();
    void Stop();
    Task TriggerPollNowAsync();
    void SetInterval(int seconds);
}
