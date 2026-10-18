"""Tests for Tier 1 Micro-Margin Policy, Candidate-Specific SL Risk, and Loss Governance.

Verifies:
1. Candidate-specific stop-loss risk calculation:
   - Evaluates true entry price, stop-loss price, order quantity, fees, slippage, and funding rate.
   - Explicitly confirms variable stop distances (e.g., 0.8%, 1.5%, 2.2%, 3.0%) do NOT assume static 1.5% SL.
2. Sizing from risk budget and structural stop distance:
   - Sizing conforms to Tier 1 target isolated margin ($1.00-$1.50) and 10x max leverage.
   - $1.50 hard SL cap acts as an absolute loss ceiling, never as target risk.
3. Symbol-specific exchange minimum notional / precision feasibility:
   - Rejection of oversized minimum-lot assets (e.g. BTCUSDT min lot 0.001 requires ~$8.15 margin > $1.50 limit -> INFEASIBLE).
   - Feasible sizing for accessible micro-lot symbols (ETH, SOL, XRP, ADA, LTC, DOGE, AVAX, LINK, BNB, XMR).
4. Loss governance and circuit breaker reconciliation:
   - Proposed $0.50 daily loss cap accommodates consecutive micro-losses ($0.20, $0.25) before tripping.
   - Daily loss limit breach (loss >= $0.50) halts new entries without stopping scan observability or account monitoring.
   - Weekly loss limit ($0.54 preserved) blocks new entries when breached.
   - Calendar bucket resets at 00:00 UTC and weekly resets on Monday.
   - State synchronization and loss recovery from persistent journal.
"""

from datetime import datetime, timezone
import pytest

from src.core.types import CandidateStatus, Direction, EntryCategory, TradeRecord
from src.exchange.metadata import SymbolSpecs
from src.risk.manager import RiskManager
from src.risk.sizing import PositionSizer
from src.risk.tier import EquityTierConfig, resolve_equity_tier


@pytest.fixture
def btc_specs():
    return SymbolSpecs(
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        contract_type="PERPETUAL",
        status="TRADING",
        tick_size=0.1,
        price_precision=1,
        step_size=0.001,
        quantity_precision=3,
        min_qty=0.001,
        max_qty=100.0,
        min_notional=5.0,
        maint_margin_percent=0.005,
    )


@pytest.fixture
def eth_specs():
    return SymbolSpecs(
        symbol="ETHUSDT",
        base_asset="ETH",
        quote_asset="USDT",
        contract_type="PERPETUAL",
        status="TRADING",
        tick_size=0.01,
        price_precision=2,
        step_size=0.001,
        quantity_precision=3,
        min_qty=0.001,
        max_qty=1000.0,
        min_notional=5.0,
        maint_margin_percent=0.005,
    )


@pytest.fixture
def xrp_specs():
    return SymbolSpecs(
        symbol="XRPUSDT",
        base_asset="XRP",
        quote_asset="USDT",
        contract_type="PERPETUAL",
        status="TRADING",
        tick_size=0.0001,
        price_precision=4,
        step_size=0.1,
        quantity_precision=1,
        min_qty=0.1,
        max_qty=100000.0,
        min_notional=5.0,
        maint_margin_percent=0.005,
    )


@pytest.fixture
def sol_specs():
    return SymbolSpecs(
        symbol="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        contract_type="PERPETUAL",
        status="TRADING",
        tick_size=0.01,
        price_precision=2,
        step_size=0.01,
        quantity_precision=2,
        min_qty=0.01,
        max_qty=10000.0,
        min_notional=5.0,
        maint_margin_percent=0.005,
    )


