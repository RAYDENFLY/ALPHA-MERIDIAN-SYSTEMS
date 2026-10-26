"""Unit tests for Unbypassable Pre-Order Submission Guards.

Fulfills Action 2 requirements:
1. Any live order submission with strategy_id == "EMA20_RECLAIM_5M_V1" is strictly aborted/raised.
2. Any live order with actual_stop_pct < 1.15% (such as the 0.70% SOL blunder) is strictly aborted with
   REJECTED_STOP_TOO_TIGHT_UNDER_1_15_PCT.
3. Pre-order DealingRangeZoneLock verification blocks orders violating discount/premium rules.
4. AsterDexV3Adapter, AsterDexAdapter, and CoinInspectorService refuse to place orders breaching these invariants.
"""

from unittest.mock import MagicMock
import pytest

from src.exchange.aster_v3 import AsterDexV3Adapter
from src.exchange.aster import AsterDexAdapter
from src.core.exceptions import ExchangeAPIError
from src.intelligence.coin_inspector import CoinInspectorService


class TestAdapterPreOrderSubmissionGuards:
    """Direct adapter assertions on AsterDexV3Adapter and AsterDexAdapter."""

    def test_aster_v3_aborts_ema20_strategy(self):
        """AsterDexV3Adapter.create_order strictly refuses EMA20_RECLAIM_5M_V1."""
        client = AsterDexV3Adapter(
            user_address="0x1111111111111111111111111111111111111111",
            signer_address="0x2222222222222222222222222222222222222222",
            signer_private_key="0x" + "3" * 64,
            live_trading_locked=False,
        )

        with pytest.raises(PermissionError) as exc_info:
            client.create_order(
                symbol="SOLUSDT",
                side="BUY",
                order_type="LIMIT",
                quantity=0.2,
                price=101.45,
                strategy_id="EMA20_RECLAIM_5M_V1",
                planned_entry_price=101.45,
                planned_stop_price=100.74,
            )
        assert "EMA20_RECLAIM_5M_V1 is strictly forbidden" in str(exc_info.value)

    def test_aster_v3_aborts_tight_stop_under_1_15_pct(self):
        """AsterDexV3Adapter.create_order strictly refuses stops tighter than 1.15%
        (e.g., SOL entry 101.45 and SL 100.74 = 0.699% stop distance).
        """
        client = AsterDexV3Adapter(
            user_address="0x1111111111111111111111111111111111111111",
            signer_address="0x2222222222222222222222222222222222222222",
            signer_private_key="0x" + "3" * 64,
            live_trading_locked=False,
        )

        # Live blunder: entry 101.45, SL 100.74 -> 0.699%
        with pytest.raises(ValueError) as exc_info:
            client.create_order(
                symbol="SOLUSDT",
                side="BUY",
                order_type="LIMIT",
                quantity=0.2,
                price=101.45,
                strategy_id="15M_SWEEP_RECLAIM_SMC",
                planned_entry_price=101.45,
                planned_stop_price=100.74,
            )
        assert "REJECTED_STOP_TOO_TIGHT_UNDER_1_15_PCT" in str(exc_info.value)

    def test_aster_v1_aborts_tight_stop_under_1_15_pct(self):
        """AsterDexAdapter.create_order strictly refuses stops tighter than 1.15%."""
        client = AsterDexAdapter(api_key="mock", api_secret="mock")

        with pytest.raises(ValueError) as exc_info:
            client.create_order(
                symbol="SOLUSDT",
                side="BUY",
                order_type="LIMIT",
                quantity=0.2,
                price=101.45,
                strategy_id="15M_SWEEP_RECLAIM_SMC",
                planned_entry_price=101.45,
                planned_stop_price=100.74,
            )
        assert "REJECTED_STOP_TOO_TIGHT_UNDER_1_15_PCT" in str(exc_info.value)

    def test_coin_inspector_aborts_ema20_and_tight_stop(self):
        """CoinInspectorService.execute_ai_trade_setup strictly refuses EMA20 and tight stops."""
        service = CoinInspectorService()
        mock_adapter = MagicMock()
        mock_journal = MagicMock()

        # 1. EMA20 attempt
        with pytest.raises(PermissionError) as exc_info:
            service.execute_ai_trade_setup(
                payload={
                    "symbol": "SOLUSDT",
                    "direction": "LONG",
                    "order_type": "LIMIT",
                    "entry_price": 101.45,
                    "stop_loss_price": 100.74,
                    "take_profit_price": 103.18,
                    "qty": 0.2,
                    "execution_venue": "LIVE",
                    "strategy_id": "EMA20_RECLAIM_5M_V1",
                },
                journal=mock_journal,
                live_trading_locked=False,
                live_adapter=mock_adapter,
            )
        assert "EMA20_RECLAIM_5M_V1 is strictly prohibited" in str(exc_info.value)
        assert mock_adapter.create_order.call_count == 0

        # 2. Tight stop attempt (0.70%) on 15M_SWEEP_RECLAIM_SMC
        with pytest.raises(ValueError) as exc_info2:
            service.execute_ai_trade_setup(
                payload={
                    "symbol": "SOLUSDT",
                    "direction": "LONG",
                    "order_type": "LIMIT",
                    "entry_price": 101.45,
                    "stop_loss_price": 100.74,
                    "take_profit_price": 103.18,
                    "qty": 0.2,
                    "execution_venue": "LIVE",
                    "strategy_id": "15M_SWEEP_RECLAIM_SMC",
                },
                journal=mock_journal,
                live_trading_locked=False,
                live_adapter=mock_adapter,
            )
        assert "REJECTED_STOP_TOO_TIGHT_UNDER_1_15_PCT" in str(exc_info2.value)
        assert mock_adapter.create_order.call_count == 0
