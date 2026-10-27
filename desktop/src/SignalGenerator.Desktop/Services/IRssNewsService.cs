using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public interface IRssNewsService
{
    INewsStorageService Storage { get; }
    IReadOnlyList<FeedSourceStatusDto> SourceStatuses { get; }
    Task<List<NewsArticleDto>> FetchLatestNewsAsync(CancellationToken ct = default);
    Task<List<NewsArticleDto>> FilterNewsAsync(
        string? category = null,
        string? searchQuery = null,
        DateTime? startDateUtc = null,
        DateTime? endDateUtc = null,
        string? publisher = null,
        CancellationToken ct = default);
    Task<NewsArticleDto?> SummarizeArticleAsync(string articleId, CancellationToken ct = default);
}
