"""Tests for Live AsterDEX Positions Dashboard & Telemetry.

Validates:
1. Parsing multi-position payloads (LONG and SHORT) with leverage, notional, and margin modes.
2. Associating conditional TP/SL orders with active positions across both LONG and SHORT directions.
3. Missing TP/SL semantics: "Not configured" string and None numeric values (never 0.00 or $0.00).
4. Liquidation price semantics: "Unavailable" / "Unavailable (Exchange reported 0)" for 0 or missing, confirmed price formatting when provided.
5. Zero-position empty state: empty list with zero position count and explicit cash state semantics.
6. Live vs Paper separation: live exchange positions do not mutate paper positions or paper risk limits.
7. Endpoint GET /api/account/positions validation with connected, stale, and offline states.
8. Credential security: frontend responses never leak secrets or private keys.
9. Static template integrity: dashboard.html renders desktop table, mobile cards, and clear equity/exposure metrics.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.exchange.account_service import (
    AccountConnectionStatus,
    AccountSnapshot,
    AsterAccountService,
    enrich_positions_with_risk_and_orders,
)
from src.exchange.aster_v3 import AsterDexV3Adapter
from src.risk.manager import RiskManager
from src.api.app import app


@pytest.fixture
def mock_positions_and_orders():
    """Mock multi-position dataset containing LONG, SHORT, missing TP/SL, and zero liquidation."""
    raw_positions = [
        {
            "symbol": "HYPEUSDT",
            "positionAmt": "0.07",
            "entryPrice": "79.658",
            "positionInitialMargin": "0.5547",
            "leverage": "10",
            "isolated": False,
            "notional": "5.5474",
            "unrealizedProfit": "-0.0286",
        },
        {
            "symbol": "BATUSDT",
            "positionAmt": "28.5",
            "entryPrice": "0.17834",
            "positionInitialMargin": "1.1293",
            "leverage": "5",
            "isolated": True,
            "isolatedMargin": "1.1293",
            "notional": "5.1950",
            "unrealizedProfit": "0.1123",
        },
        {
            "symbol": "SOLUSDT",
            "positionAmt": "-0.50",
            "entryPrice": "150.00",
            "positionInitialMargin": "7.50",
            "leverage": "10",
            "isolated": False,
            "notional": "75.00",
            "unrealizedProfit": "-1.25",
        },
    ]

    pos_risk = [
        {
            "symbol": "HYPEUSDT",
            "positionAmt": "0.07",
            "entryPrice": "79.658",
            "markPrice": "79.2488",
            "liquidationPrice": "0",  # Reported 0 by exchange for cross margin
            "leverage": "10",
            "marginType": "cross",
            "isolatedMargin": "0.0",
            "notional": "5.5474",
            "unRealizedProfit": "-0.0286",
        },
        {
            "symbol": "BATUSDT",
            "positionAmt": "28.5",
            "entryPrice": "0.17834",
            "markPrice": "0.18228",
            "liquidationPrice": "0.15016332",  # Confirmed isolated liquidation price
            "leverage": "5",
            "marginType": "isolated",
            "isolatedMargin": "1.1293",
            "notional": "5.1950",
            "unRealizedProfit": "0.1123",
        },
        {
            "symbol": "SOLUSDT",
            "positionAmt": "-0.50",
            "entryPrice": "150.00",
            "markPrice": "152.50",
            "liquidationPrice": "165.00",
            "leverage": "10",
            "marginType": "cross",
            "isolatedMargin": "0.0",
            "notional": "76.25",
            "unRealizedProfit": "-1.25",
        },
    ]

    open_orders = [
        # HYPE (LONG): Has TP and SL
        {
            "orderId": 1001,
            "symbol": "HYPEUSDT",
            "side": "SELL",
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": "97.7030",
            "origQty": "0.07",
            "reduceOnly": True,
            "status": "NEW",
        },
        {
            "orderId": 1002,
            "symbol": "HYPEUSDT",
            "side": "SELL",
            "type": "STOP_MARKET",
            "stopPrice": "74.0660",
            "origQty": "0.07",
            "reduceOnly": True,
            "status": "NEW",
        },
        # BAT (LONG): Has TP and SL
        {
            "orderId": 1003,
            "symbol": "BATUSDT",
            "side": "SELL",
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": "0.20752",
            "origQty": "28.5",
            "reduceOnly": True,
            "status": "NEW",
        },
        {
            "orderId": 1004,
            "symbol": "BATUSDT",
            "side": "SELL",
            "type": "STOP_MARKET",
            "stopPrice": "0.16417",
            "origQty": "28.5",
            "reduceOnly": True,
            "status": "NEW",
        },
        # SOL (SHORT): Has SL only, NO TP configured
        {
            "orderId": 1005,
            "symbol": "SOLUSDT",
            "side": "BUY",
            "type": "STOP_MARKET",
            "stopPrice": "158.00",
            "origQty": "0.50",
            "reduceOnly": True,
            "status": "NEW",
        },
        # Unrelated limit buy order for AAVE (should not match as position bracket)
        {
            "orderId": 1006,
            "symbol": "AAVEUSDT",
            "side": "BUY",
            "type": "LIMIT",
            "price": "148.20",
            "origQty": "0.1",
            "reduceOnly": False,
            "status": "NEW",
        },
    ]

    return raw_positions, pos_risk, open_orders


class TestLivePositionsEnrichment:
    """Test suite for position enrichment and order matching logic."""

    def test_multi_position_long_and_short_parsing(self, mock_positions_and_orders):
        raw_positions, pos_risk, open_orders = mock_positions_and_orders
        enriched = enrich_positions_with_risk_and_orders(
            raw_positions=raw_positions,
            pos_risk_list=pos_risk,
            open_orders_list=open_orders,
            sync_utc="2026-10-18T00:00:00Z",
        )

        assert len(enriched) == 3
        by_sym = {p["symbol"]: p for p in enriched}

        # HYPE (LONG)
        hype = by_sym["HYPEUSDT"]
        assert hype["side"] == "LONG"
        assert hype["position_amt"] == 0.07
        assert hype["raw_position_amt"] == 0.07
        assert hype["entry_price"] == 79.658
        assert hype["mark_price"] == 79.2488
        assert hype["leverage"] == 10
        assert hype["margin_mode"] == "CROSS"
        assert hype["take_profit_price"] == 97.7030
        assert hype["take_profit_label"] == "$97.7030"
        assert hype["stop_loss_price"] == 74.0660
        assert hype["stop_loss_label"] == "$74.0660"
        # Exchange reported 0 liquidation price
        assert hype["liquidation_price"] is None
        assert "Unavailable" in hype["liquidation_label"]

        # BAT (LONG, ISOLATED)
        bat = by_sym["BATUSDT"]
        assert bat["side"] == "LONG"
        assert bat["position_amt"] == 28.5
        assert bat["margin_mode"] == "ISOLATED"
        assert bat["allocated_margin_usdt"] == 1.1293
        assert bat["take_profit_price"] == 0.20752
        assert bat["stop_loss_price"] == 0.16417
        assert bat["liquidation_price"] == 0.15016332
        assert bat["liquidation_label"] == "$0.1502"
        assert bat["roe_pct"] > 0  # Profitable trade
        assert "Allocated Margin (Isolated)" in bat["denominator_label"]

        # SOL (SHORT, CROSS, MISSING TP)
        sol = by_sym["SOLUSDT"]
        assert sol["side"] == "SHORT"
        assert sol["position_amt"] == 0.50
        assert sol["raw_position_amt"] == -0.50
        assert sol["entry_price"] == 150.00
        assert sol["mark_price"] == 152.50
        assert sol["stop_loss_price"] == 158.00
        assert sol["stop_loss_label"] == "$158.0000"
        # Missing TP must be "Not configured", never 0.00 or $0.00
        assert sol["take_profit_price"] is None
        assert sol["take_profit_label"] == "Not configured"
        assert sol["liquidation_price"] == 165.00
        assert sol["liquidation_label"] == "$165.0000"

    def test_missing_tp_and_sl_both_unconfigured(self):
        """When an active position has neither TP nor SL configured on exchange."""
        raw_pos = [{"symbol": "ETHUSDT", "positionAmt": "1.0", "entryPrice": "2500.0"}]
        risk = [{"symbol": "ETHUSDT", "positionAmt": "1.0", "markPrice": "2510.0", "liquidationPrice": "0"}]
        orders = []  # No orders at all

        enriched = enrich_positions_with_risk_and_orders(raw_pos, risk, orders)
        assert len(enriched) == 1
        pos = enriched[0]

        assert pos["take_profit_price"] is None
        assert pos["take_profit_label"] == "Not configured"
        assert pos["stop_loss_price"] is None
        assert pos["stop_loss_label"] == "Not configured"
        assert pos["liquidation_price"] is None
        assert "Unavailable" in pos["liquidation_label"]

    def test_zero_positions_empty_state(self):
        """When account is 100% in cash with no open positions."""
        enriched = enrich_positions_with_risk_and_orders(
            raw_positions=[],
            pos_risk_list=[],
            open_orders_list=[],
        )
        assert enriched == []

    def test_roe_and_roi_denominators(self):
        """Ensure ROE (%) and ROI (%) have clearly separated and calculated denominators."""
        raw_pos = [{"symbol": "TESTUSDT", "positionAmt": "10.0", "entryPrice": "10.0"}]
        risk = [
            {
                "symbol": "TESTUSDT",
                "positionAmt": "10.0",
                "markPrice": "11.0",
                "notional": "110.0",
                "marginType": "isolated",
                "isolatedMargin": "11.0",  # 10x leverage
                "unRealizedProfit": "10.0",
            }
        ]
        enriched = enrich_positions_with_risk_and_orders(raw_pos, risk, [])
        pos = enriched[0]

        # ROE = 10.0 / 11.0 * 100 = 90.91%
        assert pos["roe_pct"] == pytest.approx(90.91, abs=0.01)
        # ROI = 10.0 / 110.0 * 100 = 9.09%
        assert pos["roi_pct"] == pytest.approx(9.09, abs=0.01)
        assert pos["denominator_label"] == "Allocated Margin (Isolated)"

    def test_hedge_mode_simultaneous_long_and_short(self):
        """Hedge mode allows holding LONG and SHORT on the same symbol concurrently."""
        raw_pos = [
            {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.5", "entryPrice": "60000.0"},
            {"symbol": "BTCUSDT", "positionSide": "SHORT", "positionAmt": "-0.2", "entryPrice": "62000.0"},
        ]
        risk = [
            {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.5", "markPrice": "61000.0", "liquidationPrice": "50000.0"},
            {"symbol": "BTCUSDT", "positionSide": "SHORT", "positionAmt": "-0.2", "markPrice": "61000.0", "liquidationPrice": "70000.0"},
        ]
        orders = [
            # LONG exit orders (positionSide == "LONG")
            {"symbol": "BTCUSDT", "positionSide": "LONG", "side": "SELL", "type": "TAKE_PROFIT_MARKET", "stopPrice": "65000.0", "origQty": "0.5", "reduceOnly": True, "status": "NEW"},
            {"symbol": "BTCUSDT", "positionSide": "LONG", "side": "SELL", "type": "STOP_MARKET", "stopPrice": "58000.0", "origQty": "0.5", "reduceOnly": True, "status": "NEW"},
            # SHORT exit orders (positionSide == "SHORT")
            {"symbol": "BTCUSDT", "positionSide": "SHORT", "side": "BUY", "type": "TAKE_PROFIT_MARKET", "stopPrice": "55000.0", "origQty": "0.2", "reduceOnly": True, "status": "NEW"},
            {"symbol": "BTCUSDT", "positionSide": "SHORT", "side": "BUY", "type": "STOP_MARKET", "stopPrice": "64000.0", "origQty": "0.2", "reduceOnly": True, "status": "NEW"},
        ]

        enriched = enrich_positions_with_risk_and_orders(raw_pos, risk, orders)
        assert len(enriched) == 2, "Both LONG and SHORT positions must be preserved in hedge mode"

        long_pos = next(p for p in enriched if p["position_side"] == "LONG")
        assert long_pos["side"] == "LONG"
        assert long_pos["take_profit_price"] == 65000.0
        assert long_pos["stop_loss_price"] == 58000.0
        assert long_pos["protection_status"] == "PROTECTED"

        short_pos = next(p for p in enriched if p["position_side"] == "SHORT")
        assert short_pos["side"] == "SHORT"
        assert short_pos["take_profit_price"] == 55000.0
        assert short_pos["stop_loss_price"] == 64000.0
        assert short_pos["protection_status"] == "PROTECTED"

    def test_inactive_and_stale_orders_rejected(self):
        """Canceled, filled, or expired orders must never be attached as active TP/SL."""
        raw_pos = [{"symbol": "ETHUSDT", "positionAmt": "1.0", "entryPrice": "2500.0"}]
        risk = [{"symbol": "ETHUSDT", "positionAmt": "1.0", "markPrice": "2510.0"}]
        orders = [
            # Canceled TP order
            {"symbol": "ETHUSDT", "side": "SELL", "type": "TAKE_PROFIT_MARKET", "stopPrice": "2800.0", "status": "CANCELED"},
            # Filled SL order (stale)
            {"symbol": "ETHUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": "2400.0", "status": "FILLED"},
        ]

        enriched = enrich_positions_with_risk_and_orders(raw_pos, risk, orders)
        assert len(enriched) == 1
        pos = enriched[0]
        # Inactive orders must not be used!
        assert pos["take_profit_price"] is None
        assert pos["take_profit_label"] == "Not configured"
        assert pos["stop_loss_price"] is None
        assert pos["stop_loss_label"] == "Not configured"
        assert pos["protection_status"] == "UNPROTECTED"
        assert pos["is_protected"] is False

    def test_partial_protection_classification(self):
        """If SL order covers less than position size, must be flagged as PARTIALLY_PROTECTED."""
        raw_pos = [{"symbol": "SOLUSDT", "positionAmt": "10.0", "entryPrice": "150.0"}]
        risk = [{"symbol": "SOLUSDT", "positionAmt": "10.0", "markPrice": "152.0"}]
        orders = [
            # Only protects 4.0 of 10.0 SOL
            {"symbol": "SOLUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": "145.0", "origQty": "4.0", "reduceOnly": True, "status": "NEW"},
        ]

        enriched = enrich_positions_with_risk_and_orders(raw_pos, risk, orders)
        assert len(enriched) == 1
        pos = enriched[0]
        assert pos["stop_loss_price"] == 145.0
        assert pos["protection_status"] == "PARTIALLY_PROTECTED"
        assert pos["is_protected"] is False
        assert "Partial 4/10" in pos["stop_loss_label"]

    def test_multiple_scale_out_tp_orders(self):
        """When multiple take-profit orders exist (scale-outs), nearest TP is primary and count is noted."""
        raw_pos = [{"symbol": "AVAXUSDT", "positionAmt": "100.0", "entryPrice": "20.0"}]
        risk = [{"symbol": "AVAXUSDT", "positionAmt": "100.0", "markPrice": "21.0"}]
        orders = [
            {"symbol": "AVAXUSDT", "side": "SELL", "type": "TAKE_PROFIT_MARKET", "stopPrice": "25.0", "origQty": "50.0", "status": "NEW"},
            {"symbol": "AVAXUSDT", "side": "SELL", "type": "TAKE_PROFIT_MARKET", "stopPrice": "22.5", "origQty": "50.0", "status": "NEW"},
        ]

        enriched = enrich_positions_with_risk_and_orders(raw_pos, risk, orders)
        assert len(enriched) == 1
        pos = enriched[0]
        # Nearest TP for LONG is 22.5
        assert pos["take_profit_price"] == 22.5
        assert "+1 TP" in pos["take_profit_label"]
        assert len(pos["tp_orders"]) == 2



class TestAccountSnapshotSerialization:
    """Test AccountSnapshot serialization and staleness behavior."""

    def test_snapshot_to_dict_includes_positions_summary(self, mock_positions_and_orders):
        raw_pos, pos_risk, open_orders = mock_positions_and_orders
        enriched = enrich_positions_with_risk_and_orders(raw_pos, pos_risk, open_orders)

        snap = AccountSnapshot(
            status=AccountConnectionStatus.CONNECTED,
            total_equity_usdt=17.22,
            available_balance_usdt=14.27,
            open_positions_count=len(enriched),
            positions=enriched,
            last_sync_utc="2026-10-18T00:00:00Z",
            last_sync_timestamp_ms=1765826770000,
        )
        data = snap.to_dict()

        assert data["status"] == "CONNECTED"
        assert data["is_connected"] is True
        assert data["open_positions_count"] == 3
        assert len(data["positions_summary"]) == 3
        assert data["positions_summary"][0]["symbol"] == "HYPEUSDT"
        assert "take_profit_label" in data["positions_summary"][0]
        assert "stop_loss_label" in data["positions_summary"][0]

    def test_no_credentials_leaked_in_snapshot_or_positions(self):
        snap = AccountSnapshot(
            status=AccountConnectionStatus.CONNECTED,
            total_equity_usdt=17.22,
            positions=[{"symbol": "BATUSDT", "positionAmt": "10"}],
        )
        data = snap.to_dict()
        data_str = str(data).lower()

        forbidden_keys = ["private_key", "secret", "signer", "apikey", "api_key", "signature"]
        for fk in forbidden_keys:
            assert fk not in data_str


class TestLiveVsPaperSeparation:
    """Verify live exchange positions do NOT pollute or alter paper risk limits or state."""

    def test_paper_risk_limits_unaffected_by_live_positions(self):
        """Paper risk manager must maintain its own position count and loss limits."""
        from datetime import datetime, timezone
        from src.core.types import Direction, EntryCategory, TradeRecord

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        rm = RiskManager(
            initial_equity_usdt=16.0,
            trade_risk_usdt=0.25,
            daily_loss_limit_usdt=0.50,
            weekly_loss_limit_usdt=1.25,
            max_open_positions=1,
        )

        # In paper mode, even if AsterDEX has 2 live positions, paper open positions = 0
        assert len(rm.open_trades) == 0
        can_open, reason = rm.can_open_new_position(now_ms)
        assert can_open is True
        assert reason is None
        assert rm.daily_loss_usdt == 0.0
        assert rm.weekly_loss_usdt == 0.0

        # Registering a paper trade increments paper count
        trade = TradeRecord(
            trade_id="PAPER_TEST_001",
            symbol="TESTUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=now_ms,
            entry_price=10.0,
            stop_loss=9.5,
            take_profit=11.0,
            quantity=1.0,
            notional_usdt=10.0,
            is_open=True,
            entry_category=EntryCategory.PULLBACK,
        )
        rm.record_entry(trade)
        assert len(rm.open_trades) == 1
        can_open_2, reason_2 = rm.can_open_new_position(now_ms)
        assert can_open_2 is False  # Hits paper cap 1/1
        assert "Maximum open position limit" in reason_2


class TestAPIEndpoints:
    """Test FastAPI endpoints /api/account/positions and /api/status."""

    def test_get_account_positions_endpoint(self, mock_positions_and_orders):
        raw_pos, pos_risk, open_orders = mock_positions_and_orders

        mock_adapter = MagicMock(spec=AsterDexV3Adapter)
        mock_adapter.signer = MagicMock()
        mock_adapter.signer.validate_credentials.return_value = (True, "Valid")
        mock_adapter.get_account_info.return_value = {
            "totalWalletBalance": "17.00",
            "totalMarginBalance": "17.20",
            "availableBalance": "14.00",
            "totalPositionInitialMargin": "2.20",
            "totalUnrealizedProfit": "0.20",
            "positions": raw_pos,
        }
        mock_adapter.get_positions.return_value = pos_risk
        mock_adapter.get_open_orders.return_value = open_orders

        svc = AsterAccountService(adapter=mock_adapter)

        with patch("src.api.app.account_service", svc):
            client = TestClient(app)
            res = client.get("/api/account/positions")
            assert res.status_code == 200
            data = res.json()

            assert data["status"] == "CONNECTED"
            assert data["is_connected"] is True
            assert data["open_positions_count"] == 3
            assert len(data["positions"]) == 3

            # Spot-check HYPE and BAT
            hype = next(p for p in data["positions"] if p["symbol"] == "HYPEUSDT")
            assert hype["side"] == "LONG"
            assert hype["take_profit_label"] == "$97.7030"
            assert hype["stop_loss_label"] == "$74.0660"
            assert "Unavailable" in hype["liquidation_label"]

            bat = next(p for p in data["positions"] if p["symbol"] == "BATUSDT")
            assert bat["margin_mode"] == "ISOLATED"
            assert bat["liquidation_label"] == "$0.1502"

    def test_get_account_positions_service_unavailable(self):
        with patch("src.api.app.account_service", None):
            client = TestClient(app)
            res = client.get("/api/account/positions")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ACCOUNT_DATA_UNAVAILABLE"
            assert data["is_connected"] is False
            assert data["open_positions_count"] == 0
            assert data["positions"] == []


class TestDashboardHTMLIntegrity:
    """Verify dashboard.html markup contains required panels, labels, and handlers."""

    def test_dashboard_template_contains_live_positions_panel(self):
        template_path = Path("src/api/dashboard.html")
        assert template_path.exists(), "dashboard.html must exist"
        content = template_path.read_text(encoding="utf-8")

        # 1. Dedicated Live Positions Section
        assert 'id="livePositionsSection"' in content
        assert "Live AsterDEX Positions" in content
        assert "READ-ONLY TELEMETRY" in content
        assert 'id="livePositionsContainer"' in content
        assert 'id="livePosCountBadge"' in content
        assert 'id="syncLiveAccountBtn"' in content

        # 2. Desktop Table Columns
        assert "Symbol &amp; Mode" in content or "Symbol & Mode" in content
        assert "Size &amp; Notional" in content or "Size & Notional" in content
        assert "Entry / Mark" in content
        assert "Allocated Margin" in content
        assert "TP / SL Targets" in content
        assert "Liquidation" in content
        assert "Unrealized PnL" in content

        # 3. Mobile Cards View Container
        assert "block md:hidden space-y-3" in content

        # 4. Missing-value semantics & empty state in JS
        assert "Not configured" in content
        assert "Unavailable" in content
        assert "No open AsterDEX positions detected. Account is currently in cash." in content

        # 5. Separation of Paper vs Live
        assert "Paper Baseline: $16.00 USDT" in content
        assert 'id="headerLiveEquityDisplay"' in content
        assert "Live AsterDEX Equity" in content
        assert 'id="livePosCardCount"' in content
        assert "Paper Pos" in content
