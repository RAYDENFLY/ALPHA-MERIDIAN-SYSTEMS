"""Unit tests for LiquidationHeatmapEngine."""

import pytest
from src.core.types import Candle
from src.intelligence.liquidation_heatmap import LiquidationHeatmapEngine


def make_candle(idx: int, open_: float, high: float, low: float, close: float) -> Candle:
    ts = 1700000000000 + idx * 3600000
    return Candle(
        symbol="BTCUSDT",
        timeframe="1h",
        timestamp_ms=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        close_time_ms=ts + 3599999,
        quote_volume=100.0 * close,
    )


class TestLiquidationHeatmapEngine:
    """Test suite for Long vs Short liquidation concentration modeling."""

    def test_heatmap_generation_basic(self):
        mark_price = 80000.0
        oi_contracts = 100.0  # $8M OI

        heatmap = LiquidationHeatmapEngine.generate_heatmap(
            symbol="BTCUSDT",
            mark_price=mark_price,
            open_interest_contracts=oi_contracts,
            funding_rate=0.0001,
        )

        assert heatmap.symbol == "BTCUSDT"
        assert heatmap.mark_price == mark_price
        assert heatmap.open_interest_usdt == 8_000_000.0
        assert heatmap.is_exchange_reported is False
        assert "Probabilistic estimation" in heatmap.methodology

        # Verify Long and Short clusters exist
        assert len(heatmap.long_clusters) == 5
        assert len(heatmap.short_clusters) == 5

        # All long clusters must be BELOW mark price
        for lc in heatmap.long_clusters:
            assert lc.price_level < mark_price
            assert lc.distance_pct > 0.0
            assert lc.side == "LONG_LIQUIDATION"

        # All short clusters must be ABOVE mark price
        for sc in heatmap.short_clusters:
            assert sc.price_level > mark_price
            assert sc.distance_pct > 0.0
            assert sc.side == "SHORT_LIQUIDATION"

    def test_funding_rate_skew_shifts_risk_balance(self):
        # Heavily positive funding rate (longs dominate)
        hm_bullish = LiquidationHeatmapEngine.generate_heatmap(
            symbol="BTCUSDT",
            mark_price=80000.0,
            open_interest_contracts=50.0,
            funding_rate=0.0008,  # High positive funding
        )
        assert hm_bullish.long_liquidation_volume_usdt > hm_bullish.short_liquidation_volume_usdt
        assert hm_bullish.predominant_risk_side == "LONG_CASCADE_RISK"

        # Heavily negative funding rate (shorts dominate)
        hm_bearish = LiquidationHeatmapEngine.generate_heatmap(
            symbol="BTCUSDT",
            mark_price=80000.0,
            open_interest_contracts=50.0,
            funding_rate=-0.0008, # High negative funding
        )
        assert hm_bearish.short_liquidation_volume_usdt > hm_bearish.long_liquidation_volume_usdt
        assert hm_bearish.predominant_risk_side == "SHORT_SQUEEZE_RISK"

    def test_intensity_normalization_and_serialization(self):
        heatmap = LiquidationHeatmapEngine.generate_heatmap(
            symbol="ETHUSDT",
            mark_price=3000.0,
            open_interest_contracts=500.0,
            funding_rate=0.0001,
        )

        all_clusters = heatmap.long_clusters + heatmap.short_clusters
        max_intensity = max(c.relative_intensity_pct for c in all_clusters)
        assert max_intensity == 100.0
        assert all(0.0 <= c.relative_intensity_pct <= 100.0 for c in all_clusters)

        # Check serialization
        d = heatmap.to_dict()
        assert d["symbol"] == "ETHUSDT"
        assert d["is_exchange_reported"] is False
        assert isinstance(d["long_clusters"], list)
        assert isinstance(d["short_clusters"], list)
        assert len(d["long_clusters"]) == 5
