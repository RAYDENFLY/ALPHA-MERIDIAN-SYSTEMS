using System;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class MarketParticipantsViewModel : ViewModelBase
{
    private readonly IApiClient _apiClient;

    [ObservableProperty]
    private string _symbol = "BTCUSDT";

    [ObservableProperty]
    private string _provider = "Bybit Derivatives";

    [ObservableProperty]
    private string _metricName = "Account Long/Short Ratio";

    [ObservableProperty]
    private string _metricDefinition = "Percentage of net account holders LONG vs SHORT.";

    [ObservableProperty]
    private string _datasetStatus = "AVAILABLE";

    [ObservableProperty]
    private string _cotDataSourceStatus = "CFTC_COT_WEEKLY (INGESTION_PENDING)";

    [ObservableProperty]
    private string _cotReportSummary = "CFTC Commitment of Traders (COT) Disaggregated Futures data ingestion is scheduled for weekly Friday settlement.";

    [ObservableProperty]
    private string _currentBuyRatio = "--%";

    [ObservableProperty]
    private string _currentSellRatio = "--%";

    [ObservableProperty]
    private string _retailSentimentBias = "NEUTRAL";

    [ObservableProperty]
    private string _retailSentimentColor = "#FFB300";

    [ObservableProperty]
    private ObservableCollection<RetailObservationDto> _observations = new();

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private string _lastRefreshUtc = "--:--:--";

    [ObservableProperty]
    private string? _errorMessage;

    public MarketParticipantsViewModel(IApiClient apiClient)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
    }

    [RelayCommand]
    public async Task LoadPositioningAsync()
    {
        if (IsLoading) return;
        IsLoading = true;
        ErrorMessage = null;

        try
        {
            var res = await _apiClient.GetRetailPositioningAsync(Symbol);
            if (res.IsSuccess && res.Value != null)
            {
                Provider = res.Value.Provider ?? "Bybit";
                MetricName = res.Value.Metric ?? "Account Ratio";
                MetricDefinition = res.Value.MetricDefinition ?? "Long and short holder percentages";
                DatasetStatus = res.Value.Status ?? "AVAILABLE";

                Observations.Clear();
                foreach (var obs in res.Value.Observations)
                {
                    Observations.Add(obs);
                }

                if (Observations.Count > 0)
                {
                    var latest = Observations[^1];
                    CurrentBuyRatio = (latest.BuyRatio * 100).ToString("F1", CultureInfo.InvariantCulture) + "%";
                    CurrentSellRatio = (latest.SellRatio * 100).ToString("F1", CultureInfo.InvariantCulture) + "%";

                    if (latest.BuyRatio > 0.55m)
                    {
                        RetailSentimentBias = "RETAIL BULLISH (LONG HEAVY)";
                        RetailSentimentColor = "#00E676";
                    }
                    else if (latest.SellRatio > 0.55m)
                    {
                        RetailSentimentBias = "RETAIL BEARISH (SHORT HEAVY)";
                        RetailSentimentColor = "#FF5252";
                    }
                    else
                    {
                        RetailSentimentBias = "BALANCED / EQUILIBRIUM";
                        RetailSentimentColor = "#FFB300";
                    }
                }
                LastRefreshUtc = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";
            }
            else
            {
                ErrorMessage = res.ErrorMessage ?? "Failed to fetch retail positioning.";
            }
        }
        catch (Exception ex)
        {
            ErrorMessage = ex.Message;
        }
        finally
        {
            IsLoading = false;
        }
    }
}
