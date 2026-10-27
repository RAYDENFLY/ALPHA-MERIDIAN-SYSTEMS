using System;
using System.IO;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class PersonalJournalServiceTests : IDisposable
{
    private readonly string _testDbPath;
    private readonly PersonalJournalService _service;

    public PersonalJournalServiceTests()
    {
        _testDbPath = Path.Combine(Path.GetTempPath(), $"test_personal_journal_{Guid.NewGuid():N}.db");
        _service = new PersonalJournalService(_testDbPath);
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
    public void PersonalTradeRecord_CalculatesPnlCorrectly_ForLongAndShort()
    {
        // LONG trade
        var longTrade = new PersonalTradeRecord
        {
            Direction = "LONG",
            EntryPrice = 60000m,
            ExitPrice = 63000m,
            Quantity = 0.5m,
            Fees = 10m,
            FundingCosts = 2m
        };
        longTrade.CalculatePnl();

        // Gross = (63000 - 60000) * 0.5 = 1500
        // Net = 1500 - 10 - 2 = 1488
        Assert.Equal(1500m, longTrade.GrossPnl);
        Assert.Equal(1488m, longTrade.NetPnl);
        Assert.True(longTrade.IsClosed);

        // SHORT trade
        var shortTrade = new PersonalTradeRecord
        {
            Direction = "SHORT",
            EntryPrice = 3000m,
            ExitPrice = 2800m,
            Quantity = 2.0m,
            Fees = 5m,
            FundingCosts = 1m
        };
        shortTrade.CalculatePnl();

        // Gross = (3000 - 2800) * 2 = 400
        // Net = 400 - 5 - 1 = 394
        Assert.Equal(400m, shortTrade.GrossPnl);
        Assert.Equal(394m, shortTrade.NetPnl);
        Assert.True(shortTrade.IsClosed);
    }

    [Fact]
    public async Task PersonalJournalService_CrudLifecycle_PersistsAndRetrieves()
    {
        await _service.InitializeAsync();

        var trade = new PersonalTradeRecord
        {
            TradeId = "TRD_TEST_001",
            TradeDateUtc = DateTime.UtcNow,
            Symbol = "SOLUSDT",
            Direction = "LONG",
            Exchange = "Aster DEX",
            EntryPrice = 100m,
            ExitPrice = 110m,
            Quantity = 1m,
            Fees = 0.5m,
            Strategy = "SMC Reclaim",
            EntryRationale = "15m sweep low reclaim"
        };

        var saved = await _service.SaveTradeAsync(trade);
        Assert.True(saved);

        var retrieved = await _service.GetTradeByIdAsync("TRD_TEST_001");
        Assert.NotNull(retrieved);
        Assert.Equal("SOLUSDT", retrieved.Symbol);
        Assert.Equal(100m, retrieved.EntryPrice);
        Assert.Equal(110m, retrieved.ExitPrice);
        Assert.Equal(9.5m, retrieved.NetPnl);

        var all = await _service.GetAllTradesAsync();
        Assert.Single(all);

        var deleted = await _service.DeleteTradeAsync("TRD_TEST_001");
        Assert.True(deleted);

        var afterDelete = await _service.GetTradeByIdAsync("TRD_TEST_001");
        Assert.Null(afterDelete);
    }

    [Fact]
    public async Task PersonalJournalService_AggregatesDailyAndMonthlyPnl()
    {
        await _service.InitializeAsync();
        var date = new DateTime(2026, 10, 27, 12, 0, 0, DateTimeKind.Utc);

        var trade1 = new PersonalTradeRecord
        {
            TradeId = "TRD_M1",
            TradeDateUtc = date,
            Symbol = "BTCUSDT",
            Direction = "LONG",
            EntryPrice = 60000m,
            ExitPrice = 61000m,
            Quantity = 1m,
            Fees = 10m
        };
        var trade2 = new PersonalTradeRecord
        {
            TradeId = "TRD_M2",
            TradeDateUtc = date,
            Symbol = "ETHUSDT",
            Direction = "LONG",
            EntryPrice = 2500m,
            ExitPrice = 2400m,
            Quantity = 2m,
            Fees = 5m
        };

        await _service.SaveTradeAsync(trade1);
        await _service.SaveTradeAsync(trade2);

        var daily = await _service.GetDailyNetPnlAsync(2026, 10);
        Assert.True(daily.ContainsKey(date.Date));

        // Trade 1 Net = 990, Trade 2 Net = -205 -> Daily Net = 785
        Assert.Equal(785m, daily[date.Date]);

        var monthly = await _service.GetMonthlyNetPnlAsync(2026, 10);
        Assert.Equal(785m, monthly);
    }
}
