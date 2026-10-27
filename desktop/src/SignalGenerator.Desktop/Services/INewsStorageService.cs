using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public interface INewsStorageService
{
    Task InitializeAsync();
    Task<int> SaveArticlesAsync(IEnumerable<NewsArticleDto> articles);
    Task<List<NewsArticleDto>> GetArticlesAsync(
        string? category = null,
        string? searchQuery = null,
        DateTime? startDateUtc = null,
        DateTime? endDateUtc = null,
        int limit = 100);
    Task<bool> UpdateArticleSummaryAsync(string articleId, string summary, DateTime generatedUtc);
    Task<NewsArticleDto?> GetArticleByIdAsync(string articleId);
}
