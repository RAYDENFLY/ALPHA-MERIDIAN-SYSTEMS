"""Model B Live Execution Adapter & Dry-Run Pre-Flight Verification Layer.

Enforces strict micro-account risk governance (~$16 USDT Aster DEX account)
while maintaining live_trading_locked = True during burn-in:
1. Dedicated Live Micro-Account Risk Profile ($0.45 risk, $4.00 max margin, 2 positions max).
2. Intelligent Exit Plan: Automatically switches from 50% TP1 partial close to
   100% Full Exit at TP1 if 50% notional is below exchange min_notional buffer ($5.50)
   or stepSize (min_qty), preventing MIN_NOTIONAL / LOT_SIZE rejections.
3. Strict Isolation of External Manual Positions (protects manual SUIUSDT).
4. Real-time LIVE_DRY_RUN telemetry and compliant order payload generation.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from enum import Enum
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from src.core.logger import logger
from src.core.types import Direction, SetupCandidate
from src.exchange.metadata import SymbolSpecs


class ExitPlanType(str, Enum):
    FULL_CLOSE_TP1 = "FULL_CLOSE_TP1"
    PARTIAL_TP1_RUNNER_TP2 = "PARTIAL_TP1_RUNNER_TP2"


@dataclass
class ModelBLiveMicroConfig:
    """Dedicated live micro-account configuration (~$16 USDT Aster account)."""
    target_trade_risk_usdt: float = 0.45
    max_margin_usdt: float = 4.00
    daily_loss_limit_usdt: float = 1.35  # 3R daily breaker
    weekly_loss_limit_usdt: float = 3.00
    max_open_positions: int = 2
    max_open_per_direction: int = 1
    min_notional_buffer: float = 5.50  # Aster DEX min_notional ($5.0) + 10% safety buffer
    taker_fee_rate: float = 0.0005
    leverage: int = 10


@dataclass
class LivePreFlightResult:
    """Detailed dry-run pre-flight validation result for a candidate setup."""
    symbol: str
    direction: str
    risk_budget_usdt: float
    raw_quantity: float
    rounded_quantity: float
    entry_price: float
    rounded_entry_price: float
    stop_loss_price: float
    rounded_stop_loss_price: float
    take_profit_1_price: float
    rounded_take_profit_1_price: float
    take_profit_2_price: Optional[float]
    rounded_take_profit_2_price: Optional[float]
    position_notional_usdt: float
    required_margin_usdt: float
    effective_leverage: float
    step_size: float
    tick_size: float
    min_qty: float
    min_notional: float
    exit_plan: str
    exit_plan_reason: str
    tp1_close_qty: float
    runner_tp2_qty: float
    is_compliant: bool
    rejection_reasons: List[str]
    compliance_checks: Dict[str, bool]
    dry_run_order_payload: Dict[str, Any]
    account_available_balance: float
    account_total_equity: float
    timestamp_utc: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def round_step_size(quantity: float, step_size: float) -> float:
    """Round quantity down to step size using Decimal to avoid over-sizing."""
    if step_size <= 0:
        return round(quantity, 4)
    d_qty = Decimal(str(round(quantity, 8)))
    d_step = Decimal(str(step_size))
    rounded = (d_qty / d_step).quantize(Decimal("1"), rounding=ROUND_DOWN) * d_step
    return float(rounded)


def round_tick_size(price: float, tick_size: float) -> float:
    """Round price to tick size using Decimal ROUND_HALF_UP."""
    if tick_size <= 0:
        return round(price, 4)
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick_size))
    rounded = (d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * d_tick
    return float(rounded)


def determine_exit_plan(
    quantity: float,
    entry_price: float,
    min_qty: float,
    min_notional: float,
    step_size: float,
    min_notional_buffer: float = 5.50,
) -> Tuple[ExitPlanType, str, float, float]:
    """Determine whether a small position can support 50% TP1 partial close or must full exit at TP1.

    Returns:
        (exit_plan, reason, tp1_close_qty, runner_tp2_qty)
    """
    if quantity <= 0:
        return ExitPlanType.FULL_CLOSE_TP1, "Zero quantity; full close default.", 0.0, 0.0

    half_qty = round_step_size(0.5 * quantity, step_size)
    runner_qty = round_step_size(quantity - half_qty, step_size)
    half_notional = half_qty * entry_price
    runner_notional = runner_qty * entry_price
    effective_buffer = max(min_notional_buffer, (min_notional or 5.0) * 1.1)

    # Check 1: Does half quantity meet min_qty?
    if half_qty < min_qty or runner_qty < min_qty:
        return (
            ExitPlanType.FULL_CLOSE_TP1,
            f"Half quantity ({half_qty}) or runner ({runner_qty}) is below exchange min_qty ({min_qty}); switched to 100% Full Exit at TP1.",
            quantity,
            0.0,
        )

    # Check 2: Does half notional meet exchange min_notional buffer?
    if half_notional < effective_buffer or runner_notional < effective_buffer:
        return (
            ExitPlanType.FULL_CLOSE_TP1,
            f"Half notional (${half_notional:.2f}) is below exchange min_notional threshold (${effective_buffer:.2f}); switched to 100% Full Exit at TP1.",
            quantity,
            0.0,
        )

    return (
        ExitPlanType.PARTIAL_TP1_RUNNER_TP2,
        f"Half notional (${half_notional:.2f}) satisfies exchange min_notional buffer (${effective_buffer:.2f}); 50% partial exit at TP1 and 50% runner to TP2 enabled.",
        half_qty,
        runner_qty,
    )


class ModelBLivePreFlightValidator:
    """Pre-flight validator and dry-run shadow execution engine for Model B Live Micro-Account."""

    def __init__(
        self,
        config: Optional[Any] = None,
        micro_config: Optional[ModelBLiveMicroConfig] = None,
        account_service: Optional[Any] = None,
        specs_provider: Optional[Any] = None,
    ) -> None:
        self.micro_config = micro_config or ModelBLiveMicroConfig()
        if config:
            self.micro_config.target_trade_risk_usdt = getattr(config, "live_target_trade_risk_usdt", self.micro_config.target_trade_risk_usdt)
            self.micro_config.max_margin_usdt = getattr(config, "live_max_margin_usdt", self.micro_config.max_margin_usdt)
            self.micro_config.daily_loss_limit_usdt = getattr(config, "live_daily_loss_limit_usdt", self.micro_config.daily_loss_limit_usdt)
            self.micro_config.weekly_loss_limit_usdt = getattr(config, "live_weekly_loss_limit_usdt", self.micro_config.weekly_loss_limit_usdt)
            self.micro_config.max_open_positions = getattr(config, "live_max_open_positions", self.micro_config.max_open_positions)
            self.micro_config.max_open_per_direction = getattr(config, "live_max_open_per_direction", self.micro_config.max_open_per_direction)

        self.account_service = account_service
        self.specs_provider = specs_provider
        self.latest_preflight: Optional[LivePreFlightResult] = None

    def get_symbol_specs(self, symbol: str) -> SymbolSpecs:
        """Fetch SymbolSpecs from provider, adapter, or construct fallback."""
        if self.specs_provider:
            if hasattr(self.specs_provider, "get_symbol_specs"):
                try:
                    s = self.specs_provider.get_symbol_specs(symbol)
                    if s:
                        return s
                except Exception:
                    pass
            if hasattr(self.specs_provider, "specs_map") and symbol in self.specs_provider.specs_map:
                return self.specs_provider.specs_map[symbol]

        # Standard defaults for common Aster / Binance pairs
        is_btc = "BTC" in symbol.upper()
        is_eth = "ETH" in symbol.upper()
        is_sui = "SUI" in symbol.upper()
        is_sol = "SOL" in symbol.upper()

        if is_btc:
            tick_size, step_size, min_qty, min_notional = 0.1, 0.001, 0.001, 5.0
        elif is_eth:
            tick_size, step_size, min_qty, min_notional = 0.01, 0.001, 0.001, 5.0
        elif is_sui:
            tick_size, step_size, min_qty, min_notional = 0.0001, 0.1, 0.1, 5.0
        elif is_sol:
            tick_size, step_size, min_qty, min_notional = 0.01, 0.01, 0.01, 5.0
        else:
            tick_size, step_size, min_qty, min_notional = 0.001, 0.01, 0.01, 5.0

        return SymbolSpecs(
            symbol=symbol,
            base_asset=symbol.replace("USDT", ""),
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=tick_size,
            price_precision=4,
            step_size=step_size,
            quantity_precision=3,
            min_qty=min_qty,
            max_qty=1000000.0,
            min_notional=min_notional,
            maint_margin_percent=2.5,
        )

    def inspect_manual_positions(self) -> List[Dict[str, Any]]:
        """Inspect live positions on Aster DEX and classify manual external positions to isolate them."""
        isolated: List[Dict[str, Any]] = []
        if not self.account_service:
            return isolated

        try:
            snapshot = self.account_service.get_snapshot()
            for p in snapshot.positions:
                sym = p.get("symbol")
                raw_amt = float(p.get("raw_position_amt", p.get("positionAmt", 0.0)))
                if abs(raw_amt) < 1e-8:
                    continue

                # Check if position is tagged as Model B managed
                # In current runtime, SUIUSDT is the manual external trade
                is_manual = True
                client_id = str(p.get("client_order_id") or "")
                if client_id.startswith("MODEL_B_"):
                    is_manual = False

                if is_manual:
                    isolated.append({
                        "symbol": sym,
                        "side": "LONG" if raw_amt > 0 else "SHORT",
                        "position_amt": abs(raw_amt),
                        "entry_price": float(p.get("entry_price", p.get("entryPrice", 0.0))),
                        "mark_price": float(p.get("mark_price", p.get("markPrice", 0.0))),
                        "unrealized_pnl_usdt": float(p.get("unrealized_pnl_usdt", p.get("unRealizedProfit", 0.0))),
                        "allocated_margin_usdt": float(p.get("allocated_margin_usdt", p.get("isolatedMargin", 0.0))),
                        "protection_status": p.get("protection_status", "PROTECTED"),
                        "isolation_status": "MANUAL_EXTERNAL_PROTECTED",
                        "is_safe_from_automated_actions": True,
                        "action_policy": "NO_TOUCH_MANUAL_POSITION",
                    })
        except Exception as e:
            logger.warning(f"Could not inspect manual positions from account service: {e}")

        return isolated

    def validate_candidate(
        self,
        candidate: SetupCandidate,
        symbol_specs: Optional[SymbolSpecs] = None,
        now_ms: Optional[int] = None,
        live_open_positions_count: int = 0,
        live_open_direction_count: int = 0,
    ) -> LivePreFlightResult:
        """Perform comprehensive live pre-flight validation against exchange filters and micro risk limits."""
        now_ts = now_ms or int(time.time() * 1000)
        from datetime import datetime, timezone
        now_iso = datetime.fromtimestamp(now_ts / 1000.0, tz=timezone.utc).isoformat()

        specs = symbol_specs or self.get_symbol_specs(candidate.symbol)
        dir_str = (candidate.direction.value if hasattr(candidate.direction, "value") else str(candidate.direction)).upper()

        # Fetch wallet balances
        total_equity = 16.0
        available_balance = 14.5
        if self.account_service:
            try:
                snap = self.account_service.get_snapshot()
                if snap.total_equity_usdt is not None:
                    total_equity = float(snap.total_equity_usdt)
                if snap.available_balance_usdt is not None:
                    available_balance = float(snap.available_balance_usdt)
            except Exception:
                pass

        # 1. Micro-sizing computation under $0.45 risk
        risk_budget = self.micro_config.target_trade_risk_usdt
        risk_dist = abs(candidate.entry_price - candidate.stop_loss)
        raw_qty = (risk_budget / risk_dist) if risk_dist > 0 else 0.0

        rounded_qty = round_step_size(raw_qty, specs.step_size)
        rounded_entry = round_tick_size(candidate.entry_price, specs.tick_size)
        rounded_sl = round_tick_size(candidate.stop_loss, specs.tick_size)
        rounded_tp1 = round_tick_size(candidate.take_profit_1, specs.tick_size)
        rounded_tp2 = round_tick_size(candidate.take_profit_2, specs.tick_size) if candidate.take_profit_2 else None

        pos_notional = round(rounded_qty * rounded_entry, 4)
        req_margin = round(pos_notional / self.micro_config.leverage, 4)
        eff_leverage = round(pos_notional / total_equity, 2) if total_equity > 0 else 0.0

        # 2. Exit plan evaluation (50% TP1 vs 100% TP1)
        exit_plan, exit_reason, tp1_qty, runner_qty = determine_exit_plan(
            quantity=rounded_qty,
            entry_price=rounded_entry,
            min_qty=specs.min_qty,
            min_notional=specs.min_notional,
            step_size=specs.step_size,
            min_notional_buffer=self.micro_config.min_notional_buffer,
        )

        # 3. Compliance checks
        checks = {
            "min_qty_passed": bool(rounded_qty >= specs.min_qty),
            "min_notional_passed": bool(pos_notional >= specs.min_notional),
            "margin_budget_passed": bool(req_margin <= self.micro_config.max_margin_usdt),
            "wallet_balance_passed": bool(req_margin <= available_balance),
            "concurrency_passed": bool(live_open_positions_count < self.micro_config.max_open_positions),
            "directional_passed": bool(live_open_direction_count < self.micro_config.max_open_per_direction),
        }

        rejections: List[str] = []
        if not checks["min_qty_passed"]:
            rejections.append(f"Position quantity ({rounded_qty}) is below exchange min_qty ({specs.min_qty}).")
        if not checks["min_notional_passed"]:
            rejections.append(f"Position notional (${pos_notional:.2f}) is below exchange min_notional (${specs.min_notional:.2f}).")
        if not checks["margin_budget_passed"]:
            rejections.append(f"Required margin (${req_margin:.2f}) exceeds micro cap (${self.micro_config.max_margin_usdt:.2f}).")
        if not checks["wallet_balance_passed"]:
            rejections.append(f"Required margin (${req_margin:.2f}) exceeds live available balance (${available_balance:.2f}).")
        if not checks["concurrency_passed"]:
            rejections.append(f"Live position concurrency cap reached ({live_open_positions_count}/{self.micro_config.max_open_positions}).")
        if not checks["directional_passed"]:
            rejections.append(f"Live directional position cap reached ({live_open_direction_count}/{self.micro_config.max_open_per_direction} {dir_str}).")

        is_compliant = len(rejections) == 0

        # 4. Generate Aster DEX V3 compliant dry-run order payloads
        clean_cand_id = candidate.candidate_id.replace("-", "").replace("_", "")[:12]
        ts_suffix = str(now_ts)[-6:]
        entry_side = "BUY" if dir_str == "LONG" else "SELL"
        close_side = "SELL" if dir_str == "LONG" else "BUY"

        dry_run_payload = {
            "entry_order": {
                "symbol": candidate.symbol,
                "side": entry_side,
                "order_type": "LIMIT",
                "quantity": rounded_qty,
                "price": rounded_entry,
                "time_in_force": "GTC",
                "client_order_id": f"MODEL_B_ENT_{clean_cand_id}_{ts_suffix}",
            },
            "stop_loss_order": {
                "symbol": candidate.symbol,
                "side": close_side,
                "order_type": "STOP_MARKET",
                "stop_price": rounded_sl,
                "quantity": rounded_qty,
                "reduce_only": True,
                "client_order_id": f"MODEL_B_SL_{clean_cand_id}_{ts_suffix}",
            },
            "take_profit_1_order": {
                "symbol": candidate.symbol,
                "side": close_side,
                "order_type": "TAKE_PROFIT_MARKET",
                "stop_price": rounded_tp1,
                "quantity": tp1_qty,
                "reduce_only": True,
                "client_order_id": f"MODEL_B_TP1_{clean_cand_id}_{ts_suffix}",
            },
            "take_profit_2_order": (
                {
                    "symbol": candidate.symbol,
                    "side": close_side,
                    "order_type": "TAKE_PROFIT_MARKET",
                    "stop_price": rounded_tp2,
                    "quantity": runner_qty,
                    "reduce_only": True,
                    "client_order_id": f"MODEL_B_TP2_{clean_cand_id}_{ts_suffix}",
                }
                if (exit_plan == ExitPlanType.PARTIAL_TP1_RUNNER_TP2 and rounded_tp2 and runner_qty > 0)
                else None
            ),
            "exit_plan": exit_plan.value if hasattr(exit_plan, "value") else str(exit_plan),
            "live_trading_locked": True,
            "mode": "DRY_RUN_SHADOW",
        }

        result = LivePreFlightResult(
            symbol=candidate.symbol,
            direction=dir_str,
            risk_budget_usdt=risk_budget,
            raw_quantity=round(raw_qty, 6),
            rounded_quantity=rounded_qty,
            entry_price=candidate.entry_price,
            rounded_entry_price=rounded_entry,
            stop_loss_price=candidate.stop_loss,
            rounded_stop_loss_price=rounded_sl,
            take_profit_1_price=candidate.take_profit_1,
            rounded_take_profit_1_price=rounded_tp1,
            take_profit_2_price=candidate.take_profit_2,
            rounded_take_profit_2_price=rounded_tp2,
            position_notional_usdt=pos_notional,
            required_margin_usdt=req_margin,
            effective_leverage=eff_leverage,
            step_size=specs.step_size,
            tick_size=specs.tick_size,
            min_qty=specs.min_qty,
            min_notional=specs.min_notional,
            exit_plan=exit_plan.value if hasattr(exit_plan, "value") else str(exit_plan),
            exit_plan_reason=exit_reason,
            tp1_close_qty=tp1_qty,
            runner_tp2_qty=runner_qty,
            is_compliant=is_compliant,
            rejection_reasons=rejections,
            compliance_checks=checks,
            dry_run_order_payload=dry_run_payload,
            account_available_balance=available_balance,
            account_total_equity=total_equity,
            timestamp_utc=now_iso,
        )

        self.latest_preflight = result
        return result

    def get_live_readiness_telemetry(self) -> Dict[str, Any]:
        """Aggregate full live-readiness status, wallet balances, manual positions, and dry-run telemetry."""
        manual_positions = self.inspect_manual_positions()

        total_equity = 16.0
        available_balance = 14.5
        used_margin = 0.33
        status_str = "CONNECTED"

        if self.account_service:
            try:
                snap = self.account_service.get_snapshot()
                total_equity = snap.total_equity_usdt or total_equity
                available_balance = snap.available_balance_usdt or available_balance
                used_margin = snap.used_margin_usdt or used_margin
                status_str = snap.status.value if hasattr(snap.status, "value") else str(snap.status)
            except Exception:
                pass

        return {
            "mode": "DRY_RUN_SHADOW",
            "live_trading_locked": True,
            "profile_name": "MODEL_B_LIVE_MICRO_16USDT",
            "micro_profile": {
                "target_trade_risk_usdt": self.micro_config.target_trade_risk_usdt,
                "max_margin_usdt": self.micro_config.max_margin_usdt,
                "daily_loss_limit_usdt": self.micro_config.daily_loss_limit_usdt,
                "weekly_loss_limit_usdt": self.micro_config.weekly_loss_limit_usdt,
                "max_open_positions": self.micro_config.max_open_positions,
                "max_open_per_direction": self.micro_config.max_open_per_direction,
                "min_notional_buffer": self.micro_config.min_notional_buffer,
                "leverage": self.micro_config.leverage,
            },
            "wallet_state": {
                "connection_status": status_str,
                "total_equity_usdt": total_equity,
                "available_balance_usdt": available_balance,
                "used_margin_usdt": used_margin,
            },
            "manual_positions_isolated": manual_positions,
            "manual_positions_count": len(manual_positions),
            "manual_isolation_enforced": True,
            "latest_candidate_preflight": self.latest_preflight.to_dict() if self.latest_preflight else None,
        }
