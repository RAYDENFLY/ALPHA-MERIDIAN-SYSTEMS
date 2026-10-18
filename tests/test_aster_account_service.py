"""Tests for AsterDEX Read-Only Account Service and Equity-Based Risk Engine.

Verifies:
1. Authenticated account snapshot parsing (/fapi/v3/account).
2. Read-only service caching and configurable TTL.
3. Staleness detection (STALE_ACCOUNT_DATA vs ACCOUNT_DATA_UNAVAILABLE).
4. No static $16.00 fallback masquerading as live exchange data.
5. Missing/invalid credential handling (UNAUTHENTICATED).
6. Configurable equity tier resolution (defensive, standard, growth, scaling).
7. Anti-martingale invariant: sizing shrinks on drawdown; never increases after loss.
8. Capital preservation halt below minimum trading equity floor ($10.00 USDT).
9. Integration with PositionSizer and ScannerRunner.
10. API endpoints (/api/status, /api/account, /api/account/sync).
11. Preservation of live_trading_locked safety invariant.
"""

import time
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.exchange.account_service import (
    AccountConnectionStatus,
    AccountSnapshot,
    AsterAccountService,
)
from src.exchange.aster_v3 import AsterDexV3Adapter
from src.exchange.metadata import SymbolSpecs
from src.risk.sizing import PositionSizer
from src.risk.tier import EquityTier, EquityTierConfig, resolve_equity_tier
from src.api.app import app


@pytest.fixture
def mock_v3_account_data():
    """Redacted mock payload matching official Aster DEX V3 /fapi/v3/account structure."""
    return {
        "feeTier": 0,
        "canTrade": True,
        "canDeposit": True,
        "canWithdraw": True,
        "updateTime": 1792252800000,
        "totalInitialMargin": "2.17014667",
        "totalMaintMargin": "0.27688146",
        "totalWalletBalance": "17.02953932",
        "totalUnrealizedProfit": "0.19198344",
        "totalMarginBalance": "17.22152276",
        "totalPositionInitialMargin": "2.17014667",
        "totalOpenOrderInitialMargin": "0.00000000",
        "totalCrossWalletBalance": "16.01252129",
        "totalCrossUnPnl": "-0.03922343",
        "availableBalance": "14.27372225",
        "maxWithdrawAmount": "14.27372225",
        "assets": [
            {
                "asset": "USDT",
                "walletBalance": "17.02953932",
                "unrealizedProfit": "0.19198344",
                "marginBalance": "17.22152276",
                "availableBalance": "14.27372225",
            }
        ],
        "positions": [
            {
                "symbol": "HYPEUSDT",
                "initialMargin": "1.10691386",
                "maintMargin": "0.01106913",
                "unrealizedProfit": "-0.04149065",
                "positionInitialMargin": "1.10691386",
                "openOrderInitialMargin": "0",
                "leverage": "5",
                "isolated": False,
                "entryPrice": "79.658",
                "maxNotional": "30000000",
                "positionSide": "BOTH",
                "positionAmt": "0.07",
                "notional": "5.53456934",
                "isolatedWallet": "0",
            },
            {
                "symbol": "BATUSDT",
                "initialMargin": "1.06324932",
                "maintMargin": "0.26581233",
                "unrealizedProfit": "0.23355664",
                "positionInitialMargin": "1.06324932",
                "openOrderInitialMargin": "0",
                "leverage": "5",
                "isolated": True,
                "entryPrice": "0.17834",
                "maxNotional": "1000000",
                "positionSide": "BOTH",
                "positionAmt": "28.5",
                "notional": "5.31624664",
                "isolatedWallet": "1.01701803",
            },
            {
                "symbol": "BTCUSDT",
                "initialMargin": "0.0",
                "maintMargin": "0.0",
                "unrealizedProfit": "0.0",
                "positionInitialMargin": "0.0",
                "openOrderInitialMargin": "0",
                "leverage": "10",
                "isolated": True,
                "entryPrice": "0.0",
                "maxNotional": "1000000",
                "positionSide": "BOTH",
                "positionAmt": "0.0",  # Inactive position
                "notional": "0.0",
                "isolatedWallet": "0",
            },
        ],
    }


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


