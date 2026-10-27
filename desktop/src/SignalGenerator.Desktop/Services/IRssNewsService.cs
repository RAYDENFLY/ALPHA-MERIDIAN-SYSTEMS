using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public interface IRssNewsService
{
    Task<List<NewsArticleDto>> FetchLatestNewsAsync(CancellationToken ct = default);
    Task<NewsArticleDto?> SummarizeArticleAsync(string articleId, CancellationToken ct = default);
}
