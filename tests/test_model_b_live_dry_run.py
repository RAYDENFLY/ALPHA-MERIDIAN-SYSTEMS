"""Tests for Model B Live Execution Adapter & Dry-Run Shadow Verification Layer.

Validates the 4 core live-readiness requirements under ~$16 USDT Aster DEX micro-profile:
1. Dedicated Live Micro-Account Risk Profile (ModelBLiveMicroConfig vs paper config).
2. Aster DEX min_notional / min_qty TP1 exit plan adaptation (FULL_CLOSE_TP1 vs PARTIAL_TP1_RUNNER_TP2).
3. Strict isolation of manual live positions (specifically protecting SUIUSDT manual trade).
4. Real-time LIVE_DRY_RUN telemetry, compliant order payloads, and /api/model_b/status exposure.
"""

from unittest.mock import MagicMock
import pytest
from starlette.testclient import TestClient

from config.settings import ModelBConfig, ModelBLiveMicroConfig, get_default_config
from src.core.types import (
    CandidateStatus,
    Direction,
    MarketBias,
    RiskAssessment,
    SetupCandidate,
    StructureState,
)
from src.exchange.metadata import SymbolSpecs
from src.execution.model_b_live_preflight import (
    ExitPlanType,
    LivePreFlightResult,
    ModelBLivePreFlightValidator,
    determine_exit_plan,
    round_step_size,
    round_tick_size,
)
from src.execution.v3_lifecycle import (
    EmergencyPolicy,
    ManagedPosition,
    ProtectionState,
    V3OrderLifecycleManager,
)
from src.risk.manager import RiskManager


def _make_candidate(
    symbol: str = "SUIUSDT",
    direction: Direction = Direction.LONG,
    entry: float = 0.9934,
    sl: float = 0.9218,
    tp1: float = 1.0650,
    tp2: float = 1.1366,
    candidate_id: str = "CAND_SUI_TEST_001",
) -> SetupCandidate:
    risk_dist = abs(entry - sl)
    raw_qty = 0.45 / risk_dist if risk_dist > 0 else 1.0
    return SetupCandidate(
        candidate_id=candidate_id,
        symbol=symbol,
        timestamp_ms=1729000000000,
        direction=direction,
        bias_4h=MarketBias.BULLISH if direction == Direction.LONG else MarketBias.BEARISH,
        structure_1h=StructureState.BULLISH if direction == Direction.LONG else StructureState.BEARISH,
        status=CandidateStatus.READY,
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp1,
        take_profit_2=tp2,
        risk_assessment=RiskAssessment(
            symbol=symbol,
            entry_price=entry,
            stop_loss=sl,
            quantity=raw_qty,
            risk_budget_usdt=0.45,
            is_feasible=True,
        ),
        expires_at_ms=1729000000000 + 3600000,
    )


class TestModelBLiveMicroConfig:
    """1. Dedicated Live Micro-Account Risk Profile."""

    def test_micro_config_defaults_and_separation_from_paper(self):
        cfg = get_default_config()

        # Paper configuration must remain untouched for rapid burn-in sample collection
        assert cfg.model_b.target_trade_risk_usdt == 1.0
        assert cfg.model_b.max_open_positions == 6
        assert cfg.model_b.max_open_per_direction == 3
        assert cfg.model_b.daily_loss_limit_usdt == 3.0
        assert cfg.model_b.weekly_loss_limit_usdt == 7.5

        # Dedicated live micro-account settings must reflect ~$16 USDT Aster account governance
        assert cfg.model_b.live_target_trade_risk_usdt == 0.45
        assert cfg.model_b.live_max_margin_usdt == 4.00
        assert cfg.model_b.live_daily_loss_limit_usdt == 1.35  # 3R daily stop
        assert cfg.model_b.live_weekly_loss_limit_usdt == 3.00
        assert cfg.model_b.live_max_open_positions == 2
        assert cfg.model_b.live_max_open_per_direction == 1

        micro = ModelBLiveMicroConfig()
        assert micro.target_trade_risk_usdt == 0.45
        assert micro.max_margin_usdt == 4.00
        assert micro.daily_loss_limit_usdt == 1.35
        assert micro.weekly_loss_limit_usdt == 3.00
        assert micro.max_open_positions == 2
        assert micro.max_open_per_direction == 1
        assert micro.min_notional_buffer == 5.50
        assert micro.leverage == 10


