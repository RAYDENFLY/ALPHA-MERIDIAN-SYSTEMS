using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Globalization;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;

namespace SignalGenerator.Desktop.ViewModels;

public partial class CryptoNewsViewModel : ViewModelBase
{
    private readonly IRssNewsService _newsService;
    private readonly IPollingCoordinator? _pollingCoordinator;
    private CancellationTokenSource? _cts;

    [ObservableProperty]
    private string _terminalTitle = "GLOBAL MARKET INTELLIGENCE";

    [ObservableProperty]
    private ObservableCollection<NewsArticleDto> _newsArticles = new();

    [ObservableProperty]
    private NewsArticleDto? _selectedArticle;

    [ObservableProperty]
    private string _filterCategory = "ALL";

    [ObservableProperty]
    private string _filterPublisher = "ALL";

    [ObservableProperty]
    private string _searchQuery = string.Empty;

    [ObservableProperty]
    private string _startDateText = string.Empty;

    [ObservableProperty]
    private string _endDateText = string.Empty;

    [ObservableProperty]
    private int _itemCount;

    [ObservableProperty]
    private string _lastWireTimeUtc = "--:--:--";

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private bool _isSummarizing;

    [ObservableProperty]
    private string _groqSummaryNotice = "GROQ_NEWS_SUMMARY: Isolated Server-Side Secret Architecture Configured";

    [ObservableProperty]
    private string _summaryStatusText = "Select an article to view excerpt or generate safe contextual summary.";

    [ObservableProperty]
    private string _aiSummaryContent = string.Empty;

    [ObservableProperty]
    private string _aiSummaryGeneratedTime = string.Empty;

    [ObservableProperty]
    private bool _hasAiSummary;

    [ObservableProperty]
    private ObservableCollection<FeedSourceStatusDto> _feedSources = new();

    public CryptoNewsViewModel(IRssNewsService newsService, IPollingCoordinator? pollingCoordinator = null)
    {
        _newsService = newsService ?? throw new ArgumentNullException(nameof(newsService));
        _pollingCoordinator = pollingCoordinator;

        _ = RefreshNewsAsync();
    }

    [RelayCommand]
    public async Task RefreshNewsAsync()
    {
        if (IsLoading) return;
        IsLoading = true;

        _cts?.Cancel();
        _cts?.Dispose();
        _cts = new CancellationTokenSource();

        try
        {
            var articles = await _newsService.FetchLatestNewsAsync(_cts.Token);
            FeedSources.Clear();
            foreach (var s in _newsService.SourceStatuses)
            {
                FeedSources.Add(s);
            }

            await ApplyFilterInternalAsync();
            LastWireTimeUtc = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";
            if (NewsArticles.Count > 0 && SelectedArticle == null)
            {
                SelectedArticle = NewsArticles[0];
                SelectArticle(NewsArticles[0]);
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            SummaryStatusText = $"Error refreshing news feed: {ex.Message}";
        }
        finally
        {
            IsLoading = false;
        }
    }

    [RelayCommand]
    public async Task FilterByCategoryAsync(string category)
    {
        FilterCategory = category;
        await ApplyFilterInternalAsync();
    }

    [RelayCommand]
    public async Task FilterByPublisherAsync(string publisher)
    {
        FilterPublisher = publisher;
        await ApplyFilterInternalAsync();
    }

    [RelayCommand]
    public async Task ApplySearchAsync()
    {
        await ApplyFilterInternalAsync();
    }

    [RelayCommand]
    public async Task ResetFilterAsync()
    {
        FilterCategory = "ALL";
        FilterPublisher = "ALL";
        SearchQuery = string.Empty;
        StartDateText = string.Empty;
        EndDateText = string.Empty;
        await ApplyFilterInternalAsync();
    }

    [RelayCommand]
    public void SelectArticle(NewsArticleDto article)
    {
        SelectedArticle = article;
        if (article != null)
        {
            HasAiSummary = article.IsAiSummarized && !string.IsNullOrWhiteSpace(article.AiSummary);
            AiSummaryContent = article.AiSummary ?? string.Empty;
            AiSummaryGeneratedTime = article.SummaryGeneratedUtc.HasValue 
                ? article.SummaryGeneratedUtc.Value.ToString("yyyy-MM-dd HH:mm:ss") + " UTC" 
                : string.Empty;

            SummaryStatusText = HasAiSummary 
                ? $"[AI SUMMARY AVAILABLE] Generated at {AiSummaryGeneratedTime}" 
                : $"[SOURCE EXCERPT] {article.Excerpt}";
        }
        else
        {
            HasAiSummary = false;
            AiSummaryContent = string.Empty;
            AiSummaryGeneratedTime = string.Empty;
            SummaryStatusText = "No article selected.";
        }
    }

    [RelayCommand]
    public void OpenSourceArticle()
    {
        if (SelectedArticle == null || string.IsNullOrWhiteSpace(SelectedArticle.Url)) return;

        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = SelectedArticle.Url,
                UseShellExecute = true
            };
            Process.Start(psi);
        }
        catch (Exception ex)
        {
            SummaryStatusText = $"Could not open article source: {ex.Message}";
        }
    }

    [RelayCommand]
    public async Task GenerateSummaryAsync()
    {
        if (SelectedArticle == null || IsSummarizing) return;
        IsSummarizing = true;
        SummaryStatusText = "Synthesizing AI summary with Groq (isolated server-side secret architecture)...";

        try
        {
            var updated = await _newsService.SummarizeArticleAsync(SelectedArticle.Id);
            if (updated != null)
            {
                SelectedArticle = updated;
                SelectArticle(updated);
            }
        }
        catch (Exception ex)
        {
            SummaryStatusText = $"Summarization failed: {ex.Message}";
        }
        finally
        {
            IsSummarizing = false;
        }
    }

    private async Task ApplyFilterInternalAsync()
    {
        DateTime? startDate = null;
        if (!string.IsNullOrWhiteSpace(StartDateText) && DateTime.TryParse(StartDateText, CultureInfo.InvariantCulture, DateTimeStyles.None, out var sdt))
        {
            startDate = sdt.ToUniversalTime();
        }

        DateTime? endDate = null;
        if (!string.IsNullOrWhiteSpace(EndDateText) && DateTime.TryParse(EndDateText, CultureInfo.InvariantCulture, DateTimeStyles.None, out var edt))
        {
            endDate = edt.ToUniversalTime();
        }

        var filtered = await _newsService.FilterNewsAsync(
            category: FilterCategory,
            searchQuery: SearchQuery,
            startDateUtc: startDate,
            endDateUtc: endDate,
            publisher: FilterPublisher);

        NewsArticles.Clear();
        foreach (var a in filtered)
        {
            NewsArticles.Add(a);
        }

        ItemCount = NewsArticles.Count;

        if (NewsArticles.Count > 0)
        {
            if (SelectedArticle == null || !NewsArticles.Contains(SelectedArticle))
            {
                SelectedArticle = NewsArticles[0];
                SelectArticle(NewsArticles[0]);
            }
        }
        else
        {
            SelectedArticle = null;
            SelectArticle(null!);
            SummaryStatusText = "NO ARTICLES FOUND MATCHING CURRENT FILTER CRITERIA.";
        }
    }
}
