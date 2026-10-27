using System;
using System.IO;
using System.Text.Json;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public class SettingsService : ISettingsService
{
    private readonly string _settingsFilePath;
    private DesktopSettings _settings;

    public DesktopSettings CurrentSettings => _settings;

    public SettingsService(string? customPath = null)
    {
        if (!string.IsNullOrWhiteSpace(customPath))
        {
            _settingsFilePath = customPath;
        }
        else
        {
            var appData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            var appDir = Path.Combine(appData, "SignalGeneratorDesktop");
            Directory.CreateDirectory(appDir);
            _settingsFilePath = Path.Combine(appDir, "settings.json");
        }

        _settings = LoadSettings();
    }

    private DesktopSettings LoadSettings()
    {
        try
        {
            if (File.Exists(_settingsFilePath))
            {
                var json = File.ReadAllText(_settingsFilePath);
                var loaded = JsonSerializer.Deserialize<DesktopSettings>(json);
                if (loaded != null)
                {
                    return loaded;
                }
            }
        }
        catch
        {
            // Fall back to default settings on read error
        }

        return new DesktopSettings();
    }

    public void SaveSettings(DesktopSettings settings)
    {
        _settings = settings ?? throw new ArgumentNullException(nameof(settings));
        try
        {
            var json = JsonSerializer.Serialize(_settings, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(_settingsFilePath, json);
        }
        catch
        {
            // Best effort persistence
        }
    }

    public void UpdateBaseUrl(string url)
    {
        if (string.IsNullOrWhiteSpace(url)) return;
        _settings.BaseUrl = url.TrimEnd('/');
        SaveSettings(_settings);
    }

    public void UpdatePollingInterval(int seconds)
    {
        if (seconds < 1) seconds = 1;
        _settings.PollingIntervalSeconds = seconds;
        SaveSettings(_settings);
    }
}
