using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Data.Sqlite;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public class NewsStorageService : INewsStorageService
{
    private readonly string _connectionString;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private bool _isInitialized;

    public NewsStorageService(string? customDbPath = null)
    {
        string dbPath;
        if (!string.IsNullOrWhiteSpace(customDbPath))
        {
            dbPath = customDbPath;
        }
        else
        {
            var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            var folder = Path.Combine(localAppData, "AMS Capital", "Alpha Meridian Systems");
            Directory.CreateDirectory(folder);
            dbPath = Path.Combine(folder, "news.db");
        }

        var dir = Path.GetDirectoryName(dbPath);
        if (!string.IsNullOrEmpty(dir))
        {
            Directory.CreateDirectory(dir);
        }

        _connectionString = $"Data Source={dbPath}";
    }

    public async Task InitializeAsync()
    {
        if (_isInitialized) return;

        await _gate.WaitAsync();
        try
        {
            if (_isInitialized) return;

            using var conn = new SqliteConnection(_connectionString);
            await conn.OpenAsync();

            const string schemaSql = """
            CREATE TABLE IF NOT EXISTS news_articles (
                id TEXT PRIMARY KEY,
                url TEXT UNIQUE NOT NULL,
                publisher TEXT NOT NULL,
                title TEXT NOT NULL,
                excerpt TEXT,
                content TEXT,
                publish_date_utc TEXT NOT NULL,
                first_seen_utc TEXT NOT NULL,
                category TEXT,
                sentiment_bias TEXT,
                affected_assets TEXT,
                ai_summary TEXT,
                summary_generated_utc TEXT,
                is_ai_summarized INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_news_publish_date ON news_articles(publish_date_utc DESC);
            CREATE INDEX IF NOT EXISTS idx_news_category ON news_articles(category);
            """;

            using var cmd = conn.CreateCommand();
            cmd.CommandText = schemaSql;
            await cmd.ExecuteNonQueryAsync();

            _isInitialized = true;
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task<int> SaveArticlesAsync(IEnumerable<NewsArticleDto> articles)
    {
        await InitializeAsync();

        await _gate.WaitAsync();
        try
        {
            using var conn = new SqliteConnection(_connectionString);
            await conn.OpenAsync();
            using var tx = conn.BeginTransaction();

            const string insertSql = """
            INSERT INTO news_articles (
                id, url, publisher, title, excerpt, content,
                publish_date_utc, first_seen_utc, category,
                sentiment_bias, affected_assets, ai_summary,
                summary_generated_utc, is_ai_summarized
            ) VALUES (
                @id, @url, @publisher, @title, @excerpt, @content,
                @publish_date_utc, @first_seen_utc, @category,
                @sentiment_bias, @affected_assets, @ai_summary,
                @summary_generated_utc, @is_ai_summarized
            )
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                excerpt = excluded.excerpt,
                publisher = excluded.publisher,
                category = excluded.category,
                sentiment_bias = excluded.sentiment_bias,
                affected_assets = excluded.affected_assets;
            """;

            int savedCount = 0;
            var nowStr = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);

            foreach (var a in articles)
            {
                if (string.IsNullOrWhiteSpace(a.Url)) continue;

                using var cmd = conn.CreateCommand();
                cmd.Transaction = tx;
                cmd.CommandText = insertSql;

                cmd.Parameters.AddWithValue("@id", a.Id);
                cmd.Parameters.AddWithValue("@url", a.Url);
                cmd.Parameters.AddWithValue("@publisher", a.Publisher ?? string.Empty);
                cmd.Parameters.AddWithValue("@title", a.Title ?? string.Empty);
                cmd.Parameters.AddWithValue("@excerpt", a.Excerpt ?? string.Empty);
                cmd.Parameters.AddWithValue("@content", DBNull.Value);
                cmd.Parameters.AddWithValue("@publish_date_utc", a.PublishDateUtc.ToString("o", CultureInfo.InvariantCulture));
                cmd.Parameters.AddWithValue("@first_seen_utc", nowStr);
                cmd.Parameters.AddWithValue("@category", a.Category ?? "GLOBAL_MACRO");
                cmd.Parameters.AddWithValue("@sentiment_bias", a.SentimentBias ?? "NEUTRAL");
                cmd.Parameters.AddWithValue("@affected_assets", a.AffectedAssets ?? "CRYPTO");
                cmd.Parameters.AddWithValue("@ai_summary", (object?)a.AiSummary ?? DBNull.Value);
                cmd.Parameters.AddWithValue("@summary_generated_utc", a.SummaryGeneratedUtc.HasValue ? a.SummaryGeneratedUtc.Value.ToString("o", CultureInfo.InvariantCulture) : (object)DBNull.Value);
                cmd.Parameters.AddWithValue("@is_ai_summarized", a.IsAiSummarized ? 1 : 0);

                await cmd.ExecuteNonQueryAsync();
                savedCount++;
            }

            await tx.CommitAsync();
            return savedCount;
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task<List<NewsArticleDto>> GetArticlesAsync(
        string? category = null,
        string? searchQuery = null,
        DateTime? startDateUtc = null,
        DateTime? endDateUtc = null,
        int limit = 100)
    {
        await InitializeAsync();

        await _gate.WaitAsync();
        try
        {
            using var conn = new SqliteConnection(_connectionString);
            await conn.OpenAsync();

            var sb = new StringBuilder("""
            SELECT id, url, publisher, title, excerpt, publish_date_utc,
                   category, sentiment_bias, affected_assets, ai_summary,
                   summary_generated_utc, is_ai_summarized
            FROM news_articles
            WHERE 1=1
            """);

            using var cmd = conn.CreateCommand();

            if (!string.IsNullOrWhiteSpace(category) && !category.Equals("ALL", StringComparison.OrdinalIgnoreCase))
            {
                sb.Append(" AND category = @category");
                cmd.Parameters.AddWithValue("@category", category);
            }

            if (startDateUtc.HasValue)
            {
                sb.Append(" AND publish_date_utc >= @startDate");
                cmd.Parameters.AddWithValue("@startDate", startDateUtc.Value.ToString("o", CultureInfo.InvariantCulture));
            }

            if (endDateUtc.HasValue)
            {
                sb.Append(" AND publish_date_utc <= @endDate");
                cmd.Parameters.AddWithValue("@endDate", endDateUtc.Value.ToString("o", CultureInfo.InvariantCulture));
            }

            if (!string.IsNullOrWhiteSpace(searchQuery))
            {
                sb.Append(" AND (title LIKE @search OR excerpt LIKE @search OR publisher LIKE @search OR category LIKE @search OR affected_assets LIKE @search)");
                cmd.Parameters.AddWithValue("@search", $"%{searchQuery.Trim()}%");
            }

            sb.Append(" ORDER BY publish_date_utc DESC LIMIT @limit;");
            cmd.Parameters.AddWithValue("@limit", Math.Max(1, limit));

            cmd.CommandText = sb.ToString();

            var list = new List<NewsArticleDto>();
            using var reader = await cmd.ExecuteReaderAsync();
            while (await reader.ReadAsync())
            {
                var id = reader.GetString(0);
                var url = reader.GetString(1);
                var publisher = reader.GetString(2);
                var title = reader.GetString(3);
                var excerpt = reader.IsDBNull(4) ? string.Empty : reader.GetString(4);
                var pubDateStr = reader.GetString(5);
                var cat = reader.IsDBNull(6) ? "GLOBAL_MACRO" : reader.GetString(6);
                var sentiment = reader.IsDBNull(7) ? "NEUTRAL" : reader.GetString(7);
                var affected = reader.IsDBNull(8) ? "CRYPTO" : reader.GetString(8);
                var aiSummary = reader.IsDBNull(9) ? null : reader.GetString(9);
                var sumGenStr = reader.IsDBNull(10) ? null : reader.GetString(10);

                DateTime pubDate = DateTime.TryParse(pubDateStr, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var dt)
                    ? dt
                    : DateTime.UtcNow;

                DateTime? sumGen = null;
                if (!string.IsNullOrEmpty(sumGenStr) && DateTime.TryParse(sumGenStr, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var sdt))
                {
                    sumGen = sdt;
                }

                list.Add(new NewsArticleDto
                {
                    Id = id,
                    Url = url,
                    Publisher = publisher,
                    Title = title,
                    Excerpt = excerpt,
                    PublishDateUtc = pubDate,
                    Category = cat,
                    SentimentBias = sentiment,
                    AffectedAssets = affected,
                    AiSummary = aiSummary,
                    SummaryGeneratedUtc = sumGen,
                    IsStale = false
                });
            }

            return list;
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task<bool> UpdateArticleSummaryAsync(string articleId, string summary, DateTime generatedUtc)
    {
        await InitializeAsync();

        await _gate.WaitAsync();
        try
        {
            using var conn = new SqliteConnection(_connectionString);
            await conn.OpenAsync();

            const string updateSql = """
            UPDATE news_articles
            SET ai_summary = @summary,
                summary_generated_utc = @genUtc,
                is_ai_summarized = 1
            WHERE id = @id;
            """;

            using var cmd = conn.CreateCommand();
            cmd.CommandText = updateSql;
            cmd.Parameters.AddWithValue("@id", articleId);
            cmd.Parameters.AddWithValue("@summary", summary);
            cmd.Parameters.AddWithValue("@genUtc", generatedUtc.ToString("o", CultureInfo.InvariantCulture));

            int rows = await cmd.ExecuteNonQueryAsync();
            return rows > 0;
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task<NewsArticleDto?> GetArticleByIdAsync(string articleId)
    {
        await InitializeAsync();

        await _gate.WaitAsync();
        try
        {
            using var conn = new SqliteConnection(_connectionString);
            await conn.OpenAsync();

            const string selectSql = """
            SELECT id, url, publisher, title, excerpt, publish_date_utc,
                   category, sentiment_bias, affected_assets, ai_summary,
                   summary_generated_utc, is_ai_summarized
            FROM news_articles
            WHERE id = @id;
            """;

            using var cmd = conn.CreateCommand();
            cmd.CommandText = selectSql;
            cmd.Parameters.AddWithValue("@id", articleId);

            using var reader = await cmd.ExecuteReaderAsync();
            if (await reader.ReadAsync())
            {
                var id = reader.GetString(0);
                var url = reader.GetString(1);
                var publisher = reader.GetString(2);
                var title = reader.GetString(3);
                var excerpt = reader.IsDBNull(4) ? string.Empty : reader.GetString(4);
                var pubDateStr = reader.GetString(5);
                var cat = reader.IsDBNull(6) ? "GLOBAL_MACRO" : reader.GetString(6);
                var sentiment = reader.IsDBNull(7) ? "NEUTRAL" : reader.GetString(7);
                var affected = reader.IsDBNull(8) ? "CRYPTO" : reader.GetString(8);
                var aiSummary = reader.IsDBNull(9) ? null : reader.GetString(9);
                var sumGenStr = reader.IsDBNull(10) ? null : reader.GetString(10);

                DateTime pubDate = DateTime.TryParse(pubDateStr, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var dt)
                    ? dt
                    : DateTime.UtcNow;

                DateTime? sumGen = null;
                if (!string.IsNullOrEmpty(sumGenStr) && DateTime.TryParse(sumGenStr, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var sdt))
                {
                    sumGen = sdt;
                }

                return new NewsArticleDto
                {
                    Id = id,
                    Url = url,
                    Publisher = publisher,
                    Title = title,
                    Excerpt = excerpt,
                    PublishDateUtc = pubDate,
                    Category = cat,
                    SentimentBias = sentiment,
                    AffectedAssets = affected,
                    AiSummary = aiSummary,
                    SummaryGeneratedUtc = sumGen,
                    IsStale = false
                };
            }

            return null;
        }
        finally
        {
            _gate.Release();
        }
    }
}