class TestExitPlanAdaptation:
    """2. Aster DEX min_qty / min_notional adaptation on TP1 partial close."""

    def test_sui_micro_position_switches_to_full_close_tp1(self):
        # SUI @ 0.9934 with 0.0716 SL distance -> 6.2 SUI ($6.16 notional)
        # 50% split would be 3.1 SUI ($3.08 notional), which is below $5.50 min_notional buffer
        plan, reason, tp1_qty, runner_qty = determine_exit_plan(
            quantity=6.2,
            entry_price=0.9934,
            min_qty=0.1,
            min_notional=5.0,
            step_size=0.1,
            min_notional_buffer=5.50,
        )
        assert plan == ExitPlanType.FULL_CLOSE_TP1
        assert tp1_qty == 6.2
        assert runner_qty == 0.0
        assert "below exchange min_notional threshold" in reason

    def test_large_position_retains_partial_tp1_and_runner_tp2(self):
        # BTC position with 0.006 BTC @ $60,000 -> $360 notional
        # 50% split is 0.003 BTC ($180 notional), well above $5.50 buffer
        plan, reason, tp1_qty, runner_qty = determine_exit_plan(
            quantity=0.006,
            entry_price=60000.0,
            min_qty=0.001,
            min_notional=5.0,
            step_size=0.001,
            min_notional_buffer=5.50,
        )
        assert plan == ExitPlanType.PARTIAL_TP1_RUNNER_TP2
        assert tp1_qty == 0.003
        assert runner_qty == 0.003
        assert "satisfies exchange min_notional buffer" in reason

    def test_sub_min_qty_split_switches_to_full_close(self):
        # Quantity 0.001 BTC where min_qty is 0.001
        # 50% would be 0.0005 < 0.001 min_qty
        plan, reason, tp1_qty, runner_qty = determine_exit_plan(
            quantity=0.001,
            entry_price=60000.0,
            min_qty=0.001,
            min_notional=5.0,
            step_size=0.001,
            min_notional_buffer=5.50,
        )
        assert plan == ExitPlanType.FULL_CLOSE_TP1
        assert tp1_qty == 0.001
        assert runner_qty == 0.0

    def test_v3_lifecycle_executes_full_close_tp1(self):
        adapter = MagicMock()
        adapter.create_order.return_value = {"orderId": 501, "status": "FILLED"}
        adapter.cancel_order.return_value = {"status": "CANCELED"}
        adapter.get_open_orders.return_value = []

        risk_mgr = RiskManager()
        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)

        pos = ManagedPosition(
            candidate_id="CAND_SUI_001",
            symbol="SUIUSDT",
            direction=Direction.LONG,
            intended_quantity=6.2,
            filled_quantity=6.2,
            entry_price=0.9934,
            stop_loss_price=0.9218,
            take_profit_price=1.0650,
            take_profit_2_price=1.1366,
            sl_order_id="SL_101",
            tp_order_id="TP_102",
            is_open=True,
            protection_state=ProtectionState.PROTECTED,
            exit_plan="FULL_CLOSE_TP1",
        )

        ok, msg = manager.transition_to_tp1_breakeven(pos, executed_tp1_qty=6.2)
        assert ok is True
        assert "FULL_CLOSE_TP1 exit plan" in msg
        assert pos.is_open is False
        assert pos.protection_state == ProtectionState.NONE
        # SL order must have been cancelled when full position closed
        adapter.cancel_order.assert_called_with(symbol="SUIUSDT", order_id="SL_101")


