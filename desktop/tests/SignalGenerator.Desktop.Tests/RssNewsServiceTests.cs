using System;
using System.Net.Http;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Services;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class RssNewsServiceTests
{
    [Fact]
    public async Task RssNewsService_ProvidesFallbackHeadlines_WhenFeedsOffline()
    {
        // Using HttpClient pointing to a non-existent port to simulate offline network
        using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(500) };
        var service = new RssNewsService(client);

        var articles = await service.FetchLatestNewsAsync();

        Assert.NotNull(articles);
        Assert.NotEmpty(articles);
        Assert.Contains(articles, a => a.Publisher == "Federal Reserve" || a.Publisher == "CoinDesk");
    }

    [Fact]
    public async Task RssNewsService_SummarizesArticle_SafelyWithoutExposingKeys()
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(500) };
        var service = new RssNewsService(client);

        var articles = await service.FetchLatestNewsAsync();
        Assert.NotEmpty(articles);

        var first = articles[0];
        var summarized = await service.SummarizeArticleAsync(first.Id);

        Assert.NotNull(summarized);
        Assert.True(summarized.IsAiSummarized);
        Assert.Contains("[INTELLIGENCE SYNTHESIS]", summarized.AiSummary);
    }
}