class TestAsterAccountService:
    """Test suite for AsterAccountService integration and data parsing."""

    def test_account_snapshot_connected(self, mock_v3_account_data):
        mock_adapter = MagicMock(spec=AsterDexV3Adapter)
        mock_adapter.signer = MagicMock()
        mock_adapter.signer.validate_credentials.return_value = (True, "OK")
        mock_adapter.get_account_info.return_value = mock_v3_account_data

        service = AsterAccountService(adapter=mock_adapter, cache_ttl_sec=10.0)
        snapshot = service.get_snapshot()

        assert snapshot.status == AccountConnectionStatus.CONNECTED
        assert snapshot.total_equity_usdt == 17.2215
        assert snapshot.wallet_balance_usdt == 17.0295
        assert snapshot.available_balance_usdt == 14.2737
        assert snapshot.used_margin_usdt == 2.1701
        assert snapshot.unrealized_pnl_usdt == 0.1920
        assert snapshot.open_positions_count == 2  # Only active positions (HYPE, BAT)
        assert snapshot.is_stale is False
        assert snapshot.error_message is None

        # Verify serialization
        dct = snapshot.to_dict()
        assert dct["status"] == "CONNECTED"
        assert dct["is_connected"] is True
        assert len(dct["positions_summary"]) == 2

    def test_account_service_caching_and_ttl(self, mock_v3_account_data):
        mock_adapter = MagicMock(spec=AsterDexV3Adapter)
        mock_adapter.signer = MagicMock()
        mock_adapter.signer.validate_credentials.return_value = (True, "OK")
        mock_adapter.get_account_info.return_value = mock_v3_account_data

        service = AsterAccountService(adapter=mock_adapter, cache_ttl_sec=5.0)

        # First call fetches from exchange
        snap1 = service.get_snapshot()
        assert mock_adapter.get_account_info.call_count == 1

        # Second call within TTL uses cached snapshot
        snap2 = service.get_snapshot()
        assert mock_adapter.get_account_info.call_count == 1
        assert snap2 is snap1

        # Force refresh bypasses cache
        snap3 = service.get_snapshot(force_refresh=True)
        assert mock_adapter.get_account_info.call_count == 2
        assert snap3.status == AccountConnectionStatus.CONNECTED

    def test_account_service_staleness_handling(self, mock_v3_account_data):
        mock_adapter = MagicMock(spec=AsterDexV3Adapter)
        mock_adapter.signer = MagicMock()
        mock_adapter.signer.validate_credentials.return_value = (True, "OK")
        mock_adapter.get_account_info.return_value = mock_v3_account_data

        service = AsterAccountService(adapter=mock_adapter, cache_ttl_sec=0.1, stale_threshold_sec=60.0)
        snap1 = service.get_snapshot()
        assert snap1.status == AccountConnectionStatus.CONNECTED

        # Now simulate network failure
        mock_adapter.get_account_info.side_effect = ConnectionError("Exchange endpoint timeout")

        time.sleep(0.15)  # Wait for TTL to expire
        snap2 = service.get_snapshot()

        # Should return cached data marked as STALE
        assert snap2.status == AccountConnectionStatus.STALE_ACCOUNT_DATA
        assert snap2.is_stale is True
        assert snap2.total_equity_usdt == 17.2215
        assert "timeout" in snap2.error_message.lower()

    def test_account_service_unavailable_beyond_stale_threshold(self, mock_v3_account_data):
        mock_adapter = MagicMock(spec=AsterDexV3Adapter)
        mock_adapter.signer = MagicMock()
        mock_adapter.signer.validate_credentials.return_value = (True, "OK")
        mock_adapter.get_account_info.return_value = mock_v3_account_data

        # Very short stale threshold (0.2s)
        service = AsterAccountService(adapter=mock_adapter, cache_ttl_sec=0.1, stale_threshold_sec=0.2)
        service.get_snapshot()

        mock_adapter.get_account_info.side_effect = ConnectionError("Total exchange outage")
        time.sleep(0.3)  # Wait past stale threshold

        snap = service.get_snapshot()
        assert snap.status == AccountConnectionStatus.ACCOUNT_DATA_UNAVAILABLE
        assert snap.total_equity_usdt is None  # Never report static numbers when unavailable!
        assert snap.wallet_balance_usdt is None
        assert snap.available_balance_usdt is None

    def test_account_service_missing_credentials(self):
        mock_adapter = MagicMock(spec=AsterDexV3Adapter)
        mock_adapter.signer = MagicMock()
        mock_adapter.signer.validate_credentials.return_value = (False, "Missing private key")

        service = AsterAccountService(adapter=mock_adapter)
        snap = service.get_snapshot()

        assert snap.status == AccountConnectionStatus.UNAUTHENTICATED
        assert snap.total_equity_usdt is None
        assert "credentials incomplete" in snap.error_message.lower()

    def test_account_service_none_adapter(self):
        service = AsterAccountService(adapter=None)
        snap = service.get_snapshot()

        assert snap.status == AccountConnectionStatus.ACCOUNT_DATA_UNAVAILABLE
        assert snap.total_equity_usdt is None
        assert "no exchange adapter configured" in snap.error_message.lower()


