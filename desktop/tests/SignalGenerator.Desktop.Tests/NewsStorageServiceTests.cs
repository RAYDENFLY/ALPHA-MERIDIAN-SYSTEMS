using System;
using System.IO;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class NewsStorageServiceTests : IDisposable
{
    private readonly string _testDbPath;
    private readonly NewsStorageService _storage;

    public NewsStorageServiceTests()
    {
        _testDbPath = Path.Combine(Path.GetTempPath(), $"ams_news_test_{Guid.NewGuid():N}.db");
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
    public async Task SaveArticlesAsync_PersistsAndDeduplicatesByUrl()
    {
        var article1 = new NewsArticleDto
        {
            Title = "FOMC Rate Policy Holds Steady",
            Publisher = "Federal Reserve",
            Url = "https://federalreserve.gov/news/1",
            PublishDateUtc = DateTime.UtcNow.AddHours(-2),
            Excerpt = "Fed policy rates remain unchanged.",
            Category = "CENTRAL_BANK",
            SentimentBias = "NEUTRAL",
            AffectedAssets = "USD / BONDS"
        };

        var article2 = new NewsArticleDto
        {
            Title = "FOMC Rate Policy Holds Steady (Updated)",
            Publisher = "Federal Reserve",
            Url = "https://federalreserve.gov/news/1", // Same URL
            PublishDateUtc = DateTime.UtcNow.AddHours(-1),
            Excerpt = "Fed policy rates remain unchanged with updated inflation outlook.",
            Category = "CENTRAL_BANK",
            SentimentBias = "HAWKISH",
            AffectedAssets = "USD / DXY"
        };

        int count1 = await _storage.SaveArticlesAsync(new[] { article1 });
        Assert.Equal(1, count1);

        int count2 = await _storage.SaveArticlesAsync(new[] { article2 });
        Assert.Equal(1, count2);

        var all = await _storage.GetArticlesAsync();
        Assert.Single(all);
        Assert.Equal("FOMC Rate Policy Holds Steady (Updated)", all[0].Title);
        Assert.Equal("HAWKISH", all[0].SentimentBias);
    }

    [Fact]
    public async Task GetArticlesAsync_FiltersByCategoryAndSearchQuery()
    {
        var cryptoArticle = new NewsArticleDto
        {
            Title = "Bitcoin breaks consolidation band as ETF inflows surge",
            Publisher = "CoinDesk",
            Url = "https://coindesk.com/btc-surge",
            PublishDateUtc = DateTime.UtcNow.AddMinutes(-30),
            Excerpt = "Institutional spot inflows drove BTC past key liquidity wall.",
            Category = "CRYPTO",
            SentimentBias = "BULLISH",
            AffectedAssets = "BTC / ETH"
        };

        var derivativesArticle = new NewsArticleDto
        {
            Title = "Funding rates flip negative across altcoin perps",
            Publisher = "CoinTelegraph",
            Url = "https://cointelegraph.com/funding-flip",
            PublishDateUtc = DateTime.UtcNow.AddMinutes(-15),
            Excerpt = "Aggressive short positioning noticed on Aster DEX and Bybit.",
            Category = "DERIVATIVES",
            SentimentBias = "BEARISH",
            AffectedAssets = "ALTS / PERPS"
        };

        await _storage.SaveArticlesAsync(new[] { cryptoArticle, derivativesArticle });

        // Category filter
        var cryptoOnly = await _storage.GetArticlesAsync(category: "CRYPTO");
        Assert.Single(cryptoOnly);
        Assert.Equal("CRYPTO", cryptoOnly[0].Category);

        // Search query filter
        var searchResult = await _storage.GetArticlesAsync(searchQuery: "ETF inflows");
        Assert.Single(searchResult);
        Assert.Equal("CoinDesk", searchResult[0].Publisher);

        var searchAlt = await _storage.GetArticlesAsync(searchQuery: "Aster DEX");
        Assert.Single(searchAlt);
        Assert.Equal("DERIVATIVES", searchAlt[0].Category);
    }

    [Fact]
    public async Task GetArticlesAsync_FiltersByDateRange()
    {
        var oldArticle = new NewsArticleDto
        {
            Title = "Historical Market Wire",
            Publisher = "Reuters",
            Url = "https://reuters.com/hist",
            PublishDateUtc = new DateTime(2026, 1, 15, 10, 0, 0, DateTimeKind.Utc),
            Category = "GLOBAL_MACRO"
        };

        var newArticle = new NewsArticleDto
        {
            Title = "Recent Market Wire",
            Publisher = "Bloomberg",
            Url = "https://bloomberg.com/recent",
            PublishDateUtc = new DateTime(2026, 10, 27, 14, 0, 0, DateTimeKind.Utc),
            Category = "GLOBAL_MACRO"
        };

        await _storage.SaveArticlesAsync(new[] { oldArticle, newArticle });

        var filtered = await _storage.GetArticlesAsync(
            startDateUtc: new DateTime(2026, 10, 1, 0, 0, 0, DateTimeKind.Utc),
            endDateUtc: new DateTime(2026, 10, 31, 23, 59, 59, DateTimeKind.Utc));

        Assert.Single(filtered);
        Assert.Equal("Recent Market Wire", filtered[0].Title);
    }

    [Fact]
    public async Task UpdateArticleSummaryAsync_CorrectlyUpdatesSummaryAndTimestamp()
    {
        var article = new NewsArticleDto
        {
            Id = "art_test_123",
            Title = "Federal Reserve Monetary Report",
            Publisher = "Federal Reserve",
            Url = "https://federalreserve.gov/report",
            PublishDateUtc = DateTime.UtcNow
        };

        await _storage.SaveArticlesAsync(new[] { article });

        var genTime = DateTime.UtcNow;
        bool ok = await _storage.UpdateArticleSummaryAsync("art_test_123", "Synthesized AI summary content.", genTime);
        Assert.True(ok);

        var retrieved = await _storage.GetArticleByIdAsync("art_test_123");
        Assert.NotNull(retrieved);
        Assert.Equal("Synthesized AI summary content.", retrieved.AiSummary);
        Assert.True(retrieved.IsAiSummarized);
    }

    [Fact]
    public async Task GetArticlesAsync_FiltersByPublisherAndCombinedConditions()
    {
        var cdArticle = new NewsArticleDto
        {
            Title = "CoinDesk Crypto Analysis",
            Publisher = "CoinDesk",
            Url = "https://coindesk.com/article1",
            PublishDateUtc = new DateTime(2026, 10, 20, 12, 0, 0, DateTimeKind.Utc),
            Category = "CRYPTO"
        };

        var ctArticle = new NewsArticleDto
        {
            Title = "CoinTelegraph Crypto Analysis",
            Publisher = "CoinTelegraph",
            Url = "https://cointelegraph.com/article2",
            PublishDateUtc = new DateTime(2026, 10, 21, 12, 0, 0, DateTimeKind.Utc),
            Category = "CRYPTO"
        };

        var fedArticle = new NewsArticleDto
        {
            Title = "Federal Reserve Monetary Policy",
            Publisher = "Federal Reserve",
            Url = "https://federalreserve.gov/article3",
            PublishDateUtc = new DateTime(2026, 10, 22, 12, 0, 0, DateTimeKind.Utc),
            Category = "CENTRAL_BANK"
        };

        await _storage.SaveArticlesAsync(new[] { cdArticle, ctArticle, fedArticle });

        // Filter strictly by publisher
        var byPub = await _storage.GetArticlesAsync(publisher: "CoinDesk");
        Assert.Single(byPub);
        Assert.Equal("CoinDesk", byPub[0].Publisher);

        // Combined filter: Publisher + Category
        var combined = await _storage.GetArticlesAsync(category: "CRYPTO", publisher: "CoinTelegraph");
        Assert.Single(combined);
        Assert.Equal("CoinTelegraph", combined[0].Publisher);

        // Empty intersection
        var emptyIntersection = await _storage.GetArticlesAsync(category: "CENTRAL_BANK", publisher: "CoinDesk");
        Assert.Empty(emptyIntersection);
    }

    [Fact]
    public async Task Persistence_AcrossServiceRestarts()
    {
        var article = new NewsArticleDto
        {
            Id = "restart_persisted_art",
            Title = "Article That Must Persist Across Restart",
            Publisher = "CoinDesk",
            Url = "https://coindesk.com/persisted",
            PublishDateUtc = DateTime.UtcNow,
            Category = "CRYPTO",
            AiSummary = "Pre-computed AI intelligence summary",
            SummaryGeneratedUtc = DateTime.UtcNow
        };

        await _storage.SaveArticlesAsync(new[] { article });

        // Simulate application restart by creating a second service instance with the same database file
        var secondInstance = new NewsStorageService(_testDbPath);
        var loaded = await secondInstance.GetArticlesAsync(searchQuery: "Must Persist Across Restart");

        Assert.Single(loaded);
        Assert.Equal("restart_persisted_art", loaded[0].Id);
        Assert.Equal("Pre-computed AI intelligence summary", loaded[0].AiSummary);
    }
}