class TestCandidateSpecificSLRiskCalculation:
    """Tests confirming stop-loss risk is calculated dynamically with all costs."""

    def test_dynamic_stop_loss_distance_not_fixed_one_point_five_percent(self, eth_specs):
        """Verifies stop-loss distance is derived from structural levels, not assumed 1.5%."""
        tier_cfg = EquityTierConfig()

        # Scenario A: Tight structural stop (0.8% distance)
        # Entry 2500, SL 2480 (distance 20 = 0.8%)
        assess_tight = PositionSizer.calculate_sizing(
            symbol_specs=eth_specs,
            entry_price=2500.0,
            stop_loss=2480.0,
            available_equity_usdt=17.20,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
            tier_config=tier_cfg,
        )
        assert assess_tight.is_feasible is True
        assert round(assess_tight.stop_distance_pct * 100.0, 2) == 0.80

        # Scenario B: Moderate structural stop (2.0% distance)
        # Entry 2500, SL 2450 (distance 50 = 2.0%)
        assess_mod = PositionSizer.calculate_sizing(
            symbol_specs=eth_specs,
            entry_price=2500.0,
            stop_loss=2450.0,
            available_equity_usdt=17.20,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
            tier_config=tier_cfg,
        )
        assert assess_mod.is_feasible is True
        assert round(assess_mod.stop_distance_pct * 100.0, 2) == 2.00

        # Sizing and risk must differ between the two
        assert assess_tight.estimated_loss_usdt < assess_mod.estimated_loss_usdt

    def test_fee_slippage_and_funding_included_in_sl_risk(self, sol_specs):
        """Verifies that taker fees, execution slippage, and adverse funding are integrated into SL risk."""
        # Entry $100.0, SL $98.0 (2% price drop)
        # Sizing with zero funding
        assess_no_fund = PositionSizer.calculate_sizing(
            symbol_specs=sol_specs,
            entry_price=100.0,
            stop_loss=98.0,
            available_equity_usdt=17.20,
            target_isolated_margin=1.0,
            target_isolated_margin_max=1.5,
            max_leverage=10,
            funding_rate=0.0,
        )
        assert assess_no_fund.is_feasible is True

        # Sizing with 0.03% (0.0003) adverse funding rate
        assess_with_fund = PositionSizer.calculate_sizing(
            symbol_specs=sol_specs,
            entry_price=100.0,
            stop_loss=98.0,
            available_equity_usdt=17.20,
            target_isolated_margin=1.0,
            target_isolated_margin_max=1.5,
            max_leverage=10,
            funding_rate=0.0003,
        )
        assert assess_with_fund.is_feasible is True
        assert assess_with_fund.estimated_funding_usdt > 0.0
        # Estimated loss with funding must strictly exceed estimated loss without funding
        assert assess_with_fund.estimated_loss_usdt > assess_no_fund.estimated_loss_usdt

        # Check exact fee & slippage calculation:
        # Notional = 0.1 SOL * 100 = 10.0 USDT
        # Exit Notional = 0.1 SOL * 98 = 9.8 USDT
        # Fee = (10.0 * 0.0005) + (9.8 * 0.0005) = 0.005 + 0.0049 = 0.0099
        # Slippage = (10.0 * 0.0005) + (9.8 * 0.0005) = 0.0099
        # Funding = 10.0 * 0.0003 = 0.003
        # Nominal SL loss = 0.1 * 2.0 = 0.20
        # Total SL loss = 0.20 + 0.0099 + 0.0099 + 0.003 = 0.2228 USDT
        assert round(assess_with_fund.estimated_fee_usdt, 4) == 0.0099
        assert round(assess_with_fund.estimated_slippage_usdt, 4) == 0.0099
        assert round(assess_with_fund.estimated_funding_usdt, 4) == 0.0030
        assert round(assess_with_fund.estimated_loss_usdt, 4) == 0.2228


