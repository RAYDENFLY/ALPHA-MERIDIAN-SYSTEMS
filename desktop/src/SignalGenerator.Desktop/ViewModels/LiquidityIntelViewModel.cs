using CommunityToolkit.Mvvm.ComponentModel;

namespace SignalGenerator.Desktop.ViewModels;

public partial class LiquidityIntelViewModel : ViewModelBase
{
    [ObservableProperty]
    private string _symbol = "BZUSDT";

    [ObservableProperty]
    private string _status = "Monitoring L2 Cross-Book & Whale Clusters";

    [ObservableProperty]
    private string _bidWallLevel = "$108.50 (24.5 BTC)";

    [ObservableProperty]
    private string _askWallLevel = "$112.80 (38.2 BTC)";

    [ObservableProperty]
    private string _whaleEventSummary = "No recent spoofing detected. Cumulative delta neutral.";
}
