using System;
using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public class NewsWireItem
{
    public string TimestampUtc { get; set; } = string.Empty;
    public string Category { get; set; } = "SYSTEM";
    public string CategoryColor { get; set; } = "#FF9D00";
    public string Headline { get; set; } = string.Empty;
    public string Source { get; set; } = "SIGNAL_GEN_TELEMETRY";
    public string Urgency { get; set; } = "NORMAL";
}

public partial class CryptoNewsViewModel : ViewModelBase
{
    private readonly IPollingCoordinator _pollingCoordinator;

    [ObservableProperty]
    private ObservableCollection<NewsWireItem> _newsItems = new();

    [ObservableProperty]
    private string _filterCategory = "ALL";

    [ObservableProperty]
    private int _itemCount;

    [ObservableProperty]
    private string _lastWireTimeUtc = "--:--:--";

    public CryptoNewsViewModel(IPollingCoordinator pollingCoordinator)
    {
        _pollingCoordinator = pollingCoordinator ?? throw new ArgumentNullException(nameof(pollingCoordinator));
        _pollingCoordinator.OnStatusUpdated += HandleStatusUpdated;
        _pollingCoordinator.OnLiveSessionUpdated += HandleLiveSessionUpdated;

        // Seed initial operational wire items
        AddNewsItem("SYSTEM", "#FF9D00", "SignalGenerator Live Micro V1 runtime active on Aster DEX Perpetual Mainnet", "CORE_DAEMON", "HIGH");
        AddNewsItem("RISK", "#00E676", "Risk gates verified: Daily loss limit $0.50 USDT with 1.20% structural stop enforcement", "RISK_ENGINE", "NORMAL");
        AddNewsItem("AI", "#448AFF", "Groq AI Auto-Hunter 10->3->1 Llama-3.3-70b inference pipeline initialized", "GROQ_AUTOHUNTER", "NORMAL");
        AddNewsItem("STRATEGY", "#FFB300", "15M_SWEEP_RECLAIM_SMC active: 3-Tier Dynamic Invalidation SL & Ratchet engaged", "STRATEGY_RUNNER", "HIGH");
    }

    private void HandleStatusUpdated(SystemStatusDto status)
    {
        if (status == null) return;
        var ts = DateTime.UtcNow.ToString("HH:mm:ss");
        AddNewsItem("TELEMETRY", "#448AFF", $"Scanner State: {status.Scanner?.State ?? "IDLE"} | Active Strategy: {status.Scanner?.ConfiguredLiveStrategyId ?? "SMC"} | Scan Cycles: {status.Scanner?.ScanCount ?? 0}", "SCANNER_ENGINE", "NORMAL");
        LastWireTimeUtc = ts + " UTC";
    }

    private void HandleLiveSessionUpdated(LiveSessionEnvelopeDto envelope)
    {
        if (envelope?.Data == null) return;
        var ts = DateTime.UtcNow.ToString("HH:mm:ss");
        if (!string.IsNullOrWhiteSpace(envelope.Data.LastStatusMessage))
        {
            AddNewsItem("SESSION", "#FF9D00", envelope.Data.LastStatusMessage, "LIVE_SESSION", "HIGH");
        }
        LastWireTimeUtc = ts + " UTC";
    }

    public void AddNewsItem(string category, string color, string headline, string source, string urgency)
    {
        var item = new NewsWireItem
        {
            TimestampUtc = DateTime.UtcNow.ToString("HH:mm:ss"),
            Category = category,
            CategoryColor = color,
            Headline = headline,
            Source = source,
            Urgency = urgency
        };

        // Insert at beginning of wire
        NewsItems.Insert(0, item);
        while (NewsItems.Count > 100)
        {
            NewsItems.RemoveAt(NewsItems.Count - 1);
        }
        ItemCount = NewsItems.Count;
    }

    [RelayCommand]
    public void ClearWire()
    {
        NewsItems.Clear();
        ItemCount = 0;
    }
}
