"""AsterDEX Live Position & Market Condition Analyzer (Analyze Entry Engine).

Provides multi-timeframe structural audit, thesis validation, risk exposure calculation,
and actionable conditional recommendations for open AsterDEX positions.

Strictly READ-ONLY. No live order placement, modification, or cancellation.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.logger import logger
from src.core.types import (
    Candle,
    Direction,
    OrderBlock,
    SMCEvent,
    SMCEventType,
    StructureState,
    SwingPoint,
    SwingType,
)
from src.smc.fvg import FVGDetector
from src.smc.order_blocks import OrderBlockDetector
from src.smc.structure import MarketStructureEngine
from src.smc.swings import detect_swings


class ThesisStatus(str, Enum):
    VALID = "VALID"
    WEAKENING = "WEAKENING"
    INVALIDATED = "INVALIDATED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ActionRecommendation(str, Enum):
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    ADJUST = "ADJUST"
    NO_ACTION = "NO_ACTION"


class ProtectionStatus(str, Enum):
    FULLY_PROTECTED = "FULLY_PROTECTED"
    PROTECTED_NO_TP = "PROTECTED_NO_TP"
    UNPROTECTED_NO_SL = "UNPROTECTED_NO_SL"
    COMPLETELY_UNPROTECTED = "COMPLETELY_UNPROTECTED"


class LiquidationThreat(str, Enum):
    CRITICAL = "CRITICAL"
    ELEVATED = "ELEVATED"
    MODERATE = "MODERATE"
    SAFE = "SAFE"
    UNKNOWN = "UNKNOWN"


@dataclass
class TimeframeStructure:
    """Structure state on a specific timeframe."""
    timeframe: str
    trend: str
    last_swing_high: Optional[float] = None
    last_swing_low: Optional[float] = None
    bos_count: int = 0
    choch_count: int = 0
    recent_events: List[Dict[str, Any]] = field(default_factory=list)
    unmitigated_order_blocks: List[Dict[str, Any]] = field(default_factory=list)
    unmitigated_fvgs: List[Dict[str, Any]] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "trend": self.trend,
            "last_swing_high": self.last_swing_high,
            "last_swing_low": self.last_swing_low,
            "bos_count": self.bos_count,
            "choch_count": self.choch_count,
            "recent_events": self.recent_events,
            "unmitigated_order_blocks": self.unmitigated_order_blocks,
            "unmitigated_fvgs": self.unmitigated_fvgs,
            "description": self.description,
        }


@dataclass
class PositionRiskAnalysis:
    """Detailed risk, fee, and exposure assessment."""
    notional_usdt: float
    allocated_margin_usdt: float
    has_stop_loss: bool
    stop_loss_price: Optional[float] = None
    sl_distance_usdt: Optional[float] = None
    sl_distance_pct: Optional[float] = None
    gross_loss_at_sl_usdt: Optional[float] = None
    est_taker_fees_usdt: float = 0.0
    est_slippage_usdt: float = 0.0
    all_in_loss_at_sl_usdt: Optional[float] = None
    loss_equity_pct: Optional[float] = None
    has_take_profit: bool = False
    take_profit_price: Optional[float] = None
    tp_distance_usdt: Optional[float] = None
    tp_distance_pct: Optional[float] = None
    gross_profit_at_tp_usdt: Optional[float] = None
    net_profit_at_tp_usdt: Optional[float] = None
    risk_reward_ratio: Optional[float] = None
    has_liquidation_price: bool = False
    liquidation_price: Optional[float] = None
    liquidation_distance_usdt: Optional[float] = None
    liquidation_distance_pct: Optional[float] = None
    liquidation_threat: LiquidationThreat = LiquidationThreat.UNKNOWN
    margin_mode: str = "ISOLATED"
    margin_mode_assessment: str = ""
    protection_status: ProtectionStatus = ProtectionStatus.COMPLETELY_UNPROTECTED
    protection_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "notional_usdt": self.notional_usdt,
            "allocated_margin_usdt": self.allocated_margin_usdt,
            "has_stop_loss": self.has_stop_loss,
            "stop_loss_price": self.stop_loss_price,
            "sl_distance_usdt": self.sl_distance_usdt,
            "sl_distance_pct": self.sl_distance_pct,
            "gross_loss_at_sl_usdt": self.gross_loss_at_sl_usdt,
            "est_taker_fees_usdt": self.est_taker_fees_usdt,
            "est_slippage_usdt": self.est_slippage_usdt,
            "all_in_loss_at_sl_usdt": self.all_in_loss_at_sl_usdt,
            "loss_equity_pct": self.loss_equity_pct,
            "has_take_profit": self.has_take_profit,
            "take_profit_price": self.take_profit_price,
            "tp_distance_usdt": self.tp_distance_usdt,
            "tp_distance_pct": self.tp_distance_pct,
            "gross_profit_at_tp_usdt": self.gross_profit_at_tp_usdt,
            "net_profit_at_tp_usdt": self.net_profit_at_tp_usdt,
            "risk_reward_ratio": self.risk_reward_ratio,
            "has_liquidation_price": self.has_liquidation_price,
            "liquidation_price": self.liquidation_price,
            "liquidation_distance_usdt": self.liquidation_distance_usdt,
            "liquidation_distance_pct": self.liquidation_distance_pct,
            "liquidation_threat": self.liquidation_threat.value,
            "margin_mode": self.margin_mode,
            "margin_mode_assessment": self.margin_mode_assessment,
            "protection_status": self.protection_status.value,
            "protection_notes": self.protection_notes,
        }


@dataclass
class SinglePositionAnalysis:
    """Comprehensive thesis and condition analysis for a live position."""
    symbol: str
    side: str
    entry_price: float
    mark_price: float
    position_amt: float
    unrealized_pnl_usdt: float
    roe_pct: float
    roi_pct: float
    thesis_status: ThesisStatus
    thesis_summary: str
    structural_evidence: List[str]
    action: ActionRecommendation
    action_summary: str
    conditional_triggers: List[str]
    invalidation_conditions: List[str]
    timeframe_4h: TimeframeStructure
    timeframe_1h: TimeframeStructure
    timeframe_15m: TimeframeStructure
    risk: PositionRiskAnalysis
    analyzed_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "mark_price": self.mark_price,
            "position_amt": self.position_amt,
            "unrealized_pnl_usdt": self.unrealized_pnl_usdt,
            "roe_pct": self.roe_pct,
            "roi_pct": self.roi_pct,
            "thesis_status": self.thesis_status.value,
            "thesis_summary": self.thesis_summary,
            "structural_evidence": self.structural_evidence,
            "action": self.action.value,
            "action_summary": self.action_summary,
            "conditional_triggers": self.conditional_triggers,
            "invalidation_conditions": self.invalidation_conditions,
            "timeframe_4h": self.timeframe_4h.to_dict(),
            "timeframe_1h": self.timeframe_1h.to_dict(),
            "timeframe_15m": self.timeframe_15m.to_dict(),
            "risk": self.risk.to_dict(),
            "analyzed_at_utc": self.analyzed_at_utc,
        }


@dataclass
class PortfolioAnalysis:
    """Portfolio-level risk, net exposure, and health audit."""
    total_equity_usdt: Optional[float]
    available_balance_usdt: Optional[float]
    open_positions_count: int
    total_notional_usdt: float
    total_margin_committed_usdt: float
    margin_utilization_pct: float
    long_notional_usdt: float
    short_notional_usdt: float
    net_directional_bias: str
    net_exposure_usdt: float
    total_all_in_risk_at_stops_usdt: float
    portfolio_risk_equity_pct: Optional[float]
    unprotected_positions_count: int
    concentration_warnings: List[str]
    correlation_warnings: List[str]
    portfolio_health_status: str
    portfolio_action_summary: str
    positions_analyses: List[SinglePositionAnalysis]
    analyzed_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_equity_usdt": self.total_equity_usdt,
            "available_balance_usdt": self.available_balance_usdt,
            "open_positions_count": self.open_positions_count,
            "total_notional_usdt": self.total_notional_usdt,
            "total_margin_committed_usdt": self.total_margin_committed_usdt,
            "margin_utilization_pct": self.margin_utilization_pct,
            "long_notional_usdt": self.long_notional_usdt,
            "short_notional_usdt": self.short_notional_usdt,
            "net_directional_bias": self.net_directional_bias,
            "net_exposure_usdt": self.net_exposure_usdt,
            "total_all_in_risk_at_stops_usdt": self.total_all_in_risk_at_stops_usdt,
            "portfolio_risk_equity_pct": self.portfolio_risk_equity_pct,
            "unprotected_positions_count": self.unprotected_positions_count,
            "concentration_warnings": self.concentration_warnings,
            "correlation_warnings": self.correlation_warnings,
            "portfolio_health_status": self.portfolio_health_status,
            "portfolio_action_summary": self.portfolio_action_summary,
            "positions_analyses": [p.to_dict() for p in self.positions_analyses],
            "analyzed_at_utc": self.analyzed_at_utc,
        }


class PositionAnalyzer:
    """Analyzes open AsterDEX positions against multi-timeframe market conditions."""

    TAKER_FEE_RATE = 0.0005     # 0.05% standard AsterDEX taker fee
    DEFAULT_SLIPPAGE_RATE = 0.0005  # 0.05% estimated execution slippage

    @classmethod
    def analyze_position(
        cls,
        position: Dict[str, Any],
        candles_4h: Optional[List[Candle]] = None,
        candles_1h: Optional[List[Candle]] = None,
        candles_15m: Optional[List[Candle]] = None,
        account_equity: Optional[float] = None,
    ) -> SinglePositionAnalysis:
        """Perform comprehensive entry thesis audit and conditional risk analysis on a single position."""
        symbol = str(position.get("symbol", "")).upper()
        side = str(position.get("side", "LONG")).upper()
        pos_amt = abs(float(position.get("position_amt", position.get("positionAmt", 0.0))))
        entry_price = float(position.get("entry_price", position.get("entryPrice", 0.0)))
        mark_price = float(position.get("mark_price", position.get("markPrice", entry_price)))
        upnl = float(position.get("unrealized_pnl_usdt", position.get("unRealizedProfit", 0.0)))
        roe = float(position.get("roe_pct", 0.0))
        roi = float(position.get("roi_pct", 0.0))
        margin_mode = str(position.get("margin_mode", position.get("marginType", "ISOLATED"))).upper()
        alloc_margin = float(position.get("allocated_margin_usdt", position.get("isolatedMargin", 0.0)))
        if alloc_margin <= 0 and entry_price > 0:
            lev = float(position.get("leverage", 1.0)) or 1.0
            alloc_margin = (pos_amt * entry_price) / lev

        # 1. Multi-Timeframe Structural Analysis
        tf_4h = cls._evaluate_tf_structure(candles_4h or [], "4h", mark_price)
        tf_1h = cls._evaluate_tf_structure(candles_1h or [], "1h", mark_price)
        tf_15m = cls._evaluate_tf_structure(candles_15m or [], "15m", mark_price)

        # 2. Risk & Fee Assessment
        risk_analysis = cls._evaluate_risk(
            position=position,
            pos_amt=pos_amt,
            entry_price=entry_price,
            mark_price=mark_price,
            margin_mode=margin_mode,
            allocated_margin=alloc_margin,
            account_equity=account_equity,
        )

        # 3. Thesis Audit & Evidence Synthesis
        thesis_status, thesis_summary, evidence = cls._audit_thesis(
            side=side,
            entry_price=entry_price,
            mark_price=mark_price,
            tf_4h=tf_4h,
            tf_1h=tf_1h,
            tf_15m=tf_15m,
        )

        # 4. Action Recommendation with Conditional Triggers & Invalidation
        action, action_summary, triggers, invalidations = cls._derive_recommendation(
            side=side,
            entry_price=entry_price,
            mark_price=mark_price,
            thesis_status=thesis_status,
            risk=risk_analysis,
            tf_4h=tf_4h,
            tf_1h=tf_1h,
            tf_15m=tf_15m,
        )

        return SinglePositionAnalysis(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            mark_price=mark_price,
            position_amt=pos_amt,
            unrealized_pnl_usdt=round(upnl, 4),
            roe_pct=round(roe, 2),
            roi_pct=round(roi, 2),
            thesis_status=thesis_status,
            thesis_summary=thesis_summary,
            structural_evidence=evidence,
            action=action,
            action_summary=action_summary,
            conditional_triggers=triggers,
            invalidation_conditions=invalidations,
            timeframe_4h=tf_4h,
            timeframe_1h=tf_1h,
            timeframe_15m=tf_15m,
            risk=risk_analysis,
        )

    @classmethod
    def _evaluate_tf_structure(
        cls,
        candles: List[Candle],
        timeframe: str,
        current_price: float,
    ) -> TimeframeStructure:
        """Extract swings, trend, structural breaks, order blocks, and FVGs for a single timeframe."""
        if not candles or len(candles) < 5:
            return TimeframeStructure(
                timeframe=timeframe,
                trend=StructureState.SIDEWAYS.value,
                description=f"Insufficient candle history ({len(candles) if candles else 0} bars available).",
            )

        # Detect non-lookahead swings
        swings = detect_swings(candles, left_bars=2, right_bars=2)
        structure_state, events = MarketStructureEngine.evaluate_structure(candles, swings)

        # Find latest swings
        last_high = None
        last_low = None
        for s in reversed(swings):
            if s.swing_type == SwingType.HIGH and last_high is None:
                last_high = s.price
            elif s.swing_type == SwingType.LOW and last_low is None:
                last_low = s.price
            if last_high is not None and last_low is not None:
                break

        # Count BOS and CHOCH
        bos_cnt = sum(1 for e in events if e.event_type == SMCEventType.BOS)
        choch_cnt = sum(1 for e in events if e.event_type == SMCEventType.CHOCH)

        # Summarize recent events (last 3)
        recent_ev_list = []
        for e in reversed(events[-3:]):
            recent_ev_list.append({
                "type": e.event_type.value,
                "direction": e.direction.value,
                "price_level": round(e.price_level, 4),
                "timestamp_ms": e.timestamp_ms,
                "description": e.description,
            })

        # Order Blocks & FVGs
        obs = OrderBlockDetector.identify_order_blocks(candles, events, max_search_bars=8)
        unmitigated_obs = []
        for ob in obs:
            if not ob.mitigated:
                unmitigated_obs.append({
                    "direction": ob.direction.value,
                    "top": round(ob.high, 4),
                    "bottom": round(ob.low, 4),
                    "is_mitigated": False,
                })

        fvgs = FVGDetector.detect_fvgs(candles)
        unmitigated_fvgs = []
        for f in fvgs:
            if not f.mitigated:
                unmitigated_fvgs.append({
                    "direction": f.direction.value,
                    "top": round(f.top, 4),
                    "bottom": round(f.bottom, 4),
                })

        desc_parts = [f"{structure_state.value} structure"]
        if last_high:
            desc_parts.append(f"Swing High: ${last_high:,.4f}")
        if last_low:
            desc_parts.append(f"Swing Low: ${last_low:,.4f}")
        if choch_cnt > 0:
            desc_parts.append(f"{choch_cnt} CHoCH confirmed")
        elif bos_cnt > 0:
            desc_parts.append(f"{bos_cnt} BOS confirmed")

        return TimeframeStructure(
            timeframe=timeframe,
            trend=structure_state.value,
            last_swing_high=last_high,
            last_swing_low=last_low,
            bos_count=bos_cnt,
            choch_count=choch_cnt,
            recent_events=recent_ev_list,
            unmitigated_order_blocks=unmitigated_obs[:4],
            unmitigated_fvgs=unmitigated_fvgs[:4],
            description=" | ".join(desc_parts),
        )

    @classmethod
    def _evaluate_risk(
        cls,
        position: Dict[str, Any],
        pos_amt: float,
        entry_price: float,
        mark_price: float,
        margin_mode: str,
        allocated_margin: float,
        account_equity: Optional[float] = None,
    ) -> PositionRiskAnalysis:
        """Compute all-in loss including fees, slippage, liquidation proximity, and protection gaps."""
        notional = round(pos_amt * mark_price, 4)
        is_long = str(position.get("side", "LONG")).upper() == "LONG"

        # Protection orders
        raw_sl = position.get("stop_loss_price")
        sl_val = float(raw_sl) if (raw_sl is not None and str(raw_sl).strip() != "") else None

        raw_tp = position.get("take_profit_price")
        tp_val = float(raw_tp) if (raw_tp is not None and str(raw_tp).strip() != "") else None

        raw_liq = position.get("liquidation_price")
        liq_val = float(raw_liq) if (raw_liq is not None and str(raw_liq).strip() != "" and float(raw_liq) > 0) else None

        notes: List[str] = []

        # SL Calculations
        has_sl = sl_val is not None and sl_val > 0
        sl_dist_usdt = None
        sl_dist_pct = None
        gross_loss_usdt = None
        fee_entry = round(cls.TAKER_FEE_RATE * (pos_amt * entry_price), 4)
        fee_exit = 0.0
        slippage = 0.0
        all_in_loss = None
        loss_eq_pct = None

        if has_sl:
            sl_dist_usdt = round(abs(entry_price - sl_val), 4)
            sl_dist_pct = round((sl_dist_usdt / entry_price) * 100, 2)
            gross_loss_usdt = round(sl_dist_usdt * pos_amt, 4)
            fee_exit = round(cls.TAKER_FEE_RATE * (pos_amt * sl_val), 4)
            slippage = round(cls.DEFAULT_SLIPPAGE_RATE * (pos_amt * sl_val), 4)
            all_in_loss = round(gross_loss_usdt + fee_entry + fee_exit + slippage, 4)
            if account_equity and account_equity > 0:
                loss_eq_pct = round((all_in_loss / account_equity) * 100, 2)
        else:
            notes.append("CRITICAL: Position has NO Stop Loss configured. Account exposed to full liquidation.")

        # TP Calculations
        has_tp = tp_val is not None and tp_val > 0
        tp_dist_usdt = None
        tp_dist_pct = None
        gross_profit = None
        net_profit = None
        rr_ratio = None

        if has_tp:
            tp_dist_usdt = round(abs(tp_val - entry_price), 4)
            tp_dist_pct = round((tp_dist_usdt / entry_price) * 100, 2)
            gross_profit = round(tp_dist_usdt * pos_amt, 4)
            fee_tp = round(cls.TAKER_FEE_RATE * (pos_amt * tp_val), 4)
            slip_tp = round(cls.DEFAULT_SLIPPAGE_RATE * (pos_amt * tp_val), 4)
            net_profit = round(gross_profit - (fee_entry + fee_tp + slip_tp), 4)
            if all_in_loss and all_in_loss > 0:
                rr_ratio = round(net_profit / all_in_loss, 2)
        else:
            notes.append("Notice: Position has no Take Profit configured.")

        # Liquidation Distance & Threat Level
        has_liq = liq_val is not None and liq_val > 0
        liq_dist_usdt = None
        liq_dist_pct = None
        liq_threat = LiquidationThreat.UNKNOWN

        if has_liq and mark_price > 0:
            liq_dist_usdt = round(abs(mark_price - liq_val), 4)
            liq_dist_pct = round((liq_dist_usdt / mark_price) * 100, 2)

            # Invalidate/check direction
            if is_long and liq_val >= mark_price:
                liq_threat = LiquidationThreat.CRITICAL
                notes.append("URGENT: Mark price has breached or equaled Long liquidation price!")
            elif not is_long and liq_val <= mark_price:
                liq_threat = LiquidationThreat.CRITICAL
                notes.append("URGENT: Mark price has breached or equaled Short liquidation price!")
            elif liq_dist_pct < 3.0:
                liq_threat = LiquidationThreat.CRITICAL
                notes.append(f"DANGER: Liquidation is within {liq_dist_pct}% of mark price.")
            elif liq_dist_pct < 8.0:
                liq_threat = LiquidationThreat.ELEVATED
            elif liq_dist_pct < 15.0:
                liq_threat = LiquidationThreat.MODERATE
            else:
                liq_threat = LiquidationThreat.SAFE

            # Compare SL with Liquidation price
            if has_sl:
                if is_long and sl_val <= liq_val:
                    notes.append("WARNING: Stop loss is below liquidation price! Liquidation will trigger BEFORE stop loss.")
                    liq_threat = LiquidationThreat.CRITICAL
                elif not is_long and sl_val >= liq_val:
                    notes.append("WARNING: Stop loss is above liquidation price! Liquidation will trigger BEFORE stop loss.")
                    liq_threat = LiquidationThreat.CRITICAL

        # Margin Mode Assessment
        if margin_mode == "ISOLATED":
            margin_desc = f"ISOLATED: Risk strictly capped to allocated margin (${allocated_margin:,.2f}). Other collateral safe."
        else:
            margin_desc = f"CROSS: Risk exposed to entire wallet equity (${account_equity:,.2f} available) if liquidation occurs."

        # Protection Status
        if has_sl and has_tp:
            prot_status = ProtectionStatus.FULLY_PROTECTED
        elif has_sl and not has_tp:
            prot_status = ProtectionStatus.PROTECTED_NO_TP
        elif not has_sl and has_tp:
            prot_status = ProtectionStatus.UNPROTECTED_NO_SL
        else:
            prot_status = ProtectionStatus.COMPLETELY_UNPROTECTED

        return PositionRiskAnalysis(
            notional_usdt=notional,
            allocated_margin_usdt=round(allocated_margin, 2),
            has_stop_loss=has_sl,
            stop_loss_price=sl_val,
            sl_distance_usdt=sl_dist_usdt,
            sl_distance_pct=sl_dist_pct,
            gross_loss_at_sl_usdt=gross_loss_usdt,
            est_taker_fees_usdt=round(fee_entry + fee_exit, 4),
            est_slippage_usdt=slippage,
            all_in_loss_at_sl_usdt=all_in_loss,
            loss_equity_pct=loss_eq_pct,
            has_take_profit=has_tp,
            take_profit_price=tp_val,
            tp_distance_usdt=tp_dist_usdt,
            tp_distance_pct=tp_dist_pct,
            gross_profit_at_tp_usdt=gross_profit,
            net_profit_at_tp_usdt=net_profit,
            risk_reward_ratio=rr_ratio,
            has_liquidation_price=has_liq,
            liquidation_price=liq_val,
            liquidation_distance_usdt=liq_dist_usdt,
            liquidation_distance_pct=liq_dist_pct,
            liquidation_threat=liq_threat,
            margin_mode=margin_mode,
            margin_mode_assessment=margin_desc,
            protection_status=prot_status,
            protection_notes=notes,
        )

    @classmethod
    def _audit_thesis(
        cls,
        side: str,
        entry_price: float,
        mark_price: float,
        tf_4h: TimeframeStructure,
        tf_1h: TimeframeStructure,
        tf_15m: TimeframeStructure,
    ) -> Tuple[ThesisStatus, str, List[str]]:
        """Audit trade thesis across 4H macro, 1H local, and 15M execution contexts."""
        evidence: List[str] = []
        is_long = side == "LONG"

        # Check for insufficient data
        if tf_4h.trend == StructureState.SIDEWAYS.value and tf_1h.trend == StructureState.SIDEWAYS.value:
            if not tf_4h.last_swing_high and not tf_1h.last_swing_high:
                return (
                    ThesisStatus.INSUFFICIENT_DATA,
                    "Insufficient candle data to establish structural trend across timeframes.",
                    ["Historical klines returned fewer than required swing bars."],
                )

        # Macro 4H Context
        macro_bullish = tf_4h.trend == StructureState.BULLISH.value
        macro_bearish = tf_4h.trend == StructureState.BEARISH.value

        if is_long:
            if macro_bullish:
                evidence.append(f"4H primary trend is BULLISH, aligning with LONG position.")
            elif macro_bearish:
                evidence.append(f"4H primary trend is BEARISH, fighting macro structural trend.")
            else:
                evidence.append(f"4H primary trend is SIDEWAYS/RANGING.")
        else:
            if macro_bearish:
                evidence.append(f"4H primary trend is BEARISH, aligning with SHORT position.")
            elif macro_bullish:
                evidence.append(f"4H primary trend is BULLISH, fighting macro structural trend.")
            else:
                evidence.append(f"4H primary trend is SIDEWAYS/RANGING.")

        # Local 1H Context
        local_bullish = tf_1h.trend == StructureState.BULLISH.value
        local_bearish = tf_1h.trend == StructureState.BEARISH.value

        if is_long:
            if local_bullish:
                evidence.append(f"1H local structure is BULLISH with confirmed BOS continuation.")
            elif local_bearish:
                evidence.append(f"1H local structure has broken BEARISH (bearish BOS/CHoCH observed).")
            else:
                evidence.append(f"1H local structure is neutral consolidation.")
        else:
            if local_bearish:
                evidence.append(f"1H local structure is BEARISH with confirmed supply displacement.")
            elif local_bullish:
                evidence.append(f"1H local structure has broken BULLISH (bullish BOS/CHoCH observed).")
            else:
                evidence.append(f"1H local structure is neutral consolidation.")

        # 15M Immediate Momentum Context
        if is_long:
            if tf_15m.trend == StructureState.BULLISH.value:
                evidence.append("15M short-term momentum favors buyers with higher local lows.")
            elif tf_15m.trend == StructureState.BEARISH.value:
                evidence.append("15M short-term momentum is breaking down with lower lows.")
        else:
            if tf_15m.trend == StructureState.BEARISH.value:
                evidence.append("15M short-term momentum favors sellers with aggressive sell pressure.")
            elif tf_15m.trend == StructureState.BULLISH.value:
                evidence.append("15M short-term momentum is pushing upward against the short.")

        # Current Price vs Swings
        if is_long:
            if tf_1h.last_swing_low and mark_price < tf_1h.last_swing_low:
                evidence.append(f"Mark price (${mark_price:,.4f}) has closed below 1H confirmed swing low (${tf_1h.last_swing_low:,.4f}).")
            if tf_4h.last_swing_low and mark_price < tf_4h.last_swing_low:
                evidence.append(f"Mark price (${mark_price:,.4f}) has breached 4H macro structural low (${tf_4h.last_swing_low:,.4f}).")
        else:
            if tf_1h.last_swing_high and mark_price > tf_1h.last_swing_high:
                evidence.append(f"Mark price (${mark_price:,.4f}) has closed above 1H confirmed swing high (${tf_1h.last_swing_high:,.4f}).")
            if tf_4h.last_swing_high and mark_price > tf_4h.last_swing_high:
                evidence.append(f"Mark price (${mark_price:,.4f}) has breached 4H macro structural high (${tf_4h.last_swing_high:,.4f}).")

        # Determine Classification
        if is_long:
            # Complete Invalidation: 4H or 1H broke major support
            if (macro_bearish and local_bearish) or (tf_1h.last_swing_low and mark_price < tf_1h.last_swing_low):
                status = ThesisStatus.INVALIDATED
                summary = "LONG thesis INVALIDATED: Key structural support has broken and trend structure is bearish."
            elif local_bearish or (tf_15m.trend == StructureState.BEARISH.value and mark_price < entry_price):
                status = ThesisStatus.WEAKENING
                summary = "LONG thesis WEAKENING: Bearish lower-timeframe pressure and loss of local momentum."
            else:
                status = ThesisStatus.VALID
                summary = "LONG thesis VALID: Bullish structure preserved across primary timeframes."
        else:
            # Complete Invalidation for SHORT
            if (macro_bullish and local_bullish) or (tf_1h.last_swing_high and mark_price > tf_1h.last_swing_high):
                status = ThesisStatus.INVALIDATED
                summary = "SHORT thesis INVALIDATED: Key structural resistance has broken and trend structure is bullish."
            elif local_bullish or (tf_15m.trend == StructureState.BULLISH.value and mark_price > entry_price):
                status = ThesisStatus.WEAKENING
                summary = "SHORT thesis WEAKENING: Bullish lower-timeframe pressure and loss of local downward momentum."
            else:
                status = ThesisStatus.VALID
                summary = "SHORT thesis VALID: Bearish structure preserved across primary timeframes."

        return status, summary, evidence

    @classmethod
    def _derive_recommendation(
        cls,
        side: str,
        entry_price: float,
        mark_price: float,
        thesis_status: ThesisStatus,
        risk: PositionRiskAnalysis,
        tf_4h: TimeframeStructure,
        tf_1h: TimeframeStructure,
        tf_15m: TimeframeStructure,
    ) -> Tuple[ActionRecommendation, str, List[str], List[str]]:
        """Derive conditional action, specific triggers, and explicit invalidation thresholds."""
        is_long = side == "LONG"
        triggers: List[str] = []
        invalidations: List[str] = []

        # Invalidation thresholds
        if is_long:
            inv_level = tf_1h.last_swing_low or (entry_price * 0.98)
            invalidations.append(f"A 1H candle close below ${inv_level:,.4f} completely breaks local market structure.")
            if tf_4h.last_swing_low:
                invalidations.append(f"Breach of 4H macro pivot at ${tf_4h.last_swing_low:,.4f} shifts multi-week bias to bearish.")
        else:
            inv_level = tf_1h.last_swing_high or (entry_price * 1.02)
            invalidations.append(f"A 1H candle close above ${inv_level:,.4f} completely breaks local resistance structure.")
            if tf_4h.last_swing_high:
                invalidations.append(f"Breach of 4H macro pivot at ${tf_4h.last_swing_high:,.4f} shifts multi-week bias to bullish.")

        # Urgent risk override: Unprotected position without stop loss
        if not risk.has_stop_loss:
            rec = ActionRecommendation.ADJUST
            suggested_sl = (tf_1h.last_swing_low * 0.998) if (is_long and tf_1h.last_swing_low) else (entry_price * 0.985 if is_long else entry_price * 1.015)
            summary = f"URGENT ADJUSTMENT: Position is completely unprotected. Set emergency Stop Loss at ${suggested_sl:,.4f}."
            triggers.append(f"Immediate Action: Place Stop Loss order at ${suggested_sl:,.4f} on exchange.")
            return rec, summary, triggers, invalidations

        # Urgent risk override: Critical liquidation threat
        if risk.liquidation_threat == LiquidationThreat.CRITICAL:
            rec = ActionRecommendation.CLOSE
            summary = "CRITICAL RISK CLOSE: Position is within 3% of liquidation price or SL is beyond liquidation."
            triggers.append("Execute immediate manual market close to prevent catastrophic exchange liquidation penalty.")
            return rec, summary, triggers, invalidations

        # Thesis-driven recommendations
        if thesis_status == ThesisStatus.INVALIDATED:
            rec = ActionRecommendation.CLOSE
            summary = "CLOSE RECOMMENDATION: Market structure has broken against the trade. Exit to preserve capital."
            if is_long:
                triggers.append(f"Execute market exit or limit exit on next 15M pullback toward ${mark_price:,.4f}.")
            else:
                triggers.append(f"Execute market exit or limit exit on next 15M pullback toward ${mark_price:,.4f}.")
            return rec, summary, triggers, invalidations

        if thesis_status == ThesisStatus.WEAKENING:
            rec = ActionRecommendation.REDUCE
            summary = "REDUCE RECOMMENDATION: Early warning signs of reversal. Derisk position by 50%."
            if is_long:
                triggers.append(f"If 1H candle closes below ${entry_price:,.4f}, reduce size by 50%.")
                if risk.stop_loss_price:
                    triggers.append(f"Tighten stop loss from ${risk.stop_loss_price:,.4f} to recent 15M low.")
            else:
                triggers.append(f"If 1H candle closes above ${entry_price:,.4f}, reduce size by 50%.")
                if risk.stop_loss_price:
                    triggers.append(f"Tighten stop loss from ${risk.stop_loss_price:,.4f} to recent 15M high.")
            return rec, summary, triggers, invalidations

        if thesis_status == ThesisStatus.VALID:
            # Check if profit justifies breakeven adjustment
            in_profit = (is_long and mark_price > entry_price) or (not is_long and mark_price < entry_price)
            profit_pct = abs(mark_price - entry_price) / entry_price * 100

            if in_profit and profit_pct >= 2.0:
                rec = ActionRecommendation.ADJUST
                summary = f"ADJUST TO BREAKEVEN: Position is {profit_pct:.1f}% in profit. Trail stop loss to entry (${entry_price:,.4f})."
                triggers.append(f"Move stop loss order to ${entry_price:,.4f} to eliminate downside risk.")
                if risk.take_profit_price:
                    triggers.append(f"Maintain Take Profit target at ${risk.take_profit_price:,.4f}.")
                return rec, summary, triggers, invalidations

            rec = ActionRecommendation.HOLD
            summary = "HOLD RECOMMENDATION: Structure remains intact and aligned with trade direction."
            if risk.take_profit_price:
                triggers.append(f"Target Take Profit at ${risk.take_profit_price:,.4f}.")
            if risk.stop_loss_price:
                triggers.append(f"Maintain Stop Loss at ${risk.stop_loss_price:,.4f}.")
            return rec, summary, triggers, invalidations

        # Default fallback
        return (
            ActionRecommendation.NO_ACTION,
            "NO ACTION: Insufficient structural evidence to modify position.",
            ["Monitor subsequent 1H candle close."],
            invalidations,
        )

    @classmethod
    def analyze_portfolio(
        cls,
        positions: List[Dict[str, Any]],
        account_equity: Optional[float] = None,
        available_balance: Optional[float] = None,
        candle_provider: Optional[Callable[[str, str], List[Candle]]] = None,
    ) -> PortfolioAnalysis:
        """Analyze all active positions and synthesize portfolio-level risk and net exposure."""
        positions_analyses: List[SinglePositionAnalysis] = []
        tot_notional = 0.0
        tot_margin = 0.0
        long_notional = 0.0
        short_notional = 0.0
        tot_all_in_risk = 0.0
        unprotected_count = 0
        symbol_notionals: Dict[str, float] = {}

        for p in positions:
            sym = str(p.get("symbol", "")).upper()
            side = str(p.get("side", "LONG")).upper()
            amt = abs(float(p.get("position_amt", p.get("positionAmt", 0.0))))
            mark_p = float(p.get("mark_price", p.get("markPrice", 0.0)))
            notional = amt * mark_p
            tot_notional += notional

            alloc_m = float(p.get("allocated_margin_usdt", p.get("isolatedMargin", 0.0)))
            tot_margin += alloc_m

            symbol_notionals[sym] = symbol_notionals.get(sym, 0.0) + notional

            if side == "LONG":
                long_notional += notional
            else:
                short_notional += notional

            # Fetch candles if provider supplied
            c_4h = candle_provider(sym, "4h") if candle_provider else None
            c_1h = candle_provider(sym, "1h") if candle_provider else None
            c_15m = candle_provider(sym, "15m") if candle_provider else None

            single_analysis = cls.analyze_position(
                position=p,
                candles_4h=c_4h,
                candles_1h=c_1h,
                candles_15m=c_15m,
                account_equity=account_equity,
            )
            positions_analyses.append(single_analysis)

            if single_analysis.risk.all_in_loss_at_sl_usdt:
                tot_all_in_risk += single_analysis.risk.all_in_loss_at_sl_usdt
            else:
                unprotected_count += 1

        # Exposure metrics
        net_exposure = long_notional - short_notional
        if abs(net_exposure) < 1e-6:
            bias = "NEUTRAL"
        elif net_exposure > 0:
            bias = "NET_LONG"
        else:
            bias = "NET_SHORT"

        margin_util = (tot_margin / account_equity * 100) if (account_equity and account_equity > 0) else 0.0
        port_risk_eq = (tot_all_in_risk / account_equity * 100) if (account_equity and account_equity > 0) else None

        # Warnings
        conc_warnings: List[str] = []
        corr_warnings: List[str] = []

        if tot_notional > 0:
            for s, notional_amt in symbol_notionals.items():
                pct = (notional_amt / tot_notional) * 100
                if pct > 60.0 and len(symbol_notionals) > 1:
                    conc_warnings.append(f"Concentration Risk: {s} represents {pct:.1f}% of total portfolio notional.")

        if len(positions) >= 2:
            all_long = all(p.get("side") == "LONG" for p in positions)
            all_short = all(p.get("side") == "SHORT" for p in positions)
            if all_long:
                corr_warnings.append("Directional Correlation: 100% of open positions are LONG. High market beta exposure.")
            elif all_short:
                corr_warnings.append("Directional Correlation: 100% of open positions are SHORT. Vulnerable to market-wide short squeeze.")

        # Health status
        if unprotected_count > 0:
            health = "CRITICAL_ACTION_REQUIRED"
            summary = f"Alert: {unprotected_count} position(s) lack stop-loss protection. Immediate adjustment required."
        elif margin_util > 75.0:
            health = "ELEVATED_RISK"
            summary = f"Elevated Risk: Margin utilization is high ({margin_util:.1f}%). Buffer against volatility is thin."
        elif port_risk_eq and port_risk_eq > 10.0:
            health = "MODERATE_RISK"
            summary = f"Moderate Risk: Total stop risk represents {port_risk_eq:.1f}% of account equity."
        else:
            health = "HEALTHY"
            summary = "Portfolio health is balanced with active risk controls in place."

        return PortfolioAnalysis(
            total_equity_usdt=round(account_equity, 2) if account_equity is not None else None,
            available_balance_usdt=round(available_balance, 2) if available_balance is not None else None,
            open_positions_count=len(positions),
            total_notional_usdt=round(tot_notional, 2),
            total_margin_committed_usdt=round(tot_margin, 2),
            margin_utilization_pct=round(margin_util, 2),
            long_notional_usdt=round(long_notional, 2),
            short_notional_usdt=round(short_notional, 2),
            net_directional_bias=bias,
            net_exposure_usdt=round(abs(net_exposure), 2),
            total_all_in_risk_at_stops_usdt=round(tot_all_in_risk, 2),
            portfolio_risk_equity_pct=port_risk_eq,
            unprotected_positions_count=unprotected_count,
            concentration_warnings=conc_warnings,
            correlation_warnings=corr_warnings,
            portfolio_health_status=health,
            portfolio_action_summary=summary,
            positions_analyses=positions_analyses,
        )
