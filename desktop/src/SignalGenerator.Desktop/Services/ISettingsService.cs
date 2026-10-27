using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public interface ISettingsService
{
    DesktopSettings CurrentSettings { get; }
    void SaveSettings(DesktopSettings settings);
    void UpdateBaseUrl(string url);
    void UpdatePollingInterval(int seconds);
}
