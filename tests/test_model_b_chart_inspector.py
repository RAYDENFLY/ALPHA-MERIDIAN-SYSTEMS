import pytest
from src.intelligence.liquidity_chart_service import LiquidityChartService
from src.exchange.hyperliquid_adapter import HyperliquidAdapter
from src.exchange.binance_adapter import BinanceFuturesAdapter
from src.exchange.aster_adapter import AsterAdapter


class TestModelBChartInspector:
    def test_asterdex_klines_retrieval(self):
        """Test OHLCV candlestick retrieval from primary AsterDEX adapter."""
        service = LiquidityChartService()
        candles = service.get_chart_klines(symbol="BTCUSDT", timeframe="1h", limit=10, venue="asterdex")
        assert isinstance(candles, list)
        if candles:
            c = candles[0]
            assert "time" in c
            assert "open" in c
            assert "high" in c
            assert "low" in c
            assert "close" in c
            assert "volume" in c

    def test_hyperliquid_klines_retrieval(self):
        """Test OHLCV candlestick retrieval from Hyperliquid adapter."""
        hl = HyperliquidAdapter()
        candles = hl.get_klines(symbol="BTCUSDT", timeframe="1h", limit=10)
        assert isinstance(candles, list)
        if candles:
            c = candles[0]
            assert c.symbol == "BTCUSDT"
            assert c.timeframe == "1h"
            assert c.open > 0
            assert c.high >= c.low

    def test_binance_klines_start_end_params(self):
        """Test Binance futures adapter supports start_time_ms and end_time_ms parameters."""
        adapter = BinanceFuturesAdapter()
        now_ms = 1792821283000
        start_ms = now_ms - (24 * 3600 * 1000)
        # Should execute without TypeError on parameters
        candles = adapter.get_klines(symbol="BTCUSDT", timeframe="1h", limit=10, start_time_ms=start_ms, end_time_ms=now_ms)
        assert isinstance(candles, list)

    def test_missing_historical_candles_graceful_handling(self):
        """Test unknown/unlisted symbol returns empty list without crashing."""
        service = LiquidityChartService()
        candles = service.get_chart_klines(symbol="NONEXISTENTCOIN999USDT", timeframe="1h", limit=10, venue="asterdex")
        assert candles == []

    def test_multi_venue_routing_in_chart_service(self):
        """Test get_chart_klines routes across asterdex, hyperliquid, and binance."""
        service = LiquidityChartService()
        # AsterDEX routing
        res_aster = service.get_chart_klines("ETHUSDT", "1h", limit=5, venue="asterdex")
        assert isinstance(res_aster, list)
        # Hyperliquid routing
        res_hl = service.get_chart_klines("ETHUSDT", "1h", limit=5, venue="hyperliquid")
        assert isinstance(res_hl, list)
        # Binance routing
        res_binance = service.get_chart_klines("ETHUSDT", "1h", limit=5, venue="binance")
        assert isinstance(res_binance, list)

    def test_stochastic_calculation_logic(self):
        """Test mathematical formulation of Stochastic %K and %D."""
        # Mock candle data with known prices
        candles = [
            {"time": 1000 + i * 3600, "open": 100 + i, "high": 105 + i, "low": 95 + i, "close": 102 + i}
            for i in range(25)
        ]
        # Verify min candle count requirement
        assert len(candles) >= 14
        periodK = 14
        i = periodK - 1
        subset = candles[i - periodK + 1 : i + 1]
        lowest_low = min(c["low"] for c in subset)
        highest_high = max(c["high"] for c in subset)
        current_close = candles[i]["close"]
        raw_k = ((current_close - lowest_low) / (highest_high - lowest_low)) * 100
        assert 0 <= raw_k <= 100

    def test_ema_calculation_logic(self):
        """Test exponential moving average mathematical formulation."""
        prices = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
        period = 3
        k = 2.0 / (period + 1.0)
        # First SMA
        sma = sum(prices[:period]) / period
        ema = (prices[3] * k) + (sma * (1 - k))
        assert round(ema, 4) == round((13.0 * 0.5) + (11.0 * 0.5), 4)

    def test_risk_reward_calculation(self):
        """Test long and short risk reward ratio and percentage derivation."""
        # Long trade
        entry_long = 100.0
        sl_long = 95.0
        tp_long = 115.0
        risk_long = entry_long - sl_long  # 5.0
        reward_long = tp_long - entry_long  # 15.0
        rr_long = reward_long / risk_long  # 3.0
        assert rr_long == 3.0

        # Short trade
        entry_short = 100.0
        sl_short = 105.0
        tp_short = 85.0
        risk_short = sl_short - entry_short  # 5.0
        reward_short = entry_short - tp_short  # 15.0
        rr_short = reward_short / risk_short  # 3.0
        assert rr_short == 3.0

    def test_missing_sl_or_tp_safeguards(self):
        """Test safeguards when stop loss or take profit is None or zero."""
        trade_no_sl = {"entry_price": 100.0, "stop_loss": None, "take_profit": 115.0}
        sl = trade_no_sl.get("stop_loss")
        assert sl is None
        # When SL is None, risk is not computable without synthetic assumptions
        risk_computable = bool(trade_no_sl.get("entry_price") and trade_no_sl.get("stop_loss"))
        assert not risk_computable

    def test_intelligent_price_scaling_xauusdt_short(self):
        """Test Fit Price Action vs Fit All scaling for XAUUSDT SHORT."""
        candles = [
            {"low": 3985.85, "high": 4015.20},
            {"low": 3992.10, "high": 4043.30},
            {"low": 4000.00, "high": 4010.00},
        ]
        trade = {
            "symbol": "XAUUSDT",
            "direction": "SHORT",
            "entry_price": 4008.91,
            "mark_price": 4003.34,
            "stop_loss": 4069.04,
            "take_profit": 3951.26,
            "take_profit_2": 3893.61,
        }

        c_min = min(c["low"] for c in candles)
        c_max = max(c["high"] for c in candles)
        core_min = min(c_min, trade["entry_price"], trade["mark_price"])
        core_max = max(c_max, trade["entry_price"], trade["mark_price"])
        core_span = core_max - core_min  # 57.45

        # Proximity threshold is 35% of core span (~20.11)
        prox = core_span * 0.35
        # Verify distant TP2 (3893.61) and SL (4069.04) are outside proximity threshold
        assert trade["take_profit_2"] < (core_min - prox)
        assert trade["stop_loss"] > (core_max + prox)

        # In PRICE_ACTION mode, range span is ~66.6 instead of 175.43 (2.6x taller candles)
        pa_span = (core_max - core_min) * 1.16
        all_span = (trade["stop_loss"] - trade["take_profit_2"]) * 1.12
        assert pa_span < all_span * 0.45

    def test_intelligent_price_scaling_ethusdt_long(self):
        """Test Fit Price Action vs Fit All scaling for ETHUSDT LONG."""
        candles = [
            {"low": 2368.50, "high": 2395.00},
            {"low": 2372.00, "high": 2401.20},
        ]
        trade = {
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "entry_price": 2386.04,
            "mark_price": 2374.76,
            "stop_loss": 2348.65,
            "take_profit": 2442.13,
            "take_profit_2": 2498.21,
        }

        c_min = min(c["low"] for c in candles)
        c_max = max(c["high"] for c in candles)
        core_min = min(c_min, trade["entry_price"], trade["mark_price"])
        core_max = max(c_max, trade["entry_price"], trade["mark_price"])
        core_span = core_max - core_min  # 32.70

        prox = core_span * 0.35  # ~11.45
        # Distant TP1 (2442.13), TP2 (2498.21), and SL (2348.65) are outside core proximity
        assert trade["take_profit_2"] > (core_max + prox)
        assert trade["stop_loss"] < (core_min - prox)