class TestExchangeMinOrderFeasibility:
    """Tests symbol-specific minimum order size and notional feasibility under Tier 1."""

    def test_btcusdt_rejected_as_infeasible_under_tier1(self, btc_specs):
        """BTCUSDT at market prices requires >= $8.15 margin for 0.001 BTC min lot,

        which exceeds Tier 1 maximum target margin of $1.50 and must be rejected.
        """
        tier_cfg = EquityTierConfig()

        # At current price $81,500, min qty 0.001 BTC = $81.50 notional
        # At 10x leverage, required margin is $8.15 > $1.50 limit
        assessment = PositionSizer.calculate_sizing(
            symbol_specs=btc_specs,
            entry_price=81500.0,
            stop_loss=80277.5,  # 1.5% SL
            available_equity_usdt=17.20,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
            tier_config=tier_cfg,
        )
        assert assessment.is_feasible is False
        assert "exceeds maximum target margin limit ($1.50)" in assessment.infeasible_reason

    def test_accessible_symbols_feasible_under_tier1(self, eth_specs, xrp_specs, sol_specs):
        """Symbols with small lot step sizes and $5.00 min notional are fully feasible."""
        tier_cfg = EquityTierConfig()

        # ETH ($2400) -> 0.004 ETH = $9.60 notional -> $0.96 margin (or 0.005 ETH = $12 notional -> $1.20 margin)
        eth_assess = PositionSizer.calculate_sizing(
            symbol_specs=eth_specs,
            entry_price=2400.0,
            stop_loss=2364.0,  # 1.5% SL
            available_equity_usdt=17.20,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
            tier_config=tier_cfg,
        )
        assert eth_assess.is_feasible is True
        assert 1.00 <= eth_assess.required_margin_usdt <= 1.50
        assert eth_assess.position_notional_usdt >= 5.0
        assert eth_assess.estimated_loss_usdt <= 0.35

        # XRP ($1.30) -> 8.0 XRP = $10.40 notional -> $1.04 margin
        xrp_assess = PositionSizer.calculate_sizing(
            symbol_specs=xrp_specs,
            entry_price=1.30,
            stop_loss=1.2805,  # 1.5% SL
            available_equity_usdt=17.20,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
            tier_config=tier_cfg,
        )
        assert xrp_assess.is_feasible is True
        assert 1.00 <= xrp_assess.required_margin_usdt <= 1.50
        assert xrp_assess.estimated_loss_usdt <= 0.35

        # SOL ($104.0) -> 0.10 SOL = $10.40 notional -> $1.04 margin
        sol_assess = PositionSizer.calculate_sizing(
            symbol_specs=sol_specs,
            entry_price=104.0,
            stop_loss=102.44,  # 1.5% SL
            available_equity_usdt=17.20,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
            tier_config=tier_cfg,
        )
        assert sol_assess.is_feasible is True
        assert 1.00 <= sol_assess.required_margin_usdt <= 1.50
        assert sol_assess.estimated_loss_usdt <= 0.35


