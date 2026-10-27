using System;
using System.IO;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class SettingsServiceTests : IDisposable
{
    private readonly string _tempSettingsPath;

    public SettingsServiceTests()
    {
        _tempSettingsPath = Path.Combine(Path.GetTempPath(), $"sg_settings_test_{Guid.NewGuid():N}.json");
    }

    public void Dispose()
    {
        if (File.Exists(_tempSettingsPath))
        {
            try { File.Delete(_tempSettingsPath); } catch { }
        }
    }

    [Fact]
    public void Constructor_LoadsDefaultSettings_WhenFileDoesNotExist()
    {
        var service = new SettingsService(_tempSettingsPath);
        Assert.NotNull(service.CurrentSettings);
        Assert.Equal("http://127.0.0.1:8000", service.CurrentSettings.BaseUrl);
        Assert.Equal(3, service.CurrentSettings.PollingIntervalSeconds);
        Assert.True(service.CurrentSettings.EnableAutoPolling);
    }

    [Fact]
    public void SaveSettings_PersistsToFileAndLoadsBack()
    {
        var service = new SettingsService(_tempSettingsPath);
        var custom = new DesktopSettings
        {
            BaseUrl = "http://localhost:9000",
            PollingIntervalSeconds = 5,
            EnableAutoPolling = false,
            Theme = "Light"
        };

        service.SaveSettings(custom);

        var service2 = new SettingsService(_tempSettingsPath);
        Assert.Equal("http://localhost:9000", service2.CurrentSettings.BaseUrl);
        Assert.Equal(5, service2.CurrentSettings.PollingIntervalSeconds);
        Assert.False(service2.CurrentSettings.EnableAutoPolling);
        Assert.Equal("Light", service2.CurrentSettings.Theme);
    }

    [Fact]
    public void UpdateBaseUrl_NormalizesTrailingSlash()
    {
        var service = new SettingsService(_tempSettingsPath);
        service.UpdateBaseUrl("http://127.0.0.1:8000///");
        Assert.Equal("http://127.0.0.1:8000", service.CurrentSettings.BaseUrl);
    }

    [Fact]
    public void UpdatePollingInterval_EnforcesMinimumValue()
    {
        var service = new SettingsService(_tempSettingsPath);
        service.UpdatePollingInterval(0);
        Assert.Equal(1, service.CurrentSettings.PollingIntervalSeconds);
    }
}
