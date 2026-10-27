using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class AlertsViewModel : ViewModelBase
{
    private readonly IApiClient _apiClient;

    [ObservableProperty]
    private string _discordWebhookStatus = "CONFIGURED (MAINNET DISCORD CHANNEL)";

    [ObservableProperty]
    private int _activeAlertsCount = 0;

    [ObservableProperty]
    private string _latestAlertMessage = "Monitoring price levels against Aster DEX L2 book.";

    [ObservableProperty]
    private ObservableCollection<AlertItemDto> _alerts = new();

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private string _lastRefreshUtc = "--:--:--";

    public AlertsViewModel(IApiClient apiClient)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _ = LoadAlertsAsync();
    }

    [RelayCommand]
    public async Task LoadAlertsAsync()
    {
        if (IsLoading) return;
        IsLoading = true;

        try
        {
            var res = await _apiClient.GetAlertsAsync();
            if (res.IsSuccess && res.Value != null)
            {
                Alerts.Clear();
                int activeCount = 0;
                foreach (var a in res.Value)
                {
                    Alerts.Add(a);
                    if (a.IsActive) activeCount++;
                }
                ActiveAlertsCount = activeCount;
                if (Alerts.Count > 0)
                {
                    var first = Alerts[0];
                    LatestAlertMessage = $"Alert {first.AlertId}: {first.Symbol} {first.Direction} ${first.TargetPrice:F4} (Status: {first.Status})";
                }
                LastRefreshUtc = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";
            }
        }
        catch
        {
            // Preserve state on failure
        }
        finally
        {
            IsLoading = false;
        }
    }
}
