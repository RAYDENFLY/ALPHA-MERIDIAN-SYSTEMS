"""Leakage-Safe Feature Extraction Pipeline for Setup Evaluation.

Extracts quantitative features available strictly on or before the candidate timestamp.
"""

import re
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from src.core.types import Candle, Direction, SetupCandidate


FEATURE_NAMES = [
    "direction_code",
    "bias_4h_code",
    "structure_1h_code",
    "gross_rr",
    "net_rr",
    "sl_distance_pct",
    "tp_distance_pct",
    "atr_ratio",
    "realized_vol_20",
    "candle_body_ratio",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "relative_volume_20",
    "has_recent_sweep",
    "has_recent_choch",
    "has_recent_bos",
    "has_unmitigated_ob",
    "has_unmitigated_fvg",
    "sr_dist_support_pct",
    "sr_dist_resistance_pct",
    "sr_nearest_strength",
    "recent_funding_rate",
    "dealing_range_pos_72h",
    "extension_48h",
    "impulse_12h",
    "macro_trend_168h",
    "variance_ratio_24_12",
]


class FeaturePipeline:
    """Extracts leakage-free numerical feature vectors for setup evaluation."""

    @staticmethod
    def _compute_dynamic_sr_metrics(
        candidate: SetupCandidate,
        candles_1h: List[Candle],
        atr: float,
    ) -> Tuple[float, float, float]:
        """Calculate actual percentage distances to nearest support/resistance and zone strength.

        Strictly uses historical bars up to the candidate evaluation index (zero lookahead).
        """
        entry = candidate.entry_price
        if entry <= 0 or not candles_1h:
            return 0.02, 0.02, 1.0

        raw_supp = getattr(candidate, "nearest_support", None)
        raw_res = getattr(candidate, "nearest_resistance", None)

        supp_price: Optional[float] = None
        res_price: Optional[float] = None
        strength_val: Optional[float] = None

        if raw_supp is not None:
            if isinstance(raw_supp, (int, float)):
                supp_price = float(raw_supp)
            else:
                supp_price = float(
                    getattr(
                        raw_supp,
                        "price",
                        getattr(raw_supp, "level", getattr(raw_supp, "midpoint", 0.0)),
                    )
                )
                if hasattr(raw_supp, "strength"):
                    strength_val = float(getattr(raw_supp, "strength"))
                elif hasattr(raw_supp, "touches"):
                    strength_val = float(getattr(raw_supp, "touches"))

        if raw_res is not None:
            if isinstance(raw_res, (int, float)):
                res_price = float(raw_res)
            else:
                res_price = float(
                    getattr(
                        raw_res,
                        "price",
                        getattr(raw_res, "level", getattr(raw_res, "midpoint", 0.0)),
                    )
                )
                if hasattr(raw_res, "strength") and strength_val is None:
                    strength_val = float(getattr(raw_res, "strength"))
                elif hasattr(raw_res, "touches") and strength_val is None:
                    strength_val = float(getattr(raw_res, "touches"))

        if (supp_price is None or res_price is None) and getattr(
            candidate, "snr_evidence", None
        ):
            for item in candidate.snr_evidence:
                cleaned = re.sub(r"\b\d+[mhdwMHDW]\b", " ", str(item))
                upper_item = cleaned.upper()
                nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", cleaned)]
                price_candidates = [x for x in nums if 0.5 * entry <= x <= 1.5 * entry]
                if "SUPPORT" in upper_item and supp_price is None and price_candidates:
                    supp_price = price_candidates[0]
                elif "RESIST" in upper_item and res_price is None and price_candidates:
                    res_price = price_candidates[0]

                strength_match = re.search(
                    r"(?:STRENGTH|TOUCH(?:ES)?)\s*[:=]?\s*(\d+(?:\.\d+)?)", upper_item
                )
                if strength_match and strength_val is None:
                    strength_val = float(strength_match.group(1))

        lookback = candles_1h[-min(len(candles_1h), 72) :]
        tol = max(0.5 * atr, 0.002 * entry)

        pivot_lows: List[float] = []
        pivot_highs: List[float] = []

        for i in range(2, len(lookback) - 2):
            lo = lookback[i].low
            hi = lookback[i].high
            if (
                lo <= lookback[i - 1].low
                and lo <= lookback[i - 2].low
                and lo <= lookback[i + 1].low
                and lo <= lookback[i + 2].low
            ):
                pivot_lows.append(lo)
            if (
                hi >= lookback[i - 1].high
                and hi >= lookback[i - 2].high
                and hi >= lookback[i + 1].high
                and hi >= lookback[i + 2].high
            ):
                pivot_highs.append(hi)

        if supp_price is None or supp_price <= 0:
            below_pivots = [p for p in pivot_lows if p <= entry]
            if below_pivots:
                supp_price = max(below_pivots)
            else:
                lows_below = [b.low for b in lookback if b.low <= entry]
                supp_price = (
                    max(lows_below) if lows_below else min(b.low for b in lookback)
                )

        if res_price is None or res_price <= 0:
            above_pivots = [p for p in pivot_highs if p >= entry]
            if above_pivots:
                res_price = min(above_pivots)
            else:
                highs_above = [b.high for b in lookback if b.high >= entry]
                res_price = (
                    min(highs_above) if highs_above else max(b.high for b in lookback)
                )

        dist_supp = abs(entry - supp_price) / entry
        dist_res = abs(res_price - entry) / entry

        nearest_level = supp_price if dist_supp <= dist_res else res_price
        if strength_val is None or strength_val <= 0:
            touches = 0
            vol_sum = 0.0
            avg_vol = float(np.mean([b.volume for b in lookback])) if lookback else 1.0
            for b in lookback:
                if (b.low - tol) <= nearest_level <= (b.high + tol):
                    touches += 1
                    vol_sum += b.volume / max(avg_vol, 1e-8)
            strength_val = float(touches) + 0.25 * (vol_sum / max(touches, 1))

        return float(dist_supp), float(dist_res), float(strength_val)

    @staticmethod
    def extract_features(
        candidate: SetupCandidate,
        candles_1h: List[Candle],
        funding_rate: float = 0.0,
    ) -> Dict[str, float]:
        """Compute feature dictionary for a single candidate."""
        c = candles_1h[-1]
        entry = candidate.entry_price
        sl = candidate.stop_loss
        tp = candidate.take_profit_1

        # 1. Structural features
        dir_code = 1.0 if candidate.direction == Direction.LONG else -1.0
        bias_code = (
            1.0
            if candidate.bias_4h.value == "BULLISH"
            else (-1.0 if candidate.bias_4h.value == "BEARISH" else 0.0)
        )
        struct_code = (
            1.0
            if candidate.structure_1h.value == "BULLISH"
            else (-1.0 if candidate.structure_1h.value == "BEARISH" else 0.0)
        )

        # 2. Risk/Reward features
        sl_dist = abs(entry - sl) / entry if entry > 0 else 0.0
        tp_dist = abs(tp - entry) / entry if entry > 0 else 0.0

        # 3. Volatility and candle metrics
        rng = max(c.high - c.low, 1e-8)
        body = abs(c.close - c.open) / rng
        upper_wick = (c.high - max(c.open, c.close)) / rng
        lower_wick = (min(c.open, c.close) - c.low) / rng

        returns = []
        for i in range(max(1, len(candles_1h) - 20), len(candles_1h)):
            prev = candles_1h[i - 1].close
            curr = candles_1h[i].close
            returns.append((curr - prev) / prev if prev > 0 else 0.0)
        realized_vol = float(np.std(returns)) if len(returns) > 1 else 0.01

        recent_15 = candles_1h[-15:]
        tr_list = [
            max(
                recent_15[idx].high - recent_15[idx].low,
                abs(recent_15[idx].high - recent_15[idx - 1].close),
                abs(recent_15[idx].low - recent_15[idx - 1].close),
            )
            for idx in range(1, len(recent_15))
        ]
        atr = float(np.mean(tr_list)) if tr_list else (0.01 * entry)
        atr_ratio = atr / entry if entry > 0 else 0.01

        # Volume
        vols = [x.volume for x in candles_1h[-20:]]
        avg_vol = float(np.mean(vols)) if vols else c.volume
        rvol = (c.volume / avg_vol) if avg_vol > 0 else 1.0

        # Evidence flags from candidate evidence
        ev_text = " ".join(candidate.smc_evidence).upper()
        has_sweep = 1.0 if "SWEEP" in ev_text else 0.0
        has_choch = 1.0 if "CHOCH" in ev_text else 0.0
        has_bos = 1.0 if "BOS" in ev_text else 0.0
        has_ob = 1.0 if "ORDER BLOCK" in ev_text else 0.0
        has_fvg = 1.0 if "FAIR VALUE GAP" in ev_text else 0.0

        # Dynamic S/R proximity and zone strength
        dist_supp, dist_res, strength = FeaturePipeline._compute_dynamic_sr_metrics(
            candidate=candidate,
            candles_1h=candles_1h,
            atr=atr,
        )

        # SMC Dealing Range (72h), Multi-Horizon Exhaustion, and Variance Ratio Regime features
        w72 = candles_1h[-min(len(candles_1h), 72) :]
        hi72 = max(b.high for b in w72)
        lo72 = min(b.low for b in w72)
        pos_72h = (c.close - lo72) / max(hi72 - lo72, 1e-8)

        c_48 = candles_1h[-min(len(candles_1h), 49)].close
        c_12 = candles_1h[-min(len(candles_1h), 13)].close
        c_168 = candles_1h[-min(len(candles_1h), 170)].close
        ext_48h = (c.close - c_48) / c_48 if c_48 > 0 else 0.0
        imp_12h = (c.close - c_12) / c_12 if c_12 > 0 else 0.0
        macro_168h = (c.close - c_168) / c_168 if c_168 > 0 else 0.0

        w170 = candles_1h[-min(len(candles_1h), 170) :]
        if len(w170) >= 36:
            closes_arr = np.array([b.close for b in w170], dtype=float)
            r12_arr = closes_arr[12:] / closes_arr[:-12] - 1.0
            r24_arr = closes_arr[24:] / closes_arr[:-24] - 1.0
            vr_val = float(np.var(r24_arr) / (2.0 * np.var(r12_arr) + 1e-12))
        else:
            vr_val = 1.0

        return {
            "direction_code": dir_code,
            "bias_4h_code": bias_code,
            "structure_1h_code": struct_code,
            "gross_rr": candidate.gross_reward_to_risk,
            "net_rr": candidate.net_reward_to_risk,
            "sl_distance_pct": sl_dist,
            "tp_distance_pct": tp_dist,
            "atr_ratio": atr_ratio,
            "realized_vol_20": realized_vol,
            "candle_body_ratio": body,
            "upper_wick_ratio": upper_wick,
            "lower_wick_ratio": lower_wick,
            "relative_volume_20": rvol,
            "has_recent_sweep": has_sweep,
            "has_recent_choch": has_choch,
            "has_recent_bos": has_bos,
            "has_unmitigated_ob": has_ob,
            "has_unmitigated_fvg": has_fvg,
            "sr_dist_support_pct": dist_supp,
            "sr_dist_resistance_pct": dist_res,
            "sr_nearest_strength": strength,
            "recent_funding_rate": funding_rate,
            "dealing_range_pos_72h": float(pos_72h),
            "extension_48h": float(ext_48h),
            "impulse_12h": float(imp_12h),
            "macro_trend_168h": float(macro_168h),
            "variance_ratio_24_12": float(vr_val),
        }

    @classmethod
    def to_dataframe(cls, feature_dicts: List[Dict[str, float]]) -> pd.DataFrame:
        """Convert list of feature dicts to DataFrame with consistent schema."""
        df = pd.DataFrame(feature_dicts)
        for col in FEATURE_NAMES:
            if col not in df.columns:
                df[col] = 0.0
        return df[FEATURE_NAMES]