class TestStrictManualPositionIsolation:
    """3. Strict Isolation from Manual Live Positions (Protect Manual SUIUSDT)."""

    def test_inspect_manual_positions_detects_live_sui_trade(self):
        acc_svc = MagicMock()
        snap = MagicMock()
        snap.positions = [
            {
                "symbol": "SUIUSDT",
                "raw_position_amt": 7.0,
                "entry_price": 0.9934,
                "mark_price": 0.9950,
                "unrealized_pnl_usdt": 0.0112,
                "allocated_margin_usdt": 0.33,
                "protection_status": "PROTECTED",
                "client_order_id": None,  # Manual Aster web trade
            }
        ]
        acc_svc.get_snapshot.return_value = snap

        validator = ModelBLivePreFlightValidator(account_service=acc_svc)
        manual_positions = validator.inspect_manual_positions()

        assert len(manual_positions) == 1
        pos = manual_positions[0]
        assert pos["symbol"] == "SUIUSDT"
        assert pos["side"] == "LONG"
        assert pos["position_amt"] == 7.0
        assert pos["isolation_status"] == "MANUAL_EXTERNAL_PROTECTED"
        assert pos["action_policy"] == "NO_TOUCH_MANUAL_POSITION"
        assert pos["is_safe_from_automated_actions"] is True

    def test_v3_lifecycle_client_order_prefix_and_orphan_purge_safety(self):
        adapter = MagicMock()
        # Open orders on Aster DEX: 2 manual SUIUSDT orders + 1 bot order
        adapter.get_open_orders.return_value = [
            {"orderId": "1929386102", "type": "TAKE_PROFIT_MARKET", "clientOrderId": ""},  # Manual SUI TP
            {"orderId": "1929386103", "type": "STOP_MARKET", "clientOrderId": ""},         # Manual SUI SL
            {"orderId": "777001", "type": "STOP_MARKET", "clientOrderId": "MODEL_B_SL_cand1_999"},  # Bot SL
        ]
        adapter.cancel_order.return_value = {"status": "CANCELED"}

        risk_mgr = RiskManager()
        manager = V3OrderLifecycleManager(
            adapter=adapter,
            risk_manager=risk_mgr,
            client_order_prefix="MODEL_B",
            isolated_manual_symbols={"SUIUSDT"},
        )

        # 1. Purge on isolated symbol immediately skips everything
        cancelled_isolated = manager.purge_orphaned_protective_orders("SUIUSDT")
        assert cancelled_isolated == []
        adapter.cancel_order.assert_not_called()

        # 2. Purge on a non-isolated symbol only cancels bot-owned orders
        manager.isolated_manual_symbols = set()
        cancelled = manager.purge_orphaned_protective_orders("SUIUSDT")
        assert "777001" in cancelled
        assert "1929386102" not in cancelled
        assert "1929386103" not in cancelled
        adapter.cancel_order.assert_called_once_with(symbol="SUIUSDT", order_id="777001")

    def test_reconcile_startup_skips_isolated_manual_symbol(self):
        adapter = MagicMock()
        risk_mgr = RiskManager()
        manager = V3OrderLifecycleManager(
            adapter=adapter,
            risk_manager=risk_mgr,
            isolated_manual_symbols={"SUIUSDT"},
        )

        ok, msg = manager.reconcile_startup_protection("SUIUSDT")
        assert ok is True
        assert "safely isolated" in msg
        adapter.get_positions.assert_not_called()

    def test_entry_blocked_on_isolated_manual_symbol(self):
        adapter = MagicMock()
        risk_mgr = RiskManager()
        manager = V3OrderLifecycleManager(
            adapter=adapter,
            risk_manager=risk_mgr,
            isolated_manual_symbols={"SUIUSDT"},
        )
        cand = _make_candidate(symbol="SUIUSDT")
        pos = manager.execute_order_lifecycle(cand)
        assert pos.entry_status == "BLOCKED_MANUAL_ISOLATION"
        adapter.create_order.assert_not_called()


