"""Model B diagnostic breaker override: state record, baseline snapshot and PAPER-only guard.

This is a TEMPORARY, paper-only research aid. It never edits the baseline risk limits; it only
records when the override was activated, what the baseline was, and refuses activation unless the
application is provably PAPER/RESEARCH with live trading locked.

State file (no DB migration, safe to delete): data/model_b_diagnostic_override.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import AppConfig, ExecutionMode, ModelBConfig
from src.core.logger import logger

DEFAULT_STATE_PATH = Path("data") / "model_b_diagnostic_override.json"
STRATEGY_VERSION_FIELD = "version"

_BASELINE_FIELDS = (
    "daily_loss_limit_usdt",
    "weekly_loss_limit_usdt",
    "target_trade_risk_usdt",
    "max_open_positions",
    "max_leverage",
    "max_margin_usdt",
    "min_risk_reward",
    "max_spread_bps",
    "min_imbalance_ratio",
    "min_absorption_score",
    "initial_equity_usdt",
    "version",
)


def baseline_snapshot(model_b: ModelBConfig, app_cfg: Optional[AppConfig] = None) -> Dict[str, Any]:
    snap = {f: getattr(model_b, f, None) for f in _BASELINE_FIELDS}
    if app_cfg is not None:
        snap["scanner_mode"] = app_cfg.mode.value
        snap["live_trading_locked"] = app_cfg.live_trading_locked
        snap["paper_slippage_rate"] = app_cfg.paper.slippage_rate
        snap["paper_taker_fee_rate"] = app_cfg.paper.taker_fee_rate
    return snap


def override_permitted(app_cfg: AppConfig) -> bool:
    """Override may only run when PAPER/RESEARCH and live trading is locked."""
    return app_cfg.mode in (ExecutionMode.PAPER, ExecutionMode.RESEARCH) and bool(app_cfg.live_trading_locked)


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None


def resolve_override_state(
    app_cfg: AppConfig,
    path: Path = DEFAULT_STATE_PATH,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Reconcile config flag with the persisted record. Mutates model_b.diagnostic_breaker_override
    to False if activation is not permitted. Returns the public diagnostic state dict."""
    mb = app_cfg.model_b
    now = now or datetime.now(timezone.utc)
    record = _read(path)

    if mb.diagnostic_breaker_override and not override_permitted(app_cfg):
        logger.error("DIAGNOSTIC_OVERRIDE_REFUSED: override requires PAPER/RESEARCH mode with live_trading_locked=True.")
        mb.diagnostic_breaker_override = False

    if mb.diagnostic_breaker_override:
        if not record or record.get("ended_at_utc") is not None:
            record = {
                "started_at_utc": now.isoformat(),
                "started_at_ms": int(now.timestamp() * 1000),
                "ended_at_utc": None,
                "baseline_config": baseline_snapshot(mb, app_cfg),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        logger.warning(
            f"DIAGNOSTIC_OVERRIDE_ACTIVE: Model B daily/weekly loss breakers are NOT enforced (paper-only). "
            f"started_at={record['started_at_utc']}"
        )
    elif record and record.get("ended_at_utc") is None:
        record["ended_at_utc"] = now.isoformat()
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        logger.info("DIAGNOSTIC_OVERRIDE_ENDED: override disabled; record retained for cohort separation.")

    return {
        "active": bool(mb.diagnostic_breaker_override),
        "started_at_utc": record.get("started_at_utc") if record else None,
        "started_at_ms": record.get("started_at_ms") if record else None,
        "ended_at_utc": record.get("ended_at_utc") if record else None,
        "baseline_config": (record or {}).get("baseline_config") or baseline_snapshot(mb, app_cfg),
    }
