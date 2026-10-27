using CommunityToolkit.Mvvm.ComponentModel;

namespace SignalGenerator.Desktop.ViewModels;

public partial class AlertsViewModel : ViewModelBase
{
    [ObservableProperty]
    private string _discordWebhookStatus = "CONFIGURED";

    [ObservableProperty]
    private int _activeAlertsCount = 0;

    [ObservableProperty]
    private string _latestAlertMessage = "No active trigger events.";
}
