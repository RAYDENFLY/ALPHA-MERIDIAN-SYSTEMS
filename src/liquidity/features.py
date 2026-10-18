"""Liquidity Intelligence Feature Engineering for Model B.

Computes L2 order book depth imbalances, spread costs, Cumulative Volume Delta (CVD),
institutional absorption, and liquidity sweep-reclaim structural patterns.
"""

from typing import Any, Dict, List, Optional, Tuple
import math

from src.core.types import Candle, Direction
from src.exchange.normalized import (
    LiquiditySnapshot,
    NormalizedOrderBook,
    NormalizedTrade,
)


def compute_order_book_features(ob: Optional[NormalizedOrderBook]) -> Dict[str, float]:
    """Compute depth, spread, and multi-tier imbalance metrics from a Level-2 book."""
    if ob is None or not ob.bids or not ob.asks:
        return {
            "spread_bps": 999.0,
            "depth_10bps_usdt": 0.0,
            "depth_25bps_usdt": 0.0,
            "depth_50bps_usdt": 0.0,
            "depth_100bps_usdt": 0.0,
            "imbalance_10bps": 0.0,
            "imbalance_25bps": 0.0,
            "imbalance_50bps": 0.0,
            "imbalance_100bps": 0.0,
        }

    b10, a10 = ob.depth_at_bps(10.0)
    b25, a25 = ob.depth_at_bps(25.0)
    b50, a50 = ob.depth_at_bps(50.0)
    b100, a100 = ob.depth_at_bps(100.0)

    imb_10 = (b10 - a10) / (b10 + a10) if (b10 + a10) > 0 else 0.0
    imb_25 = (b25 - a25) / (b25 + a25) if (b25 + a25) > 0 else 0.0
    imb_50 = (b50 - a50) / (b50 + a50) if (b50 + a50) > 0 else 0.0
    imb_100 = (b100 - a100) / (b100 + a100) if (b100 + a100) > 0 else 0.0

    return {
        "spread_bps": ob.spread_bps,
        "depth_10bps_usdt": b10 + a10,
        "depth_25bps_usdt": b25 + a25,
        "depth_50bps_usdt": b50 + a50,
        "depth_100bps_usdt": b100 + a100,
        "imbalance_10bps": imb_10,
        "imbalance_25bps": imb_25,
        "imbalance_50bps": imb_50,
        "imbalance_100bps": imb_100,
    }


def compute_trade_flow_features(
    trades: List[NormalizedTrade],
    large_trade_threshold_usdt: float = 10000.0,
) -> Dict[str, Any]:
    """Compute Cumulative Volume Delta (CVD), aggressive flow ratio, and large prints."""
    if not trades:
        return {
            "cvd_1h": 0.0,
            "taker_buy_volume_1h": 0.0,
            "taker_sell_volume_1h": 0.0,
            "large_trade_count": 0,
            "absorption_score": 0.0,
        }

    buy_vol = sum(t.notional_usdt for t in trades if t.side == "BUY")
    sell_vol = sum(t.notional_usdt for t in trades if t.side == "SELL")
    cvd = buy_vol - sell_vol

    large_trades = sum(1 for t in trades if t.notional_usdt >= large_trade_threshold_usdt)

    # Absorption score: Ratio of high volume with minimal price displacement
    total_vol = buy_vol + sell_vol
    if len(trades) >= 2 and total_vol > 0:
        first_px = trades[0].price
        last_px = trades[-1].price
        px_change_pct = abs(last_px - first_px) / first_px if first_px > 0 else 1.0
        # High volume with small price displacement produces high absorption score
        # Scaled between 0.0 and 1.0
        absorption_score = math.tanh(total_vol / (100000.0 * (px_change_pct * 100.0 + 0.1)))
    else:
        absorption_score = 0.0

    return {
        "cvd_1h": cvd,
        "taker_buy_volume_1h": buy_vol,
        "taker_sell_volume_1h": sell_vol,
        "large_trade_count": large_trades,
        "absorption_score": max(0.0, min(1.0, absorption_score)),
    }


def detect_liquidity_sweep_reclaim(
    candle: Candle,
    prior_swing_level: float,
    direction: Direction,
    buffer_pct: float = 0.0005,
) -> Tuple[bool, bool]:
    """Detect if candle swept liquidity beyond a structural swing level and reclaimed back inside.
    
    Long Sweep-Reclaim:
      - Candle Low penetrated below prior swing low by at least buffer_pct (stop-loss hunt).
      - Candle Close settled back strictly above prior swing low (reclaim of structural level).
      
    Short Sweep-Reclaim:
      - Candle High penetrated above prior swing high by at least buffer_pct.
      - Candle Close settled back strictly below prior swing high.
    """
    if prior_swing_level <= 0:
        return False, False

    if direction == Direction.LONG:
        # Sweeping lows
        penetration_target = prior_swing_level * (1.0 - buffer_pct)
        swept = candle.low < penetration_target
        reclaimed = swept and (candle.close > prior_swing_level)
        return swept, reclaimed
    else:
        # Sweeping highs
        penetration_target = prior_swing_level * (1.0 + buffer_pct)
        swept = candle.high > penetration_target
        reclaimed = swept and (candle.close < prior_swing_level)
        return swept, reclaimed


class LiquidityFeatureExtractor:
    """Combines order book, trade flow, and price action into a unified LiquiditySnapshot."""

    @staticmethod
    def build_snapshot(
        symbol: str,
        exchange_name: str,
        timestamp_ms: int,
        order_book: Optional[NormalizedOrderBook],
        trades: List[NormalizedTrade],
        sweep_detected: bool = False,
        sweep_direction: Optional[str] = None,
        sweep_reclaimed: bool = False,
    ) -> LiquiditySnapshot:
        ob_feats = compute_order_book_features(order_book)
        flow_feats = compute_trade_flow_features(trades)

        return LiquiditySnapshot(
            symbol=symbol,
            exchange_name=exchange_name,
            timestamp_ms=timestamp_ms,
            spread_bps=ob_feats["spread_bps"],
            depth_10bps_usdt=ob_feats["depth_10bps_usdt"],
            depth_25bps_usdt=ob_feats["depth_25bps_usdt"],
            depth_50bps_usdt=ob_feats["depth_50bps_usdt"],
            depth_100bps_usdt=ob_feats["depth_100bps_usdt"],
            imbalance_10bps=ob_feats["imbalance_10bps"],
            imbalance_25bps=ob_feats["imbalance_25bps"],
            imbalance_50bps=ob_feats["imbalance_50bps"],
            imbalance_100bps=ob_feats["imbalance_100bps"],
            cvd_1h=flow_feats["cvd_1h"],
            taker_buy_volume_1h=flow_feats["taker_buy_volume_1h"],
            taker_sell_volume_1h=flow_feats["taker_sell_volume_1h"],
            large_trade_count=flow_feats["large_trade_count"],
            absorption_score=flow_feats["absorption_score"],
            sweep_detected=sweep_detected,
            sweep_direction=sweep_direction,
            sweep_reclaimed=sweep_reclaimed,
        )
