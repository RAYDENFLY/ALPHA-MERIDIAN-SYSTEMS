using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using Microsoft.Data.Sqlite;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Services;

public class PersonalJournalService : IPersonalJournalService
{
    private readonly string _connectionString;
    public string DatabasePath { get; }

    public PersonalJournalService(string? customDbPath = null)
    {
        if (!string.IsNullOrWhiteSpace(customDbPath))
        {
            DatabasePath = customDbPath;
        }
        else
        {
            var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            var dir = Path.Combine(appData, "SignalGeneratorDesktop");
            if (!Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }
            DatabasePath = Path.Combine(dir, "personal-journal.db");
        }

        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = DatabasePath,
            Mode = SqliteOpenMode.ReadWriteCreate
        }.ToString();
    }

    public async Task InitializeAsync()
    {
        using var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync();

        var tableCmd = connection.CreateCommand();
        tableCmd.CommandText = @"
            CREATE TABLE IF NOT EXISTS personal_trades (
                trade_id TEXT PRIMARY KEY,
                trade_date_utc TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                exchange TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity REAL NOT NULL,
                fees REAL NOT NULL DEFAULT 0,
                funding_costs REAL NOT NULL DEFAULT 0,
                gross_pnl REAL,
                net_pnl REAL,
                strategy TEXT,
                entry_rationale TEXT,
                exit_rationale TEXT,
                lessons_learned TEXT,
                notes TEXT,
                is_closed INTEGER NOT NULL DEFAULT 1,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
        ";
        await tableCmd.ExecuteNonQueryAsync();

        var checkVersionCmd = connection.CreateCommand();
        checkVersionCmd.CommandText = "SELECT COUNT(*) FROM schema_version WHERE version = 1;";
        var versionCount = Convert.ToInt64(await checkVersionCmd.ExecuteScalarAsync());
        if (versionCount == 0)
        {
            var insertVersionCmd = connection.CreateCommand();
            insertVersionCmd.CommandText = "INSERT INTO schema_version (version, applied_at) VALUES (1, @now);";
            insertVersionCmd.Parameters.AddWithValue("@now", DateTime.UtcNow.ToString("o"));
            await insertVersionCmd.ExecuteNonQueryAsync();
        }
    }

    public async Task<List<PersonalTradeRecord>> GetAllTradesAsync()
    {
        await InitializeAsync();
        var trades = new List<PersonalTradeRecord>();

        using var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync();

        using var cmd = connection.CreateCommand();
        cmd.CommandText = "SELECT * FROM personal_trades ORDER BY trade_date_utc DESC;";

        using var reader = await cmd.ExecuteReaderAsync();
        while (await reader.ReadAsync())
        {
            trades.Add(MapReaderToRecord(reader));
        }

        return trades;
    }

    public async Task<PersonalTradeRecord?> GetTradeByIdAsync(string tradeId)
    {
        await InitializeAsync();
        using var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync();

        using var cmd = connection.CreateCommand();
        cmd.CommandText = "SELECT * FROM personal_trades WHERE trade_id = @id LIMIT 1;";
        cmd.Parameters.AddWithValue("@id", tradeId);

        using var reader = await cmd.ExecuteReaderAsync();
        if (await reader.ReadAsync())
        {
            return MapReaderToRecord(reader);
        }

        return null;
    }

    public async Task<bool> SaveTradeAsync(PersonalTradeRecord trade)
    {
        await InitializeAsync();
        trade.CalculatePnl();
        trade.UpdatedAtUtc = DateTime.UtcNow;

        using var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync();

        using var cmd = connection.CreateCommand();
        cmd.CommandText = @"
            INSERT INTO personal_trades (
                trade_id, trade_date_utc, symbol, direction, exchange,
                entry_price, exit_price, quantity, fees, funding_costs,
                gross_pnl, net_pnl, strategy, entry_rationale, exit_rationale,
                lessons_learned, notes, is_closed, created_at_utc, updated_at_utc
            ) VALUES (
                @trade_id, @trade_date_utc, @symbol, @direction, @exchange,
                @entry_price, @exit_price, @quantity, @fees, @funding_costs,
                @gross_pnl, @net_pnl, @strategy, @entry_rationale, @exit_rationale,
                @lessons_learned, @notes, @is_closed, @created_at_utc, @updated_at_utc
            )
            ON CONFLICT(trade_id) DO UPDATE SET
                trade_date_utc = excluded.trade_date_utc,
                symbol = excluded.symbol,
                direction = excluded.direction,
                exchange = excluded.exchange,
                entry_price = excluded.entry_price,
                exit_price = excluded.exit_price,
                quantity = excluded.quantity,
                fees = excluded.fees,
                funding_costs = excluded.funding_costs,
                gross_pnl = excluded.gross_pnl,
                net_pnl = excluded.net_pnl,
                strategy = excluded.strategy,
                entry_rationale = excluded.entry_rationale,
                exit_rationale = excluded.exit_rationale,
                lessons_learned = excluded.lessons_learned,
                notes = excluded.notes,
                is_closed = excluded.is_closed,
                updated_at_utc = excluded.updated_at_utc;
        ";

        cmd.Parameters.AddWithValue("@trade_id", trade.TradeId);
        cmd.Parameters.AddWithValue("@trade_date_utc", trade.TradeDateUtc.ToString("o"));
        cmd.Parameters.AddWithValue("@symbol", trade.Symbol);
        cmd.Parameters.AddWithValue("@direction", trade.Direction);
        cmd.Parameters.AddWithValue("@exchange", trade.Exchange);
        cmd.Parameters.AddWithValue("@entry_price", Convert.ToDouble(trade.EntryPrice));
        cmd.Parameters.AddWithValue("@exit_price", trade.ExitPrice.HasValue ? Convert.ToDouble(trade.ExitPrice.Value) : DBNull.Value);
        cmd.Parameters.AddWithValue("@quantity", Convert.ToDouble(trade.Quantity));
        cmd.Parameters.AddWithValue("@fees", Convert.ToDouble(trade.Fees));
        cmd.Parameters.AddWithValue("@funding_costs", Convert.ToDouble(trade.FundingCosts));
        cmd.Parameters.AddWithValue("@gross_pnl", trade.GrossPnl.HasValue ? Convert.ToDouble(trade.GrossPnl.Value) : DBNull.Value);
        cmd.Parameters.AddWithValue("@net_pnl", trade.NetPnl.HasValue ? Convert.ToDouble(trade.NetPnl.Value) : DBNull.Value);
        cmd.Parameters.AddWithValue("@strategy", trade.Strategy ?? "");
        cmd.Parameters.AddWithValue("@entry_rationale", trade.EntryRationale ?? "");
        cmd.Parameters.AddWithValue("@exit_rationale", trade.ExitRationale ?? "");
        cmd.Parameters.AddWithValue("@lessons_learned", trade.LessonsLearned ?? "");
        cmd.Parameters.AddWithValue("@notes", trade.Notes ?? "");
        cmd.Parameters.AddWithValue("@is_closed", trade.IsClosed ? 1 : 0);
        cmd.Parameters.AddWithValue("@created_at_utc", trade.CreatedAtUtc.ToString("o"));
        cmd.Parameters.AddWithValue("@updated_at_utc", trade.UpdatedAtUtc.ToString("o"));

        return await cmd.ExecuteNonQueryAsync() > 0;
    }

    public async Task<bool> DeleteTradeAsync(string tradeId)
    {
        await InitializeAsync();
        using var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync();

        using var cmd = connection.CreateCommand();
        cmd.CommandText = "DELETE FROM personal_trades WHERE trade_id = @id;";
        cmd.Parameters.AddWithValue("@id", tradeId);

        return await cmd.ExecuteNonQueryAsync() > 0;
    }

    public async Task<Dictionary<DateTime, decimal>> GetDailyNetPnlAsync(int year, int month)
    {
        var allTrades = await GetAllTradesAsync();
        var daily = new Dictionary<DateTime, decimal>();

        foreach (var t in allTrades)
        {
            if (t.IsClosed && t.NetPnl.HasValue && t.TradeDateUtc.Year == year && t.TradeDateUtc.Month == month)
            {
                var date = t.TradeDateUtc.Date;
                if (!daily.ContainsKey(date))
                {
                    daily[date] = 0;
                }
                daily[date] += t.NetPnl.Value;
            }
        }

        return daily;
    }

    public async Task<decimal> GetMonthlyNetPnlAsync(int year, int month)
    {
        var daily = await GetDailyNetPnlAsync(year, month);
        decimal sum = 0;
        foreach (var val in daily.Values)
        {
            sum += val;
        }
        return sum;
    }

    private static PersonalTradeRecord MapReaderToRecord(SqliteDataReader reader)
    {
        return new PersonalTradeRecord
        {
            TradeId = reader.GetString(reader.GetOrdinal("trade_id")),
            TradeDateUtc = DateTime.Parse(reader.GetString(reader.GetOrdinal("trade_date_utc"))),
            Symbol = reader.GetString(reader.GetOrdinal("symbol")),
            Direction = reader.GetString(reader.GetOrdinal("direction")),
            Exchange = reader.GetString(reader.GetOrdinal("exchange")),
            EntryPrice = Convert.ToDecimal(reader.GetDouble(reader.GetOrdinal("entry_price"))),
            ExitPrice = reader.IsDBNull(reader.GetOrdinal("exit_price")) ? null : Convert.ToDecimal(reader.GetDouble(reader.GetOrdinal("exit_price"))),
            Quantity = Convert.ToDecimal(reader.GetDouble(reader.GetOrdinal("quantity"))),
            Fees = Convert.ToDecimal(reader.GetDouble(reader.GetOrdinal("fees"))),
            FundingCosts = Convert.ToDecimal(reader.GetDouble(reader.GetOrdinal("funding_costs"))),
            GrossPnl = reader.IsDBNull(reader.GetOrdinal("gross_pnl")) ? null : Convert.ToDecimal(reader.GetDouble(reader.GetOrdinal("gross_pnl"))),
            NetPnl = reader.IsDBNull(reader.GetOrdinal("net_pnl")) ? null : Convert.ToDecimal(reader.GetDouble(reader.GetOrdinal("net_pnl"))),
            Strategy = reader.IsDBNull(reader.GetOrdinal("strategy")) ? "" : reader.GetString(reader.GetOrdinal("strategy")),
            EntryRationale = reader.IsDBNull(reader.GetOrdinal("entry_rationale")) ? "" : reader.GetString(reader.GetOrdinal("entry_rationale")),
            ExitRationale = reader.IsDBNull(reader.GetOrdinal("exit_rationale")) ? "" : reader.GetString(reader.GetOrdinal("exit_rationale")),
            LessonsLearned = reader.IsDBNull(reader.GetOrdinal("lessons_learned")) ? "" : reader.GetString(reader.GetOrdinal("lessons_learned")),
            Notes = reader.IsDBNull(reader.GetOrdinal("notes")) ? "" : reader.GetString(reader.GetOrdinal("notes")),
            IsClosed = reader.GetInt32(reader.GetOrdinal("is_closed")) == 1,
            CreatedAtUtc = DateTime.Parse(reader.GetString(reader.GetOrdinal("created_at_utc"))),
            UpdatedAtUtc = DateTime.Parse(reader.GetString(reader.GetOrdinal("updated_at_utc")))
        };
    }
}