class TestLossGovernanceAndBreakerScenarios:
    """Tests daily ($0.50) and weekly ($0.54) loss limits, consecutive losses, and resets."""

    def test_daily_loss_budget_allows_multiple_trades(self):
        """Under the proposed $0.50 daily cap, two consecutive $0.20 losses are allowed,

        while the legacy $0.18 cap would have blocked the second trade.
        """
        rm = RiskManager(
            initial_equity_usdt=17.20,
            trade_risk_usdt=0.25,
            daily_loss_limit_usdt=0.50,
            weekly_loss_limit_usdt=0.54,
            max_open_positions=1,
        )
        now_ms = 1792252800000  # Monday 00:00 UTC

        # Initial check: allowed to open position
        can_open, reason = rm.can_open_new_position(now_ms)
        assert can_open is True
        assert rm.get_effective_trade_risk(now_ms) == 0.25

        # First Trade: Loss of $0.20
        t1 = TradeRecord(
            trade_id="T1",
            symbol="ETHUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=now_ms + 1000,
            entry_price=2400.0,
            stop_loss=2364.0,
            take_profit=2500.0,
            quantity=0.005,
            notional_usdt=12.0,
            exit_timestamp_ms=now_ms + 5000,
            exit_price=2360.0,
            pnl_gross=-0.20,
            pnl_net=-0.22,
            is_open=False,
        )
        rm.record_entry(t1)
        rm.record_exit(t1)

        # After 1st loss ($0.22): remaining daily budget is $0.28 ($0.50 - $0.22)
        assert rm.daily_loss_usdt == 0.22
        can_open, reason = rm.can_open_new_position(now_ms + 6000)
        assert can_open is True, "Second trade must be allowed under $0.50 daily cap"
        eff_risk = rm.get_effective_trade_risk(now_ms + 6000)
        assert round(eff_risk, 4) == 0.25  # min(0.25, 0.28, 0.32)

        # Second Trade: Loss of $0.20
        t2 = TradeRecord(
            trade_id="T2",
            symbol="XRPUSDT",
            direction=Direction.SHORT,
            entry_timestamp_ms=now_ms + 7000,
            entry_price=1.30,
            stop_loss=1.32,
            take_profit=1.25,
            quantity=8.0,
            notional_usdt=10.4,
            exit_timestamp_ms=now_ms + 10000,
            exit_price=1.325,
            pnl_gross=-0.20,
            pnl_net=-0.22,
            is_open=False,
        )
        rm.record_entry(t2)
        rm.record_exit(t2)

        # Total daily loss: $0.44. Remaining daily budget: $0.06.
        assert rm.daily_loss_usdt == 0.44
        eff_risk_3 = rm.get_effective_trade_risk(now_ms + 11000)
        assert round(eff_risk_3, 4) == 0.06

        # Third Trade: Loss of $0.08 pushes daily loss to $0.52 (>= $0.50)
        t3 = TradeRecord(
            trade_id="T3",
            symbol="SOLUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=now_ms + 12000,
            entry_price=100.0,
            stop_loss=99.0,
            take_profit=105.0,
            quantity=0.06,
            notional_usdt=6.0,
            exit_timestamp_ms=now_ms + 15000,
            exit_price=98.8,
            pnl_gross=-0.072,
            pnl_net=-0.08,
            is_open=False,
        )
        rm.record_entry(t3)
        rm.record_exit(t3)

        assert rm.daily_loss_usdt == 0.52
        can_open_blocked, block_reason = rm.can_open_new_position(now_ms + 16000)
        assert can_open_blocked is False
        assert "daily loss limit" in block_reason.lower()

    def test_daily_loss_resets_at_midnight_utc(self):
        """Verifies daily loss resets to $0.00 at 00:00 UTC while weekly loss persists."""
        rm = RiskManager(
            initial_equity_usdt=17.20,
            trade_risk_usdt=0.25,
            daily_loss_limit_usdt=0.50,
            weekly_loss_limit_usdt=1.25,
        )
        # Monday 23:00 UTC
        mon_23h_ms = int(datetime(2026, 10, 19, 23, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        t_mon = TradeRecord(
            trade_id="T_MON",
            symbol="ETHUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=mon_23h_ms,
            entry_price=2400.0,
            stop_loss=2364.0,
            take_profit=2500.0,
            quantity=0.005,
            notional_usdt=12.0,
            exit_timestamp_ms=mon_23h_ms + 1000,
            exit_price=2360.0,
            pnl_net=-0.30,
            is_open=False,
        )
        rm.record_entry(t_mon)
        rm.record_exit(t_mon)
        assert rm.daily_loss_usdt == 0.30
        assert rm.weekly_loss_usdt == 0.30

        # Tuesday 00:01 UTC
        tue_00h_ms = int(datetime(2026, 10, 20, 0, 1, 0, tzinfo=timezone.utc).timestamp() * 1000)
        can_open, _ = rm.can_open_new_position(tue_00h_ms)
        # Daily loss must be reset to 0.0
        assert rm.daily_loss_usdt == 0.0
        # Weekly loss persists ($0.30)
        assert rm.weekly_loss_usdt == 0.30
        # Trade is allowed because daily is 0 and weekly remaining is $0.95 ($1.25 - $0.30)
        assert can_open is True
        eff_risk = rm.get_effective_trade_risk(tue_00h_ms)
        assert eff_risk == 0.25

    def test_weekly_loss_cap_blocks_entries_at_1_25(self):
        """Verifies that exceeding the approved weekly loss limit ($1.25) blocks further entries,

        while accommodating 2 full daily loss caps ($0.50) plus an entry buffer on Day 3.
        """
        rm = RiskManager(
            initial_equity_usdt=17.20,
            trade_risk_usdt=0.25,
            daily_loss_limit_usdt=0.50,
            weekly_loss_limit_usdt=1.25,
        )

        # Day 1: Monday - 2 losses of $0.25 = $0.50 (Daily cap reached)
        mon_ms = int(datetime(2026, 10, 19, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        t_m1 = TradeRecord(
            trade_id="T_M1",
            symbol="ETHUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=mon_ms,
            entry_price=2400.0,
            stop_loss=2364.0,
            take_profit=2500.0,
            quantity=0.005,
            notional_usdt=12.0,
            exit_timestamp_ms=mon_ms + 1000,
            exit_price=2360.0,
            pnl_net=-0.25,
            is_open=False,
        )
        t_m2 = TradeRecord(
            trade_id="T_M2",
            symbol="SOLUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=mon_ms + 2000,
            entry_price=100.0,
            stop_loss=98.0,
            take_profit=105.0,
            quantity=0.1,
            notional_usdt=10.0,
            exit_timestamp_ms=mon_ms + 3000,
            exit_price=97.5,
            pnl_net=-0.25,
            is_open=False,
        )
        rm.record_entry(t_m1)
        rm.record_exit(t_m1)
        rm.record_entry(t_m2)
        rm.record_exit(t_m2)

        assert rm.daily_loss_usdt == 0.50
        assert rm.weekly_loss_usdt == 0.50
        can_open_mon, reason_mon = rm.can_open_new_position(mon_ms + 4000)
        assert can_open_mon is False
        assert "daily loss limit" in reason_mon.lower()

        # Day 2: Tuesday - Daily resets to 0.0, weekly remains $0.50. Another $0.50 loss.
        tue_ms = int(datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        can_open_tue, _ = rm.can_open_new_position(tue_ms)
        assert can_open_tue is True, "Day 2 must allow trading under $1.25 weekly cap"

        t_t1 = TradeRecord(
            trade_id="T_T1",
            symbol="XRPUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=tue_ms + 1000,
            entry_price=1.30,
            stop_loss=1.28,
            take_profit=1.35,
            quantity=7.7,
            notional_usdt=10.0,
            exit_timestamp_ms=tue_ms + 2000,
            exit_price=1.27,
            pnl_net=-0.25,
            is_open=False,
        )
        t_t2 = TradeRecord(
            trade_id="T_T2",
            symbol="ADAUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=tue_ms + 3000,
            entry_price=0.225,
            stop_loss=0.220,
            take_profit=0.235,
            quantity=45.0,
            notional_usdt=10.125,
            exit_timestamp_ms=tue_ms + 4000,
            exit_price=0.219,
            pnl_net=-0.25,
            is_open=False,
        )
        rm.record_entry(t_t1)
        rm.record_exit(t_t1)
        rm.record_entry(t_t2)
        rm.record_exit(t_t2)

        assert rm.daily_loss_usdt == 0.50
        assert rm.weekly_loss_usdt == 1.00
        can_open_tue2, reason_tue2 = rm.can_open_new_position(tue_ms + 5000)
        assert can_open_tue2 is False
        assert "daily loss limit" in reason_tue2.lower()

        # Day 3: Wednesday - Daily resets to 0.0, weekly remains $1.00. Remaining weekly buffer = $0.25.
        wed_ms = int(datetime(2026, 10, 21, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        can_open_wed, _ = rm.can_open_new_position(wed_ms)
        assert can_open_wed is True, "Day 3 must allow trading with remaining $0.25 weekly budget"
        eff_risk_wed = rm.get_effective_trade_risk(wed_ms)
        assert eff_risk_wed == 0.25  # min(0.25, 0.50, 0.25)

        t_w1 = TradeRecord(
            trade_id="T_W1",
            symbol="LINKUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=wed_ms + 1000,
            entry_price=12.05,
            stop_loss=11.75,
            take_profit=12.65,
            quantity=0.83,
            notional_usdt=10.0,
            exit_timestamp_ms=wed_ms + 2000,
            exit_price=11.70,
            pnl_net=-0.25,
            is_open=False,
        )
        rm.record_entry(t_w1)
        rm.record_exit(t_w1)

        # Cumulative weekly loss reaches $1.25!
        assert rm.daily_loss_usdt == 0.25  # Daily cap not breached ($0.25 < $0.50)
        assert rm.weekly_loss_usdt == 1.25  # Weekly cap breached ($1.25 >= $1.25)

        can_open_wed_blocked, reason_wed_blocked = rm.can_open_new_position(wed_ms + 3000)
        assert can_open_wed_blocked is False
        assert "weekly loss limit" in reason_wed_blocked.lower()
        assert "$1.25" in reason_wed_blocked

    def test_weekly_loss_reset_on_monday_utc(self):
        """Verifies that accumulated weekly loss ($1.25) completely resets on Monday 00:00 UTC."""
        rm = RiskManager(
            initial_equity_usdt=17.20,
            trade_risk_usdt=0.25,
            daily_loss_limit_usdt=0.50,
            weekly_loss_limit_usdt=1.25,
        )
        # Sunday 23:30 UTC - weekly loss capped out at $1.25
        sun_ms = int(datetime(2026, 10, 25, 23, 30, 0, tzinfo=timezone.utc).timestamp() * 1000)
        t_sun = TradeRecord(
            trade_id="T_SUN",
            symbol="ETHUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=sun_ms,
            entry_price=2400.0,
            stop_loss=2364.0,
            take_profit=2500.0,
            quantity=0.02,
            notional_usdt=48.0,
            exit_timestamp_ms=sun_ms + 1000,
            exit_price=2300.0,
            pnl_net=-1.25,
            is_open=False,
        )
        rm.record_entry(t_sun)
        rm.record_exit(t_sun)
        assert rm.weekly_loss_usdt == 1.25
        can_open_sun, _ = rm.can_open_new_position(sun_ms + 2000)
        assert can_open_sun is False

        # Next Monday 00:01 UTC - new calendar week bucket!
        mon_next_ms = int(datetime(2026, 10, 26, 0, 1, 0, tzinfo=timezone.utc).timestamp() * 1000)
        can_open_mon, block_reason = rm.can_open_new_position(mon_next_ms)
        assert can_open_mon is True
        assert block_reason is None
        assert rm.weekly_loss_usdt == 0.0
        assert rm.daily_loss_usdt == 0.0

    def test_restart_recovery_from_persistent_journal_with_1_25_weekly_cap(self, tmp_path):
        """Verifies that restarting RiskManager and syncing from SQLite journal

        accurately reconstructs $1.25 weekly loss accounting without double counting.
        """
        from src.data.database import DatabaseManager
        from src.journal.repository import JournalRepository

        db_file = tmp_path / "test_restart_journal.db"
        db = DatabaseManager(db_file)
        journal = JournalRepository(db)

        # Setup 4 closed trades in journal across 2 days within current week
        mon_ms = int(datetime(2026, 10, 19, 14, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        tue_ms = int(datetime(2026, 10, 20, 15, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # Monday trades: 2 losses of $0.25 (net pnl = -0.25, fee = 0.01, slip = 0.01, funding = 0.001)
        tr1 = TradeRecord(
            trade_id="TR_1",
            symbol="ETHUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=mon_ms,
            entry_price=2400.0,
            stop_loss=2360.0,
            take_profit=2500.0,
            quantity=0.005,
            notional_usdt=12.0,
            exit_timestamp_ms=mon_ms + 10000,
            exit_price=2360.0,
            pnl_gross=-0.20,
            pnl_net=-0.25,
            fee_paid=0.024,
            funding_paid=0.001,
            slippage_paid=0.024,
            exit_reason="STOP_LOSS",
            is_open=False,
            entry_category=EntryCategory.BREAKOUT,
        )
        journal.save_trade(tr1)

        tr2 = TradeRecord(
            trade_id="TR_2",
            symbol="SOLUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=mon_ms + 20000,
            entry_price=100.0,
            stop_loss=98.0,
            take_profit=105.0,
            quantity=0.1,
            notional_usdt=10.0,
            exit_timestamp_ms=mon_ms + 30000,
            exit_price=98.0,
            pnl_gross=-0.20,
            pnl_net=-0.25,
            fee_paid=0.02,
            funding_paid=0.001,
            slippage_paid=0.02,
            exit_reason="STOP_LOSS",
            is_open=False,
            entry_category=EntryCategory.BREAKOUT,
        )
        journal.save_trade(tr2)

        # Tuesday trades: 3 losses of $0.25 -> total weekly = $1.25
        for i, tid in enumerate(["TR_3", "TR_4", "TR_5"]):
            tri = TradeRecord(
                trade_id=tid,
                symbol="ADAUSDT",
                direction=Direction.LONG,
                entry_timestamp_ms=tue_ms + (i * 10000),
                entry_price=0.225,
                stop_loss=0.220,
                take_profit=0.235,
                quantity=45.0,
                notional_usdt=10.125,
                exit_timestamp_ms=tue_ms + (i * 10000) + 5000,
                exit_price=0.220,
                pnl_gross=-0.22,
                pnl_net=-0.25,
                fee_paid=0.015,
                funding_paid=0.001,
                slippage_paid=0.015,
                exit_reason="STOP_LOSS",
                is_open=False,
                entry_category=EntryCategory.BREAKOUT,
            )
            journal.save_trade(tri)

        # Simulate fresh application startup / restart:
        fresh_rm = RiskManager(
            initial_equity_usdt=17.20,
            trade_risk_usdt=0.25,
            daily_loss_limit_usdt=0.50,
            weekly_loss_limit_usdt=1.25,
        )
        # Authoritatively restore state from journal
        eval_now_ms = tue_ms + 40000
        fresh_rm.sync_state_from_journal(journal, now_ms=eval_now_ms)

        # Tuesday daily loss is 3 * $0.25 = $0.75
        assert fresh_rm.daily_loss_usdt == 0.75
        # Total weekly loss is 5 * $0.25 = $1.25
        assert fresh_rm.weekly_loss_usdt == 1.25
        # Initial equity $17.20 - $1.25 = $15.95
        assert round(fresh_rm.equity_usdt, 2) == 15.95

        # Verify circuit breaker trips immediately upon restart
        can_open_restart, restart_reason = fresh_rm.can_open_new_position(eval_now_ms)
        assert can_open_restart is False
        assert "daily loss limit" in restart_reason.lower() or "weekly loss limit" in restart_reason.lower()

        # Deduplication check: repeated calls do not double count
        fresh_rm.sync_state_from_journal(journal, now_ms=eval_now_ms)
        assert fresh_rm.daily_loss_usdt == 0.75
        assert fresh_rm.weekly_loss_usdt == 1.25


class TestPropertyBasedRiskBudgetEnforcement:
    """Property-based and parameterized tests for exact mathematical risk calculation and budget enforcement."""

    @pytest.mark.parametrize(
        "sym,price,min_q,step_q,min_notional,prec",
        [
            ("ADAUSDT", 0.225, 1.0, 1.0, 5.0, 4),
            ("XRPUSDT", 1.30, 0.1, 0.1, 5.0, 4),
            ("ETHUSDT", 2415.0, 0.001, 0.001, 5.0, 2),
            ("SOLUSDT", 104.20, 0.01, 0.01, 5.0, 2),
            ("AVAXUSDT", 9.87, 1.0, 1.0, 5.0, 4),
            ("LINKUSDT", 12.05, 0.01, 0.01, 5.0, 4),
            ("DOGEUSDT", 0.082, 1.0, 1.0, 5.0, 4),
            ("LTCUSDT", 59.29, 0.001, 0.001, 5.0, 2),
        ],
    )
    @pytest.mark.parametrize("stop_dist", [0.008, 0.010, 0.015, 0.020, 0.025, 0.035])
    def test_mathematical_formula_and_budget_invariants(
        self, sym, price, min_q, step_q, min_notional, prec, stop_dist
    ):
        """Verifies:
        1. Exact formula equality:
           estimated_loss == qty * abs(entry - sl) + entry_costs + exit_costs + adverse_funding.
        2. Strict budget enforcement:
           Whenever feasible, estimated_loss <= 0.250000001 (never silently exceeds $0.25).
        3. Structural invariants:
           Whenever feasible, position_notional >= min_notional and margin <= $1.50.
        4. Rejection invariant:
           If cannot fit within $0.25 budget, must be rejected as INFEASIBLE.
        """
        specs = SymbolSpecs(
            symbol=sym,
            base_asset=sym.replace("USDT", ""),
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=10 ** (-prec),
            price_precision=prec,
            step_size=step_q,
            quantity_precision=3,
            min_qty=min_q,
            max_qty=100000.0,
            min_notional=min_notional,
            maint_margin_percent=0.005,
        )

        entry_p = price
        sl_p = round(entry_p * (1.0 - stop_dist), prec)
        fee_rate = 0.0005
        slippage_rate = 0.0005
        funding_rate = 0.0001
        risk_budget = 0.25

        ra = PositionSizer.calculate_sizing(
            symbol_specs=specs,
            entry_price=entry_p,
            stop_loss=sl_p,
            target_isolated_margin=1.0,
            target_isolated_margin_max=1.5,
            available_equity_usdt=17.20,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
            risk_budget_usdt=risk_budget,
            funding_rate=funding_rate,
            enforce_risk_budget=True,
            allow_risk_step_down=True,
        )

        # 1. Formula verification on returned assessment
        q = ra.quantity
        if q > 0:
            nominal_loss = q * abs(entry_p - sl_p)
            entry_notional = q * entry_p
            exit_notional = q * sl_p
            entry_costs = entry_notional * (fee_rate + slippage_rate)
            exit_costs = exit_notional * (fee_rate + slippage_rate)
            funding_cost = entry_notional * abs(funding_rate)
            expected_loss = nominal_loss + entry_costs + exit_costs + funding_cost

            assert abs(ra.estimated_fee_usdt - ((entry_notional * fee_rate) + (exit_notional * fee_rate))) < 1e-6
            assert abs(ra.estimated_slippage_usdt - ((entry_notional * slippage_rate) + (exit_notional * slippage_rate))) < 1e-6
            assert abs(ra.estimated_funding_usdt - funding_cost) < 1e-6
            assert abs(ra.estimated_loss_usdt - expected_loss) < 1e-6

        # 2. Strict budget invariant
        if ra.is_feasible:
            assert ra.estimated_loss_usdt <= risk_budget + 1e-9, (
                f"{sym} at {stop_dist*100}% SL produced loss {ra.estimated_loss_usdt} > budget {risk_budget}"
            )
            assert ra.position_notional_usdt >= specs.min_notional
            assert ra.required_margin_usdt <= 1.50 + 1e-9
        else:
            # If infeasible due to risk budget, reason must state it
            assert ra.infeasible_reason is not None

    def test_minimum_lot_overflow_never_silently_expands_risk_budget(self):
        """When an asset's minimum lot cannot be stepped down and produces loss > budget,

        it MUST be rejected as INFEASIBLE.
        """
        # AVAXUSDT with 1.0 AVAX min lot.
        # At price $9.87 and 3.5% stop distance:
        # Nominal loss = 1.0 * 0.34545 = 0.345
        # Costs = ~0.02
        # Total loss = ~0.365 > $0.25.
        # Min lot is 1.0, so it cannot step down.
        avax_specs = SymbolSpecs(
            symbol="AVAXUSDT",
            base_asset="AVAX",
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=0.0001,
            price_precision=4,
            step_size=1.0,
            quantity_precision=1,
            min_qty=1.0,
            max_qty=10000.0,
            min_notional=5.0,
            maint_margin_percent=0.005,
        )
        ra = PositionSizer.calculate_sizing(
            symbol_specs=avax_specs,
            entry_price=9.87,
            stop_loss=9.5245,  # 3.5% SL
            target_isolated_margin=1.0,
            target_isolated_margin_max=1.5,
            available_equity_usdt=17.20,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
            risk_budget_usdt=0.25,
            funding_rate=0.0001,
            enforce_risk_budget=True,
            allow_risk_step_down=True,
        )
        assert ra.is_feasible is False
        assert "exceeds effective trade risk budget of $0.25" in ra.infeasible_reason