class TestLivePreFlightValidatorAndApiStatus:
    """4. Real-time LIVE_DRY_RUN telemetry & /api/model_b/status integration."""

    def test_validator_generates_compliant_order_payload(self):
        acc_svc = MagicMock()
        snap = MagicMock()
        snap.total_equity_usdt = 15.92
        snap.available_balance_usdt = 14.39
        snap.positions = []
        acc_svc.get_snapshot.return_value = snap

        specs = SymbolSpecs(
            symbol="SUIUSDT",
            base_asset="SUI",
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
            maint_margin_percent=2.5,
        )

        validator = ModelBLivePreFlightValidator(account_service=acc_svc)
        cand = _make_candidate()
        result: LivePreFlightResult = validator.validate_candidate(cand, symbol_specs=specs)

        assert result.symbol == "SUIUSDT"
        assert result.direction == "LONG"
        assert result.risk_budget_usdt == 0.45
        assert result.rounded_quantity == 6.2
        assert result.rounded_entry_price == 0.9934
        assert result.position_notional_usdt == pytest.approx(6.2 * 0.9934, abs=0.01)
        assert result.required_margin_usdt <= 4.00
        assert result.exit_plan == "FULL_CLOSE_TP1"
        assert result.is_compliant is True
        assert len(result.rejection_reasons) == 0

        # Verify order payloads have MODEL_B_ client_order_id prefixes and correct types
        payload = result.dry_run_order_payload
        assert payload["mode"] == "DRY_RUN_SHADOW"
        assert payload["live_trading_locked"] is True
        assert payload["entry_order"]["client_order_id"].startswith("MODEL_B_ENT_")
        assert payload["stop_loss_order"]["client_order_id"].startswith("MODEL_B_SL_")
        assert payload["take_profit_1_order"]["client_order_id"].startswith("MODEL_B_TP1_")
        assert payload["take_profit_2_order"] is None  # FULL_CLOSE_TP1 disables TP2 order

    def test_validator_enforces_concurrency_and_directional_caps(self):
        validator = ModelBLivePreFlightValidator()
        cand = _make_candidate()

        # At concurrency cap (2 open positions)
        res_conc = validator.validate_candidate(cand, live_open_positions_count=2, live_open_direction_count=0)
        assert res_conc.is_compliant is False
        assert any("concurrency cap reached" in r for r in res_conc.rejection_reasons)

        # At directional cap (1 LONG position already open)
        res_dir = validator.validate_candidate(cand, live_open_positions_count=1, live_open_direction_count=1)
        assert res_dir.is_compliant is False
        assert any("directional position cap reached" in r for r in res_dir.rejection_reasons)

    def test_model_b_status_endpoint_returns_live_readiness_dry_run(self):
        from src.api.app import app
        client = TestClient(app)

        res = client.get("/api/model_b/status")
        assert res.status_code == 200
        data = res.json()

        assert data["live_trading_locked"] is True
        assert "live_readiness_dry_run" in data
        dry_run = data["live_readiness_dry_run"]
        assert dry_run is not None
        assert dry_run["mode"] == "DRY_RUN_SHADOW"
        assert dry_run["live_trading_locked"] is True
        assert dry_run["profile_name"] == "MODEL_B_LIVE_MICRO_16USDT"
        assert dry_run["manual_isolation_enforced"] is True

        # Check micro risk configuration
        profile = dry_run["micro_profile"]
        assert profile["target_trade_risk_usdt"] == 0.45
        assert profile["max_margin_usdt"] == 4.00
        assert profile["daily_loss_limit_usdt"] == 1.35
        assert profile["weekly_loss_limit_usdt"] == 3.00
        assert profile["max_open_positions"] == 2
        assert profile["max_open_per_direction"] == 1

        # Check live wallet state
        wallet = dry_run["wallet_state"]
        assert "connection_status" in wallet
        assert wallet["total_equity_usdt"] > 0.0
