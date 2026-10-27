using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
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
    private List<NewsArticleDto> _allArticles = new();

    [ObservableProperty]
    private string _terminalTitle = "GLOBAL MARKET INTELLIGENCE";

    [ObservableProperty]
    private ObservableCollection<NewsArticleDto> _newsArticles = new();

    [ObservableProperty]
    private NewsArticleDto? _selectedArticle;

    [ObservableProperty]
    private string _filterCategory = "ALL";

    [ObservableProperty]
    private int _itemCount;

    [ObservableProperty]
    private string _lastWireTimeUtc = "--:--:--";

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private string _groqSummaryNotice = "GROQ_NEWS_SUMMARY: Isolated Server-Side Secret Architecture Configured";

    [ObservableProperty]
    private string _summaryStatusText = "Select an article to view excerpt or generate safe contextual summary.";

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
            _allArticles = articles;
            ApplyFilter();
            LastWireTimeUtc = DateTime.UtcNow.ToString("HH:mm:ss") + " UTC";
            if (NewsArticles.Count > 0 && SelectedArticle == null)
            {
                SelectedArticle = NewsArticles[0];
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
    public void FilterByCategory(string category)
    {
        FilterCategory = category;
        ApplyFilter();
    }

    [RelayCommand]
    public void SelectArticle(NewsArticleDto article)
    {
        SelectedArticle = article;
        if (article != null)
        {
            SummaryStatusText = article.IsAiSummarized 
                ? $"[AI SUMMARY - {article.SummaryGeneratedUtc:HH:mm:ss}] {article.AiSummary}" 
                : $"[SOURCE EXCERPT] {article.Excerpt}";
        }
    }

    [RelayCommand]
    public async Task GenerateSummaryAsync()
    {
        if (SelectedArticle == null) return;
        SummaryStatusText = "Synthesizing safe summary (preserving GROQ_NEWS_SUMMARY isolation)...";

        var updated = await _newsService.SummarizeArticleAsync(SelectedArticle.Id);
        if (updated != null)
        {
            SelectedArticle = updated;
            SummaryStatusText = $"[AI SUMMARY] {updated.AiSummary}";
        }
    }

    private void ApplyFilter()
    {
        NewsArticles.Clear();
        foreach (var a in _allArticles)
        {
            if (FilterCategory == "ALL" || a.Category.Equals(FilterCategory, StringComparison.OrdinalIgnoreCase))
            {
                NewsArticles.Add(a);
            }
        }
        ItemCount = NewsArticles.Count;
    }
}
