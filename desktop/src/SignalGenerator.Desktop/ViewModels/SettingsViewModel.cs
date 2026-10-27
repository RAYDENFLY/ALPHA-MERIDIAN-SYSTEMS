using System;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class SettingsViewModel : ViewModelBase
{
    private readonly ISettingsService _settingsService;
    private readonly IApiClient _apiClient;
    private readonly IPollingCoordinator _pollingCoordinator;

    [ObservableProperty]
    private string _baseUrl;

    [ObservableProperty]
    private int _pollingIntervalSeconds;

    [ObservableProperty]
    private bool _enableAutoPolling;

    [ObservableProperty]
    private string _theme;

    [ObservableProperty]
    private string? _saveMessage;

    public SettingsViewModel(
        ISettingsService settingsService,
        IApiClient apiClient,
        IPollingCoordinator pollingCoordinator)
    {
        _settingsService = settingsService ?? throw new ArgumentNullException(nameof(settingsService));
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _pollingCoordinator = pollingCoordinator ?? throw new ArgumentNullException(nameof(pollingCoordinator));

        var current = _settingsService.CurrentSettings;
        _baseUrl = current.BaseUrl;
        _pollingIntervalSeconds = current.PollingIntervalSeconds;
        _enableAutoPolling = current.EnableAutoPolling;
        _theme = current.Theme;
    }

    [RelayCommand]
    public void SaveSettings()
    {
        var settings = new DesktopSettings
        {
            BaseUrl = BaseUrl.TrimEnd('/'),
            PollingIntervalSeconds = PollingIntervalSeconds,
            EnableAutoPolling = EnableAutoPolling,
            Theme = Theme
        };

        _settingsService.SaveSettings(settings);
        _apiClient.SetBaseUrl(settings.BaseUrl);
        _pollingCoordinator.SetInterval(settings.PollingIntervalSeconds);

        if (settings.EnableAutoPolling)
        {
            _pollingCoordinator.Start();
        }
        else
        {
            _pollingCoordinator.Stop();
        }

        SaveMessage = $"Settings successfully saved at {DateTime.Now:HH:mm:ss}.";
    }
}
