"""Versioned, structure-derived paper target policy for Model D."""
from typing import Any, Dict, Optional, Tuple

TARGET_POLICY_VERSION = "structure-range-rr2-v1"


def derive_targets(direction: str, entry: Optional[float], amd: Any) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
    inputs = {"policy_version": TARGET_POLICY_VERSION, "entry": entry, "range_high": getattr(amd, "range_high", None), "range_low": getattr(amd, "range_low", None), "risk_reward": 2.0}
    if entry is None or entry <= 0 or inputs["range_high"] is None or inputs["range_low"] is None:
        return None, None, inputs
    if direction == "LONG":
        stop = float(inputs["range_low"]); risk = entry - stop; target = entry + 2.0 * risk
    elif direction == "SHORT":
        stop = float(inputs["range_high"]); risk = stop - entry; target = entry - 2.0 * risk
    else:
        return None, None, inputs
    if risk <= 0 or target <= 0: return None, None, inputs
    inputs.update({"risk_distance": risk, "reward_distance": abs(target-entry)})
    return stop, target, inputs
