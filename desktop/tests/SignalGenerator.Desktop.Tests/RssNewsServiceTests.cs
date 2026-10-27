using System;
using System.IO;
using System.Net.Http;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class RssNewsServiceTests : IDisposable
{
    private readonly string _testDbPath;
    private readonly NewsStorageService _storage;

    public RssNewsServiceTests()
    {
        _testDbPath = Path.Combine(Path.GetTempPath(), $"ams_rss_test_{Guid.NewGuid():N}.db");
        _storage = new NewsStorageService(_testDbPath);
    }

    public void Dispose()
    {
        try
        {
            if (File.Exists(_testDbPath))
            {
                File.Delete(_testDbPath);
            }
        }
        catch { }
    }

    [Fact]
    public async Task RssNewsService_ProvidesFallbackHeadlines_WhenFeedsOffline()
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(500) };
        var service = new RssNewsService(client, _storage);

        var articles = await service.FetchLatestNewsAsync();

        Assert.NotNull(articles);
        Assert.NotEmpty(articles);
        Assert.Contains(articles, a => a.Publisher == "Federal Reserve" || a.Publisher == "CoinDesk");
    }

    [Fact]
    public async Task RssNewsService_SummarizesArticle_HandlesMissingKeyWithSafeSetupGuidance()
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(500) };
        var service = new RssNewsService(client, _storage);

        var articles = await service.FetchLatestNewsAsync();
        Assert.NotEmpty(articles);

        var first = articles[0];
        var summarized = await service.SummarizeArticleAsync(first.Id);

        Assert.NotNull(summarized);
        // When GROQ_NEWS_SUMMARY is missing, safe setup guidance is returned without exposing keys
        Assert.Contains("GROQ_NEWS_SUMMARY NOT CONFIGURED", summarized.AiSummary);
        Assert.Contains("Set GROQ_NEWS_SUMMARY=<your_groq_api_key>", summarized.AiSummary);
    }

    [Fact]
    public async Task RssNewsService_SummarizesArticle_ReusesCachedSummaryWithoutRequerying()
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(500) };
        var service = new RssNewsService(client, _storage);

        var cachedArticle = new NewsArticleDto
        {
            Id = "art_cached_101",
            Title = "Ethereum Layer 2 Gas Optimizations",
            Publisher = "CoinDesk",
            Url = "https://coindesk.com/eth-l2-gas",
            PublishDateUtc = DateTime.UtcNow,
            Category = "CRYPTO",
            AiSummary = "Pre-computed cached institutional summary.",
            SummaryGeneratedUtc = DateTime.UtcNow.AddMinutes(-10)
        };

        await _storage.SaveArticlesAsync(new[] { cachedArticle });
        await _storage.UpdateArticleSummaryAsync("art_cached_101", cachedArticle.AiSummary, cachedArticle.SummaryGeneratedUtc!.Value);

        // Summarize should return the cached summary immediately
        var result = await service.SummarizeArticleAsync("art_cached_101");

        Assert.NotNull(result);
        Assert.Equal("Pre-computed cached institutional summary.", result.AiSummary);
        Assert.True(result.IsAiSummarized);
    }

    [Fact]
    public void SourceStatuses_TracksKnownFeeds()
    {
        using var client = new HttpClient();
        var service = new RssNewsService(client, _storage);

        Assert.NotNull(service.SourceStatuses);
        Assert.Equal(3, service.SourceStatuses.Count);
        Assert.Contains(service.SourceStatuses, s => s.Name == "CoinDesk");
        Assert.Contains(service.SourceStatuses, s => s.Name == "CoinTelegraph");
        Assert.Contains(service.SourceStatuses, s => s.Name == "Federal Reserve");
    }

    [Fact]
    public async Task FilterNewsAsync_FiltersByPublisherAndCategory()
    {
        using var client = new HttpClient();
        var service = new RssNewsService(client, _storage);

        var a1 = new NewsArticleDto
        {
            Title = "Fed Decision",
            Publisher = "Federal Reserve",
            Url = "https://fed.gov/1",
            Category = "CENTRAL_BANK"
        };
        var a2 = new NewsArticleDto
        {
            Title = "CoinDesk Derivatives Market",
            Publisher = "CoinDesk",
            Url = "https://coindesk.com/2",
            Category = "DERIVATIVES"
        };

        await _storage.SaveArticlesAsync(new[] { a1, a2 });

        var fedArticles = await service.FilterNewsAsync(publisher: "Federal Reserve");
        Assert.Single(fedArticles);
        Assert.Equal("Federal Reserve", fedArticles[0].Publisher);

        var derivArticles = await service.FilterNewsAsync(category: "DERIVATIVES");
        Assert.Single(derivArticles);
        Assert.Equal("CoinDesk", derivArticles[0].Publisher);
    }
}