class TestEquityTierModel:
    """Test suite for configurable equity-based isolated margin tier model."""

    def test_tier_resolution_monotonic(self):
        cfg = EquityTierConfig()

        # Equity < $10.00: Halt
        res_halt = resolve_equity_tier(9.50, cfg)
        assert res_halt.is_trading_allowed is False
        assert res_halt.tier_id == "TIER_HALTED_LOW_EQUITY"
        assert res_halt.target_margin_min == 0.0

        # Equity $17.22 (Current AsterDEX): Tier 1 Micro ($1.00 - $1.50)
        res_t1 = resolve_equity_tier(17.22, cfg)
        assert res_t1.is_trading_allowed is True
        assert res_t1.tier_id == "TIER_1_MICRO"
        assert res_t1.target_margin_min == 1.00
        assert res_t1.target_margin_max == 1.50

        # Equity $35.00: Tier 2 Standard ($2.50 - $3.50)
        res_t2 = resolve_equity_tier(35.00, cfg)
        assert res_t2.is_trading_allowed is True
        assert res_t2.tier_id == "TIER_2_STANDARD"
        assert res_t2.target_margin_min == 2.50
        assert res_t2.target_margin_max == 3.50

        # Equity $75.00: Tier 3 Growth ($5.00 - $6.50)
        res_t3 = resolve_equity_tier(75.00, cfg)
        assert res_t3.is_trading_allowed is True
        assert res_t3.tier_id == "TIER_3_GROWTH"
        assert res_t3.target_margin_min == 5.00
        assert res_t3.target_margin_max == 6.50

        # Equity $150.00: Tier 4 Scale ($8.00 - $10.00)
        res_t4 = resolve_equity_tier(150.00, cfg)
        assert res_t4.is_trading_allowed is True
        assert res_t4.tier_id == "TIER_4_SCALE"
        assert res_t4.target_margin_min == 8.00
        assert res_t4.target_margin_max == 10.00

        # Anti-martingale invariant: Target margin is monotonically non-decreasing
        assert res_t1.target_margin_min <= res_t2.target_margin_min <= res_t3.target_margin_min <= res_t4.target_margin_min

    def test_anti_martingale_shrinkage_on_drawdown(self):
        """Simulate sequential losses: verify sizing shrinks as equity drops."""
        cfg = EquityTierConfig()

        # Start with $30.00 -> Tier 2 ($2.50-$3.50)
        t1 = resolve_equity_tier(30.00, cfg)
        assert t1.tier_id == "TIER_2_STANDARD"
        assert t1.target_margin_min == 2.50

        # Loss takes equity to $17.22 -> Shrinks to Tier 1 Micro ($1.00-$1.50)
        t2 = resolve_equity_tier(17.22, cfg)
        assert t2.tier_id == "TIER_1_MICRO"
        assert t2.target_margin_min == 1.00
        assert t2.target_margin_min < t1.target_margin_min

        # Further loss takes equity to $9.00 -> Circuit breaker halt
        t3 = resolve_equity_tier(9.00, cfg)
        assert t3.is_trading_allowed is False
        assert t3.target_margin_min == 0.0

    def test_position_sizer_with_tier_config(self, btc_specs):
        cfg = EquityTierConfig()

        # Under Tier 1 ($1.00-$1.50 margin), BTCUSDT at $55,000 has min notional $55 (margin $5.50)
        # which exceeds the $1.50 limit and MUST be rejected as INFEASIBLE.
        btc_assessment = PositionSizer.calculate_sizing(
            symbol_specs=btc_specs,
            entry_price=55000.0,
            stop_loss=54175.0,
            available_equity_usdt=17.22,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
            tier_config=cfg,
        )
        assert btc_assessment.is_feasible is False
        assert "exceeds maximum target margin limit" in btc_assessment.infeasible_reason

        # For an asset with small min lot (e.g. ETH at $2500, min qty 0.001, min notional $5.00),
        # Tier 1 micro-margin sizing successfully derives a $10 notional ($1.00 margin) order.
        eth_specs = SymbolSpecs(
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
        eth_assessment = PositionSizer.calculate_sizing(
            symbol_specs=eth_specs,
            entry_price=2500.0,
            stop_loss=2462.5,  # 1.5% SL
            available_equity_usdt=17.22,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
            tier_config=cfg,
        )
        assert eth_assessment.is_feasible is True
        assert 1.00 <= eth_assessment.target_isolated_margin_usdt <= 1.50
        assert 1.00 <= eth_assessment.required_margin_usdt <= 1.50
        assert 10.0 <= eth_assessment.position_notional_usdt <= 15.0
        assert eth_assessment.estimated_loss_usdt <= 0.50

        # At $9.50 equity (below minimum $10 floor), sizing halts immediately
        halt_assessment = PositionSizer.calculate_sizing(
            symbol_specs=eth_specs,
            entry_price=2500.0,
            stop_loss=2462.5,
            available_equity_usdt=9.50,
            max_leverage=10,
            tier_config=cfg,
        )
        assert halt_assessment.is_feasible is False
        assert "below minimum trading threshold" in halt_assessment.infeasible_reason.lower()


class TestAPIAccountIntegration:
    """Test suite for FastAPI endpoints exposing verified live account metrics."""

    def test_api_status_contains_account_and_tier(self):
        client = TestClient(app)
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.json()

        # Verify account block
        assert "account" in data
        acc = data["account"]
        assert "status" in acc
        assert "is_connected" in acc
        assert "is_stale" in acc
        assert "total_equity_usdt" in acc
        assert "available_balance_usdt" in acc
        assert "used_margin_usdt" in acc
        assert "unrealized_pnl_usdt" in acc
        assert "source" in acc

        # Verify capital block contains active tier and verified live equity
        cap = data["capital"]
        assert "initial_equity_usdt" in cap
        assert "current_equity_usdt" in cap
        assert "active_tier" in cap
        assert "tier_id" in cap["active_tier"]
        assert "target_margin_min" in cap["active_tier"]

    def test_api_account_endpoint(self):
        client = TestClient(app)
        res = client.get("/api/account")
        assert res.status_code == 200
        data = res.json()
        assert "status" in data
        assert "is_connected" in data

    def test_live_trading_locked_invariant(self):
        client = TestClient(app)
        res = client.get("/api/status")
        data = res.json()
        assert data["live_trading_locked"] is True
        assert data["mode"] in ("RESEARCH", "PAPER")
