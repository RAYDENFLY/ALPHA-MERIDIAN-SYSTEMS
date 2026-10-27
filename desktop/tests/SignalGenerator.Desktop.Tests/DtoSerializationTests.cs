using System.Text.Json;
using System.Text.Json.Serialization;
using SignalGenerator.Desktop.Models;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class DtoSerializationTests
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        NumberHandling = JsonNumberHandling.AllowReadingFromString
    };

    [Fact]
    public void SystemStatusDto_DeserializesCorrectly_FromLivePayload()
    {
        const string json = """
        {
          "app_name": "SMC-SNR ML Perpetual Scanner",
          "version": "1.0.0",
          "mode": "LIVE_AUTO",
          "base_mode": "PAPER",
          "session_state": "ACTIVE",
          "session_id": "SESS_LIVE_AUTO_1793099684427",
          "live_trading_locked": true,
          "account": {
            "status": "CONNECTED",
            "is_connected": true,
            "is_stale": false,
            "total_equity_usdt": 14.4652,
            "wallet_balance_usdt": 14.4652,
            "available_balance_usdt": 12.4129,
            "used_margin_usdt": 0.0,
            "unrealized_pnl_usdt": 0.0,
            "open_positions_count": 0,
            "last_sync_utc": "2026-10-27T11:14:44.427187+00:00",
            "source": "ASTERDEX_V3_REST"
          },
          "capital": {
            "initial_equity_usdt": 15.0,
            "current_equity_usdt": 14.4652,
            "margin_mode": "ISOLATED",
            "max_leverage": 10.0
          },
          "risk_limits": {
            "daily_loss_limit_usdt": 0.45,
            "daily_loss_used_usdt": 0.0,
            "daily_loss_status": "VERIFIED",
            "open_positions": 0,
            "max_open_positions": 3,
            "daily_entries_used": 1,
            "max_daily_entries": 3,
            "can_open_position": true,
            "block_reason": null,
            "reconciliation_status": "OK",
            "lock_state": "HELD",
            "lock_symbol": "BZUSDT"
          },
          "scanner": {
            "state": "IDLE",
            "process_status": "RUNNING",
            "scan_count": 124,
            "configured_live_strategy_id": "15M_SWEEP_RECLAIM_SMC",
            "last_scan_utc": "2026-10-27T11:14:44Z",
            "background_worker_active": true,
            "is_stale": false,
            "symbols_attempted": ["BZUSDT", "PLAYUSDT"]
          }
        }
        """;

        var dto = JsonSerializer.Deserialize<SystemStatusDto>(json, JsonOptions);

        Assert.NotNull(dto);
        Assert.Equal("SMC-SNR ML Perpetual Scanner", dto.AppName);
        Assert.Equal("LIVE_AUTO", dto.Mode);
        Assert.Equal("ACTIVE", dto.SessionState);
        Assert.Equal("SESS_LIVE_AUTO_1793099684427", dto.SessionId);
        Assert.True(dto.LiveTradingLocked);

        Assert.NotNull(dto.Account);
        Assert.Equal("CONNECTED", dto.Account.Status);
        Assert.Equal(14.4652m, dto.Account.TotalEquityUsdt);
        Assert.Equal(12.4129m, dto.Account.AvailableBalanceUsdt);

        Assert.NotNull(dto.RiskLimits);
        Assert.Equal(0.45m, dto.RiskLimits.DailyLossLimitUsdt);
        Assert.Equal(0.0m, dto.RiskLimits.DailyLossUsedUsdt);
        Assert.Equal("VERIFIED", dto.RiskLimits.DailyLossStatus);
        Assert.Equal(1, dto.RiskLimits.DailyEntriesUsed);
        Assert.Equal(3, dto.RiskLimits.MaxDailyEntries);
        Assert.Equal("HELD", dto.RiskLimits.LockState);
        Assert.Equal("BZUSDT", dto.RiskLimits.LockSymbol);

        Assert.NotNull(dto.Scanner);
        Assert.Equal("IDLE", dto.Scanner.State);
        Assert.Equal("RUNNING", dto.Scanner.ProcessStatus);
        Assert.Equal(124, dto.Scanner.ScanCount);
        Assert.Equal(2, dto.Scanner.SymbolsAttempted.Count);
    }

    [Fact]
    public void LiveSessionEnvelopeDto_DeserializesCorrectly()
    {
        const string json = """
        {
          "status": "SUCCESS",
          "data": {
            "session_id": "SESS_LIVE_AUTO_1793099684427",
            "mode": "LIVE_AUTO",
            "state": "ACTIVE",
            "is_active": true,
            "is_paused": false,
            "started_at_utc": "2026-10-27T11:14:44.427187+00:00",
            "authorized_by": "OPERATOR",
            "orders_placed_count": 0,
            "orders_filled_count": 0,
            "last_candidate_symbol": "BZUSDT",
            "last_status_message": "LIVE Auto-Execution Session authorized and active.",
            "daily_filled_entries": 1,
            "entry_limit": 3,
            "global_lock_state": "HELD",
            "global_lock_symbol": "BZUSDT"
          },
          "preflight": {
            "verdict": "GO",
            "timestamp_utc": "2026-10-27T11:14:44Z",
            "checks": [
              { "check_name": "ACCOUNT_EQUITY", "passed": true, "severity": "CRITICAL" },
              { "check_name": "KILL_SWITCH", "passed": true, "severity": "CRITICAL" }
            ]
          },
          "timestamp_utc": "2026-10-27T11:14:45Z"
        }
        """;

        var dto = JsonSerializer.Deserialize<LiveSessionEnvelopeDto>(json, JsonOptions);

        Assert.NotNull(dto);
        Assert.Equal("SUCCESS", dto.Status);
        Assert.NotNull(dto.Data);
        Assert.Equal("SESS_LIVE_AUTO_1793099684427", dto.Data.SessionId);
        Assert.Equal("ACTIVE", dto.Data.State);
        Assert.True(dto.Data.IsActive);
        Assert.Equal(1, dto.Data.DailyFilledEntries);
        Assert.Equal(3, dto.Data.EntryLimit);
        Assert.Equal("HELD", dto.Data.GlobalLockState);
        Assert.Equal("BZUSDT", dto.Data.GlobalLockSymbol);

        Assert.NotNull(dto.Preflight);
        Assert.Equal("GO", dto.Preflight.Verdict);
        Assert.Equal(2, dto.Preflight.Checks.Count);
        Assert.True(dto.Preflight.Checks[0].Passed);
    }

    [Fact]
    public void JournalSummaryDto_DeserializesCorrectly()
    {
        const string json = """
        {
          "mode": "LIVE",
          "total_closed_trades": 8,
          "wins_count": 2,
          "losses_count": 6,
          "breakeven_count": 0,
          "win_rate_pct": 25.0,
          "profit_factor": 0.09,
          "average_realized_r": -0.563,
          "total_gross_pnl_usd": -0.7776,
          "total_net_pnl_usd": -0.8583,
          "total_fees_paid_usd": 0.0807,
          "total_funding_usd": 0.0,
          "open_count": 0,
          "open_positions_count": 0,
          "daily_loss_usage_usd": 0.0,
          "daily_loss_limit_usd": 0.45,
          "daily_filled_entries": 1,
          "daily_entries_allowance": 3,
          "remaining_entries": 2,
          "miss_count": 0,
          "expired_count": 0,
          "reconciliation_ok": true
        }
        """;

        var dto = JsonSerializer.Deserialize<JournalSummaryDto>(json, JsonOptions);

        Assert.NotNull(dto);
        Assert.Equal("LIVE", dto.Mode);
        Assert.Equal(8, dto.TotalClosedTrades);
        Assert.Equal(2, dto.WinsCount);
        Assert.Equal(6, dto.LossesCount);
        Assert.Equal(25.0m, dto.WinRatePct);
        Assert.Equal(0.09m, dto.ProfitFactor);
        Assert.Equal(-0.563m, dto.AverageRealizedR);
        Assert.Equal(-0.8583m, dto.TotalNetPnlUsd);
        Assert.Equal(0.0807m, dto.TotalFeesPaidUsd);
        Assert.Equal(1, dto.DailyFilledEntries);
        Assert.Equal(2, dto.RemainingEntries);
        Assert.True(dto.ReconciliationOk);
    }

    [Fact]
    public void ScreenerResponseDto_DeserializesCorrectly()
    {
        const string json = """
        {
          "items": [
            {
              "normalized_symbol": "BZUSDT",
              "base_asset": "BZ",
              "quote_asset": "USDT",
              "total_volume_24h_usdt": 18500000.5,
              "total_open_interest_usdt": 4200000.0,
              "benchmark_price": 109.85,
              "price_change_24h_pct": 3.45,
              "venue_count": 3,
              "cross_listed": true,
              "best_spread_bps": 1.2,
              "data_freshness_seconds": 0.8
            }
          ],
          "total_count": 1,
          "page": 1,
          "page_size": 25,
          "total_pages": 1
        }
        """;

        var dto = JsonSerializer.Deserialize<ScreenerResponseDto>(json, JsonOptions);

        Assert.NotNull(dto);
        Assert.Equal(1, dto.TotalCount);
        Assert.Single(dto.Items);

        var item = dto.Items[0];
        Assert.Equal("BZUSDT", item.NormalizedSymbol);
        Assert.Equal(109.85m, item.BenchmarkPrice);
        Assert.Equal(18500000.5m, item.TotalVolume24hUsdt);
        Assert.Equal(3.45m, item.PriceChange24hPct);
        Assert.True(item.CrossListed);
        Assert.Equal(3, item.VenueCount);
        Assert.Equal(1.2m, item.BestSpreadBps);
    }
}
