"""Model B Strategy Generator.

Scans the eligible universal perpetual universe on 1H bar close, validates liquidity
depth and absorption, checks feasibility, and constructs Model B SetupCandidate instances.
"""

from typing import Any, Dict, List, Optional
import math
import time

from src.core.logger import logger
from src.core.types import Candle, CandidateStatus, Direction, EntryCategory, RiskAssessment, SetupCandidate
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.normalized import NormalizedInstrument
from src.exchange.service import MultiExchangeService
from src.liquidity.collector import LiquidityCollector
from src.strategy.model_b.evaluator import evaluate_liquidity_setup
from config.settings import ModelBConfig


class ModelBStrategyGenerator:
    """Independent strategy generator for Model B Aggressive Liquidity Research."""

    def __init__(
        self,
        config: Optional[ModelBConfig] = None,
        exchange_service: Optional[MultiExchangeService] = None,
        liquidity_collector: Optional[LiquidityCollector] = None,
    ) -> None:
        self.config = config or ModelBConfig()
        self.exchange_service = exchange_service or MultiExchangeService()
        self.liquidity_collector = liquidity_collector

    def size_position(
        self,
        entry_price: float,
        stop_loss: float,
        instrument: NormalizedInstrument,
        fee_rate: float = 0.0005,
    ) -> RiskAssessment:
        """Calculate position size and risk feasibility against Model B risk policy."""
        stop_dist = abs(entry_price - stop_loss)
        if stop_dist <= 0 or entry_price <= 0:
            return RiskAssessment(
                is_feasible=False,
                quantity=0.0,
                position_notional_usdt=0.0,
                target_isolated_margin_usdt=0.0,
                position_leverage=1.0,
                rejection_reasons=["Invalid entry or stop-loss price."],
            )

        # 1. Unconstrained sizing from target risk ($1.00 USDT)
        raw_qty = self.config.target_trade_risk_usdt / stop_dist

        # 2. Round to exchange step size
        step = instrument.step_size or 0.001
        step_precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
        quantity = round(math.floor(raw_qty / step) * step, step_precision)

        if quantity < instrument.min_qty:
            quantity = instrument.min_qty

        notional = quantity * entry_price
        rejections: List[str] = []

        # 3. Minimum notional check
        if notional < instrument.min_notional:
            rejections.append(
                f"Position notional ${notional:.2f} below exchange minimum ${instrument.min_notional:.2f}."
            )

        # 4. Leverage and margin check
        leverage = float(self.config.max_leverage)
        required_margin = notional / leverage if leverage > 0 else notional

        if required_margin > self.config.max_margin_usdt:
            rejections.append(
                f"Required margin ${required_margin:.2f} exceeds Model B cap ${self.config.max_margin_usdt:.2f}."
            )

        # 5. True loss calculation including maker entry + taker exit fees + adverse slippage
        price_loss = quantity * stop_dist
        entry_fee = notional * fee_rate
        exit_fee = (quantity * stop_loss) * fee_rate
        est_slippage = notional * 0.0005
        est_funding = notional * 0.0001
        estimated_loss = price_loss + entry_fee + exit_fee + est_slippage + est_funding

        # Budget check: If minimum lot size forces risk to exceed 1.25x target, reject as INFEASIBLE
        max_allowed_loss = self.config.target_trade_risk_usdt * 1.25
        if estimated_loss > max_allowed_loss:
            rejections.append(
                f"Estimated loss ${estimated_loss:.2f} exceeds risk budget cap ${max_allowed_loss:.2f}."
            )

        is_feasible = len(rejections) == 0

        return RiskAssessment(
            symbol=instrument.symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            stop_distance_pct=round(stop_dist / entry_price * 100.0, 4) if entry_price > 0 else 0.0,
            risk_budget_usdt=self.config.target_trade_risk_usdt,
            position_notional_usdt=round(notional, 4),
            quantity=quantity,
            required_margin_usdt=round(required_margin, 4),
            effective_leverage=round(notional / self.config.target_account_equity_usdt, 2),
            position_leverage=leverage,
            estimated_fee_usdt=round(entry_fee + exit_fee, 4),
            estimated_slippage_usdt=round(est_slippage, 4),
            estimated_funding_usdt=round(est_funding, 4),
            is_feasible=is_feasible,
            infeasible_reason="; ".join(rejections) if rejections else None,
            target_isolated_margin_usdt=round(required_margin, 4),
            estimated_loss_usdt=round(estimated_loss, 4),
            estimated_loss_pct=round(estimated_loss / self.config.target_account_equity_usdt * 100.0, 4),
            rejection_reasons=rejections,
        )

    def evaluate_symbol(
        self,
        symbol: str,
        candles: List[Candle],
        instrument: NormalizedInstrument,
        now_ms: Optional[int] = None,
    ) -> Optional[SetupCandidate]:
        """Evaluate a single symbol's 1H candles and liquidity for Model B candidacy."""
        if not self.liquidity_collector:
            return None

        # Fetch liquidity snapshot
        snapshot = self.liquidity_collector.collect_for_symbol(
            symbol=symbol,
            candle=candles[-1] if candles else None,
        )
        if not snapshot:
            return None

        # Check volume screen
        vol_24h = instrument.volume_24h_usdt or 0.0
        if vol_24h > 0 and vol_24h < self.config.min_24h_volume_usdt:
            return None

        # Check spread screen
        if snapshot.spread_bps > self.config.max_spread_bps:
            return None

        # Run structural + liquidity setup evaluator
        signal = evaluate_liquidity_setup(
            symbol=symbol,
            candles=candles,
            snapshot=snapshot,
            min_imbalance=self.config.min_imbalance_ratio,
            min_absorption=self.config.min_absorption_score,
            max_spread_bps=self.config.max_spread_bps,
        )
        if not signal:
            return None

        # Sizing and risk assessment
        assessment = self.size_position(
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            instrument=instrument,
        )

        status = CandidateStatus.READY if assessment.is_feasible else CandidateStatus.INFEASIBLE
        ts_ms = now_ms or signal.timestamp_ms
        expires_at = ts_ms + int(self.config.pending_order_ttl_hours * 3600 * 1000)

        cid = f"MCAND_B_{symbol}_{ts_ms}"

        candidate = SetupCandidate(
            candidate_id=cid,
            symbol=symbol,
            direction=signal.direction,
            timeframe="1h",
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            rr_ratio=1.5,
            confidence_score=signal.confidence_score,
            timestamp_ms=ts_ms,
            status=status,
            rejection_reasons=assessment.rejection_reasons,
            model_id="MODEL_B",
            entry_category=EntryCategory.CONFIRMED,
            risk_assessment=assessment,
            expires_at_ms=expires_at,
            metadata={
                "strategy": "Model_B_Liquidity_Sweep",
                "signal_type": signal.signal_type,
                "reasons": signal.reasons,
                "spread_bps": snapshot.spread_bps,
                "imbalance_25bps": snapshot.imbalance_25bps,
                "cvd_1h": snapshot.cvd_1h,
                "absorption_score": snapshot.absorption_score,
                "exchange": snapshot.exchange_name,
            },
        )
        return candidate
