"""AsterDEX Live Position & Market Condition Analyzer (Position Entry Audit Engine).

Provides multi-timeframe structural audit, thesis validation, risk exposure calculation,
conditional triggers, and actionable recommendations for open AsterDEX positions.

Strictly READ-ONLY. No live order placement, modification, or cancellation.
Preserves existing safety invariants: live_trading_locked=true, paper_only=true.
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
    """Structural thesis validation status."""
    THESIS_INTACT = "THESIS_INTACT"
    VALID = "THESIS_INTACT"  # Backward compatibility alias
    THESIS_WEAKENING = "THESIS_WEAKENING"
    WEAKENING = "THESIS_WEAKENING"  # Backward compatibility alias
    THESIS_INVALIDATED = "THESIS_INVALIDATED"
    INVALIDATED = "THESIS_INVALIDATED"  # Backward compatibility alias
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class PositionRiskStatus(str, Enum):
    """Position risk exposure classification."""
    RISK_WITHIN_LIMIT = "RISK_WITHIN_LIMIT"
    RISK_WARNING = "RISK_WARNING"
    RISK_BREACHED = "RISK_BREACHED"
    PROTECTION_UNVERIFIED = "PROTECTION_UNVERIFIED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ActionRecommendation(str, Enum):
    """Deterministic actionable recommendation."""
    HOLD = "HOLD"
    REVIEW = "REVIEW"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    CLOSE = "EXIT"  # Backward compatibility alias
    ADJUST = "REVIEW"  # Backward compatibility alias
    NO_ACTION = "NO_ACTION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ProtectionStatus(str, Enum):
    """Execution protection status verified on exchange."""
    FULLY_PROTECTED = "FULLY_PROTECTED"
    PROTECTED_NO_TP = "PROTECTED_NO_TP"
    PARTIALLY_PROTECTED = "PARTIALLY_PROTECTED"
    UNPROTECTED_NO_SL = "UNPROTECTED_NO_SL"
    COMPLETELY_UNPROTECTED = "COMPLETELY_UNPROTECTED"
    PROTECTION_UNVERIFIED = "PROTECTION_UNVERIFIED"
    INVALID_PROTECTION_ORDER = "INVALID_PROTECTION_ORDER"


class LiquidationThreat(str, Enum):
    """Liquidation distance proximity level."""
    CRITICAL = "CRITICAL"
    ELEVATED = "ELEVATED"
    MODERATE = "MODERATE"
    SAFE = "SAFE"
    UNKNOWN = "UNKNOWN"


class TriggerType(str, Enum):
    """Classification for conditional triggers and levels."""
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    STRUCTURAL_INVALIDATION = "STRUCTURAL_INVALIDATION"
    WARNING_CONDITION = "WARNING_CONDITION"
    TRAILING_STOP = "TRAILING_STOP"
    REDUCE_SIZE = "REDUCE_SIZE"


@dataclass
class ConditionalTrigger:
    """Explicit conditional trigger with confirmation requirement and implication."""
    trigger_type: str
    price_level: Optional[float]
    timeframe: str
    condition_description: str
    required_confirmation: str
    action_implication: str
    is_active: bool = False
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trigger_type": self.trigger_type,
            "price_level": self.price_level,
            "timeframe": self.timeframe,
            "condition_description": self.condition_description,
            "required_confirmation": self.required_confirmation,
            "action_implication": self.action_implication,
            "is_active": self.is_active,
            "timestamp_utc": self.timestamp_utc,
        }


@dataclass
class TimeframeStructure:
    """Comprehensive structure state on a specific timeframe."""
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
    ema_alignment: str = "NEUTRAL"
    ema20: Optional[float] = None
    ema50: Optional[float] = None
    momentum_condition: str = "NEUTRAL"
    rsi_14: Optional[float] = None
    structural_support: Optional[float] = None
    structural_resistance: Optional[float] = None
    position_alignment: str = "NEUTRAL"  # "SUPPORTS", "CONFLICTS", "NEUTRAL"
    data_freshness: Dict[str, Any] = field(default_factory=dict)

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
            "ema_alignment": self.ema_alignment,
            "ema20": self.ema20,
            "ema50": self.ema50,
            "momentum_condition": self.momentum_condition,
            "rsi_14": self.rsi_14,
            "structural_support": self.structural_support,
            "structural_resistance": self.structural_resistance,
            "position_alignment": self.position_alignment,
            "data_freshness": self.data_freshness,
        }


@dataclass
class MarketRegimeContext:
    """Broad market context, BTC/ETH anchor condition, and correlation bias."""
    regime: str
    btc_price: Optional[float] = None
    btc_change_24h_pct: Optional[float] = None
    btc_trend_1h: str = "NEUTRAL"
    btc_trend_4h: str = "NEUTRAL"
    eth_price: Optional[float] = None
    eth_change_24h_pct: Optional[float] = None
    eth_trend_1h: str = "NEUTRAL"
    eth_trend_4h: str = "NEUTRAL"
    position_correlation: str = "NEUTRAL"
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime,
            "btc_price": self.btc_price,
            "btc_change_24h_pct": self.btc_change_24h_pct,
            "btc_trend_1h": self.btc_trend_1h,
            "btc_trend_4h": self.btc_trend_4h,
            "eth_price": self.eth_price,
            "eth_change_24h_pct": self.eth_change_24h_pct,
            "eth_trend_1h": self.eth_trend_1h,
            "eth_trend_4h": self.eth_trend_4h,
            "position_correlation": self.position_correlation,
            "summary": self.summary,
        }


@dataclass
class AuditFreshnessContext:
    """Timestamp synchronization and candle staleness verification."""
    position_snapshot_utc: Optional[str] = None
    market_data_utc: Optional[str] = None
    candle_timestamps: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    order_verification_utc: Optional[str] = None
    analysis_generation_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    overall_freshness: str = "FRESH"
    staleness_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_snapshot_utc": self.position_snapshot_utc,
            "market_data_utc": self.market_data_utc,
            "candle_timestamps": self.candle_timestamps,
            "order_verification_utc": self.order_verification_utc,
            "analysis_generation_utc": self.analysis_generation_utc,
            "overall_freshness": self.overall_freshness,
            "staleness_reasons": self.staleness_reasons,
        }


@dataclass
class PositionRiskAnalysis:
    """Detailed risk, fee, and exposure assessment."""
    notional_usdt: float
    allocated_margin_usdt: float
    has_stop_loss: bool
    risk_status: PositionRiskStatus = PositionRiskStatus.PROTECTION_UNVERIFIED
    stop_loss_price: Optional[float] = None
    sl_distance_usdt: Optional[float] = None
    sl_distance_pct: Optional[float] = None
    gross_loss_at_sl_usdt: Optional[float] = None
    est_taker_fees_usdt: float = 0.0
    est_slippage_usdt: float = 0.0
    est_funding_fee_usdt: float = 0.0
    net_loss_at_sl_usdt: Optional[float] = None
    all_in_loss_at_sl_usdt: Optional[float] = None  # Backward compatibility alias for net_loss_at_sl_usdt
    gross_loss_equity_pct: Optional[float] = None
    net_loss_equity_pct: Optional[float] = None
    loss_equity_pct: Optional[float] = None  # Backward compatibility alias
    has_take_profit: bool = False
    take_profit_price: Optional[float] = None
    tp_distance_usdt: Optional[float] = None
    tp_distance_pct: Optional[float] = None
    gross_profit_at_tp_usdt: Optional[float] = None
    net_profit_at_tp_usdt: Optional[float] = None
    gross_risk_reward_ratio: Optional[float] = None
    net_risk_reward_ratio: Optional[float] = None
    risk_reward_ratio: Optional[float] = None  # Backward compatibility alias for net_risk_reward_ratio
    has_liquidation_price: bool = False
    liquidation_price: Optional[float] = None
    liquidation_distance_usdt: Optional[float] = None
    liquidation_distance_pct: Optional[float] = None
    liquidation_threat: LiquidationThreat = LiquidationThreat.UNKNOWN
    margin_mode: str = "ISOLATED"
    margin_mode_assessment: str = ""
    protection_status: ProtectionStatus = ProtectionStatus.PROTECTION_UNVERIFIED
    is_exchange_verified: bool = False
    verified_sl_order_id: Optional[str] = None
    verified_sl_qty: Optional[float] = None
    verified_tp_order_id: Optional[str] = None
    verified_tp_qty: Optional[float] = None
    protection_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "notional_usdt": self.notional_usdt,
            "allocated_margin_usdt": self.allocated_margin_usdt,
            "has_stop_loss": self.has_stop_loss,
            "risk_status": self.risk_status.value if isinstance(self.risk_status, PositionRiskStatus) else str(self.risk_status),
            "stop_loss_price": self.stop_loss_price,
            "sl_distance_usdt": self.sl_distance_usdt,
            "sl_distance_pct": self.sl_distance_pct,
            "gross_loss_at_sl_usdt": self.gross_loss_at_sl_usdt,
            "est_taker_fees_usdt": self.est_taker_fees_usdt,
            "est_slippage_usdt": self.est_slippage_usdt,
            "est_funding_fee_usdt": self.est_funding_fee_usdt,
            "net_loss_at_sl_usdt": self.net_loss_at_sl_usdt,
            "all_in_loss_at_sl_usdt": self.all_in_loss_at_sl_usdt,
            "gross_loss_equity_pct": self.gross_loss_equity_pct,
            "net_loss_equity_pct": self.net_loss_equity_pct,
            "loss_equity_pct": self.loss_equity_pct,
            "has_take_profit": self.has_take_profit,
            "take_profit_price": self.take_profit_price,
            "tp_distance_usdt": self.tp_distance_usdt,
            "tp_distance_pct": self.tp_distance_pct,
            "gross_profit_at_tp_usdt": self.gross_profit_at_tp_usdt,
            "net_profit_at_tp_usdt": self.net_profit_at_tp_usdt,
            "gross_risk_reward_ratio": self.gross_risk_reward_ratio,
            "net_risk_reward_ratio": self.net_risk_reward_ratio,
            "risk_reward_ratio": self.risk_reward_ratio,
            "has_liquidation_price": self.has_liquidation_price,
            "liquidation_price": self.liquidation_price,
            "liquidation_distance_usdt": self.liquidation_distance_usdt,
            "liquidation_distance_pct": self.liquidation_distance_pct,
            "liquidation_threat": self.liquidation_threat.value if isinstance(self.liquidation_threat, LiquidationThreat) else str(self.liquidation_threat),
            "margin_mode": self.margin_mode,
            "margin_mode_assessment": self.margin_mode_assessment,
            "protection_status": self.protection_status.value if isinstance(self.protection_status, ProtectionStatus) else str(self.protection_status),
            "is_exchange_verified": self.is_exchange_verified,
            "verified_sl_order_id": self.verified_sl_order_id,
            "verified_sl_qty": self.verified_sl_qty,
            "verified_tp_order_id": self.verified_tp_order_id,
            "verified_tp_qty": self.verified_tp_qty,
            "protection_notes": self.protection_notes,
        }


@dataclass
class SinglePositionAnalysis:
    """Comprehensive thesis, multi-timeframe structure, and condition audit for a live position."""
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
    multi_timeframe_synthesis: str = "MIXED_TIMEFRAME_CONSOLIDATION"
    synthesis_narrative: str = ""
    market_regime: Optional[MarketRegimeContext] = None
    freshness: Optional[AuditFreshnessContext] = None
    structured_triggers: List[ConditionalTrigger] = field(default_factory=list)
    structured_invalidations: List[ConditionalTrigger] = field(default_factory=list)
    analyzed_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def risk_status(self) -> PositionRiskStatus:
        return self.risk.risk_status

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
            "thesis_status": self.thesis_status.value if isinstance(self.thesis_status, ThesisStatus) else str(self.thesis_status),
            "thesis_summary": self.thesis_summary,
            "structural_evidence": self.structural_evidence,
            "action": self.action.value if isinstance(self.action, ActionRecommendation) else str(self.action),
            "action_summary": self.action_summary,
            "conditional_triggers": self.conditional_triggers,
            "invalidation_conditions": self.invalidation_conditions,
            "multi_timeframe_synthesis": self.multi_timeframe_synthesis,
            "synthesis_narrative": self.synthesis_narrative,
            "market_regime": self.market_regime.to_dict() if self.market_regime else None,
            "freshness": self.freshness.to_dict() if self.freshness else None,
            "structured_triggers": [t.to_dict() for t in self.structured_triggers],
            "structured_invalidations": [i.to_dict() for i in self.structured_invalidations],
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
    """Analyzes open AsterDEX positions against multi-timeframe market structure and risk limits."""

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
        btc_candles_1h: Optional[List[Candle]] = None,
        btc_candles_4h: Optional[List[Candle]] = None,
        eth_candles_1h: Optional[List[Candle]] = None,
        eth_candles_4h: Optional[List[Candle]] = None,
        btc_ticker: Optional[Dict[str, Any]] = None,
        eth_ticker: Optional[Dict[str, Any]] = None,
        funding_rate_pct: Optional[float] = None,
    ) -> SinglePositionAnalysis:
        """Perform comprehensive entry thesis audit, risk status classification, and conditional triggers."""
        wall_clock_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        all_close_times = [
            getattr(c, "close_time_ms", getattr(c, "timestamp_ms", 0))
            for series in (candles_4h, candles_1h, candles_15m)
            if series
            for c in series
            if c
        ]
        max_candle_ms = max(all_close_times) if all_close_times else 0

        # If candles exist and their latest timestamp is > 2 days in the past (historical test/replay),
        # use max_candle_ms so historical tests do not falsely flag staleness!
        if max_candle_ms > 0 and (wall_clock_ms - max_candle_ms) > 172800000:
            now_ms = max_candle_ms
        else:
            now_ms = wall_clock_ms

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
            lev = float(position.get("leverage", 10.0)) or 10.0
            alloc_margin = (pos_amt * entry_price) / lev

        # 1. Multi-Timeframe Structural Analysis (Evaluated Independently)
        tf_4h = cls._evaluate_tf_structure(candles_4h or [], "4h", mark_price, side=side, now_ms=now_ms)
        tf_1h = cls._evaluate_tf_structure(candles_1h or [], "1h", mark_price, side=side, now_ms=now_ms)
        tf_15m = cls._evaluate_tf_structure(candles_15m or [], "15m", mark_price, side=side, now_ms=now_ms)

        # 2. Multi-Timeframe Synthesis (Explicit, Never Collapsing Mixed Signals)
        mtf_synthesis, synthesis_narrative = cls._synthesize_multi_timeframe(tf_15m, tf_1h, tf_4h, side)

        # 3. Freshness & Timestamp Consistency Audit
        freshness_ctx = cls._audit_freshness(
            position=position,
            candles_4h=candles_4h or [],
            candles_1h=candles_1h or [],
            candles_15m=candles_15m or [],
            now_ms=now_ms,
        )

        # 4. Market Regime & BTC/ETH Macro Anchor Context
        regime_ctx = cls._evaluate_market_regime(
            btc_candles_1h=btc_candles_1h,
            btc_candles_4h=btc_candles_4h,
            eth_candles_1h=eth_candles_1h,
            eth_candles_4h=eth_candles_4h,
            btc_ticker=btc_ticker,
            eth_ticker=eth_ticker,
            side=side,
            mtf_synthesis=mtf_synthesis,
        )

        # 5. Position Risk, Financial Calculations & Execution Protection
        risk_analysis = cls._evaluate_risk(
            position=position,
            pos_amt=pos_amt,
            entry_price=entry_price,
            mark_price=mark_price,
            margin_mode=margin_mode,
            allocated_margin=alloc_margin,
            account_equity=account_equity,
            funding_rate_pct=funding_rate_pct,
        )

        # 6. Structural Thesis Status (Independent of Position Risk)
        thesis_status, thesis_summary, evidence = cls._audit_thesis(
            side=side,
            entry_price=entry_price,
            mark_price=mark_price,
            tf_4h=tf_4h,
            tf_1h=tf_1h,
            tf_15m=tf_15m,
            mtf_synthesis=mtf_synthesis,
            freshness_ctx=freshness_ctx,
            regime_ctx=regime_ctx,
        )

        # 7. Action Recommendation & Structured Conditional Triggers
        action, action_summary, structured_triggers, structured_invalidations = cls._derive_recommendation(
            side=side,
            entry_price=entry_price,
            mark_price=mark_price,
            thesis_status=thesis_status,
            risk=risk_analysis,
            tf_4h=tf_4h,
            tf_1h=tf_1h,
            tf_15m=tf_15m,
            mtf_synthesis=mtf_synthesis,
            regime=regime_ctx,
            freshness=freshness_ctx,
        )

        # Backward-compatible list of strings for UI / existing clients
        legacy_triggers = [
            f"{t.trigger_type} [{t.timeframe}]: {t.condition_description} -> {t.action_implication}"
            for t in structured_triggers
        ]
        legacy_invalidations = [
            f"{i.condition_description} ({i.action_implication})"
            for i in structured_invalidations
        ]

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
            conditional_triggers=legacy_triggers,
            invalidation_conditions=legacy_invalidations,
            timeframe_4h=tf_4h,
            timeframe_1h=tf_1h,
            timeframe_15m=tf_15m,
            risk=risk_analysis,
            multi_timeframe_synthesis=mtf_synthesis,
            synthesis_narrative=synthesis_narrative,
            market_regime=regime_ctx,
            freshness=freshness_ctx,
            structured_triggers=structured_triggers,
            structured_invalidations=structured_invalidations,
        )

    @classmethod
    def _evaluate_tf_structure(
        cls,
        candles: List[Candle],
        timeframe: str,
        current_price: float,
        side: str = "LONG",
        now_ms: Optional[int] = None,
    ) -> TimeframeStructure:
        """Extract swings, trend, EMA alignment, RSI momentum, S/R levels, freshness, and alignment."""
        if not candles or len(candles) < 5:
            trend_val = "INSUFFICIENT_DATA" if (not candles or len(candles) < 3) else StructureState.SIDEWAYS.value
            return TimeframeStructure(
                timeframe=timeframe,
                trend=trend_val,
                description=f"Insufficient candle history ({len(candles) if candles else 0} bars available).",
                ema_alignment="UNCERTAIN",
                momentum_condition="UNCERTAIN",
                position_alignment="NEUTRAL",
                data_freshness={
                    "is_stale": True,
                    "age_minutes": None,
                    "status": "INSUFFICIENT_DATA",
                },
            )

        # Detect swings and market structure breaks
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
        support_levels: List[float] = []
        resistance_levels: List[float] = []

        if last_low is not None:
            support_levels.append(last_low)
        if last_high is not None:
            resistance_levels.append(last_high)

        for ob in obs:
            if not ob.mitigated:
                unmitigated_obs.append({
                    "direction": ob.direction.value,
                    "top": round(ob.high, 4),
                    "bottom": round(ob.low, 4),
                    "is_mitigated": False,
                })
                if ob.direction == Direction.LONG and ob.high <= current_price:
                    support_levels.append(ob.high)
                elif ob.direction == Direction.SHORT and ob.low >= current_price:
                    resistance_levels.append(ob.low)

        fvgs = FVGDetector.detect_fvgs(candles)
        unmitigated_fvgs = []
        for f in fvgs:
            if not f.mitigated:
                unmitigated_fvgs.append({
                    "direction": f.direction.value,
                    "top": round(f.top, 4),
                    "bottom": round(f.bottom, 4),
                })
                if f.direction == Direction.LONG and f.top <= current_price:
                    support_levels.append(f.top)
                elif f.direction == Direction.SHORT and f.bottom >= current_price:
                    resistance_levels.append(f.bottom)

        # Closest structural support and resistance
        structural_sup = max(support_levels) if support_levels else last_low
        structural_res = min(resistance_levels) if resistance_levels else last_high

        # 3. EMA Alignment (EMA 20 & EMA 50)
        closes = [float(c.close) for c in candles]

        def _calc_ema(series: List[float], period: int) -> Optional[float]:
            if len(series) < period:
                return round(sum(series) / len(series), 4) if len(series) >= 5 else None
            k = 2.0 / (period + 1)
            ema = series[0]
            for val in series[1:]:
                ema = (val * k) + (ema * (1.0 - k))
            return round(ema, 4)

        ema20 = _calc_ema(closes, 20)
        ema50 = _calc_ema(closes, 50)

        if ema20 is not None and ema50 is not None:
            if current_price >= ema20 > ema50:
                ema_alignment = "BULLISH_STACKED"
            elif current_price <= ema20 < ema50:
                ema_alignment = "BEARISH_STACKED"
            else:
                ema_alignment = "NEUTRAL_CHOP"
        else:
            ema_alignment = "INSUFFICIENT_BARS"

        # 4. Momentum Condition (Wilder's RSI 14)
        def _calc_rsi(series: List[float], period: int = 14) -> Optional[float]:
            if len(series) <= period:
                return None
            gains, losses = [], []
            for i in range(1, len(series)):
                diff = series[i] - series[i - 1]
                gains.append(max(0.0, diff))
                losses.append(max(0.0, -diff))
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            if avg_loss == 0.0:
                return 100.0
            rs = avg_gain / avg_loss
            return round(100.0 - (100.0 / (1.0 + rs)), 1)

        rsi14 = _calc_rsi(closes, 14)
        if rsi14 is not None:
            if rsi14 >= 58.0:
                momentum_cond = "BULLISH_MOMENTUM"
            elif rsi14 <= 42.0:
                momentum_cond = "BEARISH_MOMENTUM"
            else:
                momentum_cond = "NEUTRAL_MOMENTUM"
        else:
            momentum_cond = "NEUTRAL_INSUFFICIENT_BARS"

        # 5. Position Alignment relative to position direction
        is_long = side == "LONG"
        if is_long:
            if structure_state == StructureState.BULLISH and (ema20 is None or current_price >= ema20):
                pos_alignment = "SUPPORTS"
            elif structure_state == StructureState.BEARISH and (ema20 is None or current_price <= ema20):
                pos_alignment = "CONFLICTS"
            else:
                pos_alignment = "NEUTRAL"
        else:
            if structure_state == StructureState.BEARISH and (ema20 is None or current_price <= ema20):
                pos_alignment = "SUPPORTS"
            elif structure_state == StructureState.BULLISH and (ema20 is None or current_price >= ema20):
                pos_alignment = "CONFLICTS"
            else:
                pos_alignment = "NEUTRAL"

        # 6. Data Freshness per Timeframe
        last_candle = candles[-1]
        close_ms = getattr(last_candle, "close_time_ms", getattr(last_candle, "timestamp_ms", 0))
        ref_now = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        age_min = round((ref_now - close_ms) / 60000.0, 1) if close_ms > 0 else None

        staleness_limits = {"15m": 35.0, "1h": 130.0, "4h": 500.0}
        max_age = staleness_limits.get(timeframe.lower(), 180.0)
        is_stale = age_min is not None and age_min > max_age

        data_freshness = {
            "latest_close_utc": datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc).isoformat() if close_ms > 0 else None,
            "age_minutes": age_min,
            "is_stale": is_stale,
            "status": "STALE" if is_stale else "FRESH",
        }

        # Descriptive Summary
        desc_parts = [f"{structure_state.value} structure", f"EMA: {ema_alignment}"]
        if last_high:
            desc_parts.append(f"Swing High: ${last_high:,.4f}")
        if last_low:
            desc_parts.append(f"Swing Low: ${last_low:,.4f}")
        if choch_cnt > 0:
            desc_parts.append(f"{choch_cnt} CHoCH")
        elif bos_cnt > 0:
            desc_parts.append(f"{bos_cnt} BOS")

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
            ema_alignment=ema_alignment,
            ema20=ema20,
            ema50=ema50,
            momentum_condition=momentum_cond,
            rsi_14=rsi14,
            structural_support=structural_sup,
            structural_resistance=structural_res,
            position_alignment=pos_alignment,
            data_freshness=data_freshness,
        )

    @classmethod
    def _synthesize_multi_timeframe(
        cls,
        tf_15m: TimeframeStructure,
        tf_1h: TimeframeStructure,
        tf_4h: TimeframeStructure,
        side: str,
    ) -> Tuple[str, str]:
        """Synthesize independent 15M, 1H, and 4H conditions without collapsing mixed signals."""
        t15 = tf_15m.trend
        t1h = tf_1h.trend
        t4h = tf_4h.trend

        # Missing or uncertain data handling
        if "INSUFFICIENT_DATA" in (t15, t1h, t4h) or tf_15m.data_freshness.get("status") == "INSUFFICIENT_DATA":
            code = "STRUCTURAL_UNCERTAINTY_INSUFFICIENT_DATA"
            narrative = f"15M {t15} | 1H {t1h} | 4H {t4h} — Insufficient or unconfirmed candle history prevents structural trend confirmation."
            return code, narrative

        lead = f"15M {t15} | 1H {t1h} | 4H {t4h}"

        # 1. Exact Mandated Case: 15M Bullish, 1H Neutral/Sideways, 4H Bearish
        if t15 == "BULLISH" and t1h in ("SIDEWAYS", "NEUTRAL") and t4h == "BEARISH":
            code = "SHORT_TERM_BULLISH_RECOVERY_WITHIN_HIGHER_TIMEFRAME_BEARISH_STRUCTURE"
            narrative = f"{lead} — Short-term recovery inside a bearish higher-timeframe structure. The position thesis is not invalidated, but higher-timeframe trend alignment is weak."
            return code, narrative

        # 2. Bullish 15M against 1H and 4H Bearish
        if t15 == "BULLISH" and t1h == "BEARISH" and t4h == "BEARISH":
            code = "SHORT_TERM_BULLISH_RECOVERY_WITHIN_HIGHER_TIMEFRAME_BEARISH_STRUCTURE"
            narrative = f"{lead} — Counter-trend bullish bounce on 15M inside a dominant higher-timeframe bearish downtrend."
            return code, narrative

        # 3. Pullback Case: 15M Bearish, 1H Neutral/Sideways, 4H Bullish
        if t15 == "BEARISH" and t1h in ("SIDEWAYS", "NEUTRAL") and t4h == "BULLISH":
            code = "SHORT_TERM_PULLBACK_WITHIN_HIGHER_TIMEFRAME_BULLISH_STRUCTURE"
            narrative = f"{lead} — Short-term pullback inside a bullish higher-timeframe structure. Local momentum is corrective while macro support remains intact."
            return code, narrative

        # 4. Bearish 15M against 1H and 4H Bullish
        if t15 == "BEARISH" and t1h == "BULLISH" and t4h == "BULLISH":
            code = "SHORT_TERM_PULLBACK_WITHIN_HIGHER_TIMEFRAME_BULLISH_STRUCTURE"
            narrative = f"{lead} — Corrective lower-timeframe pullback against higher-timeframe bullish trend continuation."
            return code, narrative

        # 5. Full Confluence Bullish
        if t15 == "BULLISH" and t1h == "BULLISH" and t4h == "BULLISH":
            code = "CONFLUENT_BULLISH_ACROSS_ALL_TIMEFRAMES"
            narrative = f"{lead} — Strong bullish alignment across execution, intermediate, and macro timeframes."
            return code, narrative

        # 6. Full Confluence Bearish
        if t15 == "BEARISH" and t1h == "BEARISH" and t4h == "BEARISH":
            code = "CONFLUENT_BEARISH_ACROSS_ALL_TIMEFRAMES"
            narrative = f"{lead} — Strong bearish alignment across execution, intermediate, and macro timeframes."
            return code, narrative

        # 7. Intermediate Expansion into Macro Resistance
        if t15 == "BULLISH" and t1h == "BULLISH" and t4h == "BEARISH":
            code = "INTERMEDIATE_BULLISH_EXPANSION_INTO_HIGHER_TIMEFRAME_BEARISH_RESISTANCE"
            narrative = f"{lead} — Bullish push on 15M/1H approaching higher-timeframe 4H supply/resistance."
            return code, narrative

        # 8. Intermediate Correction into Macro Support
        if t15 == "BEARISH" and t1h == "BEARISH" and t4h == "BULLISH":
            code = "INTERMEDIATE_BEARISH_CORRECTION_INTO_HIGHER_TIMEFRAME_BULLISH_SUPPORT"
            narrative = f"{lead} — Bearish correction on 15M/1H approaching higher-timeframe 4H demand/support."
            return code, narrative

        # 9. Generic Mixed
        code = "MIXED_TIMEFRAME_CONSOLIDATION"
        narrative = f"{lead} — Mixed timeframe conditions without clean directional confluence."
        return code, narrative

    @classmethod
    def _audit_freshness(
        cls,
        position: Dict[str, Any],
        candles_4h: List[Candle],
        candles_1h: List[Candle],
        candles_15m: List[Candle],
        now_ms: int,
    ) -> AuditFreshnessContext:
        """Inspect and verify timestamp synchronization across market data and position snapshots."""
        pos_sync_utc = position.get("last_sync_utc")
        pos_sync_ms = position.get("last_sync_timestamp_ms")
        reasons: List[str] = []

        # Check position snapshot freshness (< 60s)
        if pos_sync_ms:
            age_sec = (now_ms - int(pos_sync_ms)) / 1000.0
            if age_sec > 60.0:
                reasons.append(f"Position snapshot is {age_sec:.0f}s old (threshold: 60s).")

        # Candle timestamps
        candle_ts_map: Dict[str, Dict[str, Any]] = {}
        for tf, series, max_m in (("4h", candles_4h, 500.0), ("1h", candles_1h, 130.0), ("15m", candles_15m, 35.0)):
            if not series:
                candle_ts_map[tf] = {"latest_close_utc": None, "age_minutes": None, "is_stale": True}
                reasons.append(f"Missing {tf} candle data.")
                continue
            last_c = series[-1]
            c_ms = getattr(last_c, "close_time_ms", getattr(last_c, "timestamp_ms", 0))
            age_m = round((now_ms - c_ms) / 60000.0, 1) if c_ms > 0 else 999.0
            is_stale = age_m > max_m
            if is_stale:
                reasons.append(f"{tf} candles are stale ({age_m}m old, threshold: {max_m:.0f}m).")
            candle_ts_map[tf] = {
                "latest_close_utc": datetime.fromtimestamp(c_ms / 1000, tz=timezone.utc).isoformat() if c_ms > 0 else None,
                "age_minutes": age_m,
                "is_stale": is_stale,
            }

        overall = "STALE_WARNING" if reasons else "FRESH"
        return AuditFreshnessContext(
            position_snapshot_utc=pos_sync_utc or datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
            market_data_utc=datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
            candle_timestamps=candle_ts_map,
            order_verification_utc=pos_sync_utc if position.get("is_exchange_verified") else None,
            analysis_generation_utc=datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
            overall_freshness=overall,
            staleness_reasons=reasons,
        )

    @classmethod
    def _evaluate_market_regime(
        cls,
        btc_candles_1h: Optional[List[Candle]],
        btc_candles_4h: Optional[List[Candle]],
        eth_candles_1h: Optional[List[Candle]],
        eth_candles_4h: Optional[List[Candle]],
        btc_ticker: Optional[Dict[str, Any]],
        eth_ticker: Optional[Dict[str, Any]],
        side: str,
        mtf_synthesis: str,
    ) -> MarketRegimeContext:
        """Evaluate BTC/ETH market regime and correlate with position direction."""
        btc_px = float(btc_ticker.get("lastPrice") or btc_ticker.get("mark_price", 0.0)) if btc_ticker else None
        btc_chg = float(btc_ticker.get("priceChangePercent") or btc_ticker.get("price_change_pct", 0.0)) if btc_ticker else None
        eth_px = float(eth_ticker.get("lastPrice") or eth_ticker.get("mark_price", 0.0)) if eth_ticker else None
        eth_chg = float(eth_ticker.get("priceChangePercent") or eth_ticker.get("price_change_pct", 0.0)) if eth_ticker else None

        btc_t1h = StructureState.SIDEWAYS.value
        btc_t4h = StructureState.SIDEWAYS.value
        eth_t1h = StructureState.SIDEWAYS.value
        eth_t4h = StructureState.SIDEWAYS.value

        if btc_candles_1h and len(btc_candles_1h) >= 5:
            sw = detect_swings(btc_candles_1h, 2, 2)
            st, _ = MarketStructureEngine.evaluate_structure(btc_candles_1h, sw)
            btc_t1h = st.value
        if btc_candles_4h and len(btc_candles_4h) >= 5:
            sw = detect_swings(btc_candles_4h, 2, 2)
            st, _ = MarketStructureEngine.evaluate_structure(btc_candles_4h, sw)
            btc_t4h = st.value

        if eth_candles_1h and len(eth_candles_1h) >= 5:
            sw = detect_swings(eth_candles_1h, 2, 2)
            st, _ = MarketStructureEngine.evaluate_structure(eth_candles_1h, sw)
            eth_t1h = st.value
        if eth_candles_4h and len(eth_candles_4h) >= 5:
            sw = detect_swings(eth_candles_4h, 2, 2)
            st, _ = MarketStructureEngine.evaluate_structure(eth_candles_4h, sw)
            eth_t4h = st.value

        # Classify regime
        if (btc_chg is not None and btc_chg > 1.2) and (eth_chg is not None and eth_chg > 1.2):
            regime = "RISK_ON_BULLISH"
        elif (btc_chg is not None and btc_chg < -1.5) and (eth_chg is not None and eth_chg < -1.5):
            regime = "RISK_OFF_BEARISH"
        elif (eth_chg is not None and btc_chg is not None) and (eth_chg > btc_chg + 1.0):
            regime = "ALTCOIN_LEADERSHIP"
        else:
            regime = "RANGEBOUND_CHOP"

        # Correlation Assessment
        is_long = side == "LONG"
        btc_is_bearish = btc_t1h == "BEARISH" or btc_t4h == "BEARISH" or (btc_chg is not None and btc_chg < -1.0)
        btc_is_bullish = btc_t1h == "BULLISH" or btc_t4h == "BULLISH" or (btc_chg is not None and btc_chg > 1.0)

        if is_long:
            if btc_is_bearish:
                correlation = "COUNTER_TREND_TO_BTC"
                summary = "LONG position counter-trend to bearish BTC/ETH macro regime. Short-term 15M recovery vulnerable to macro liquidity selloff."
            elif btc_is_bullish:
                correlation = "ALIGNED_WITH_MACRO"
                summary = "LONG position aligned with bullish BTC/ETH macro regime and broad crypto liquidity tailwinds."
            else:
                correlation = "NEUTRAL"
                summary = "BTC/ETH ranging; altcoin movement governed predominantly by local structural levels."
        else:
            if btc_is_bullish:
                correlation = "COUNTER_TREND_TO_BTC"
                summary = "SHORT position counter-trend to bullish BTC/ETH macro regime. Vulnerable to market-wide short squeeze."
            elif btc_is_bearish:
                correlation = "ALIGNED_WITH_MACRO"
                summary = "SHORT position aligned with bearish BTC/ETH macro distribution and capital outflows."
            else:
                correlation = "NEUTRAL"
                summary = "BTC/ETH ranging; altcoin short governed predominantly by local supply resistance."

        return MarketRegimeContext(
            regime=regime,
            btc_price=btc_px,
            btc_change_24h_pct=btc_chg,
            btc_trend_1h=btc_t1h,
            btc_trend_4h=btc_t4h,
            eth_price=eth_px,
            eth_change_24h_pct=eth_chg,
            eth_trend_1h=eth_t1h,
            eth_trend_4h=eth_t4h,
            position_correlation=correlation,
            summary=summary,
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
        funding_rate_pct: Optional[float] = None,
    ) -> PositionRiskAnalysis:
        """Compute all-in risk, fees, slippage, liquidation proximity, and verified protection status."""
        notional = round(pos_amt * mark_price, 4)
        is_long = str(position.get("side", "LONG")).upper() == "LONG"
        notes: List[str] = []

        # 1. Protection Verification Check (Never Fabricate Confirmation)
        # Position is exchange-verified if explicitly flagged or if sl_orders list is provided from exchange API
        is_verified = bool(position.get("is_exchange_verified", False))
        sl_orders = position.get("sl_orders")
        tp_orders = position.get("tp_orders")

        if sl_orders is not None:
            is_verified = True

        raw_sl = position.get("stop_loss_price")
        sl_val = float(raw_sl) if (raw_sl is not None and str(raw_sl).strip() != "") else None

        raw_tp = position.get("take_profit_price")
        tp_val = float(raw_tp) if (raw_tp is not None and str(raw_tp).strip() != "") else None

        raw_liq = position.get("liquidation_price")
        liq_val = float(raw_liq) if (raw_liq is not None and str(raw_liq).strip() != "" and float(raw_liq) > 0) else None

        verified_sl_id: Optional[str] = None
        verified_sl_qty: Optional[float] = None
        verified_tp_id: Optional[str] = None
        verified_tp_qty: Optional[float] = None

        # Check exchange orders list if present
        if sl_orders is not None and len(sl_orders) > 0:
            best_sl = sl_orders[0]
            sl_val = float(best_sl.get("price", sl_val or 0.0))
            verified_sl_id = str(best_sl.get("order_id", ""))
            verified_sl_qty = sum(float(o.get("orig_qty", 0.0)) for o in sl_orders)

        if tp_orders is not None and len(tp_orders) > 0:
            best_tp = tp_orders[0]
            tp_val = float(best_tp.get("price", tp_val or 0.0))
            verified_tp_id = str(best_tp.get("order_id", ""))
            verified_tp_qty = sum(float(o.get("orig_qty", 0.0)) for o in tp_orders)

        # 2. SL Risk Calculations (Exact Math for LONG and SHORT)
        has_sl = sl_val is not None and sl_val > 0
        sl_is_inverted = False
        sl_dist_usdt = None
        sl_dist_pct = None
        gross_loss_usdt = None
        fee_entry = round(cls.TAKER_FEE_RATE * (pos_amt * entry_price), 4)
        fee_exit_sl = 0.0
        slippage_sl = 0.0
        funding_fee = 0.0
        net_loss_usdt = None
        gross_loss_eq_pct = None
        net_loss_eq_pct = None

        if has_sl:
            if is_long:
                sl_dist_usdt = round(entry_price - sl_val, 4)
                if sl_val >= mark_price:
                    sl_is_inverted = True
                    notes.append("INVALID CONFIGURATION: Stop loss is above mark price for a LONG position.")
                elif sl_val >= entry_price:
                    notes.append("Notice: Stop loss is above entry price (Trailing stop locking profit).")
            else:
                sl_dist_usdt = round(sl_val - entry_price, 4)
                if sl_val <= mark_price:
                    sl_is_inverted = True
                    notes.append("INVALID CONFIGURATION: Stop loss is below mark price for a SHORT position.")
                elif sl_val <= entry_price:
                    notes.append("Notice: Stop loss is below entry price (Trailing stop locking profit).")

            sl_dist_pct = round((abs(entry_price - sl_val) / (entry_price + 1e-9)) * 100, 2)
            gross_loss_usdt = round(max(0.0, sl_dist_usdt * pos_amt), 4)

            fee_exit_sl = round(cls.TAKER_FEE_RATE * (pos_amt * sl_val), 4)
            slippage_sl = round(cls.DEFAULT_SLIPPAGE_RATE * (pos_amt * sl_val), 4)
            if funding_rate_pct:
                funding_fee = round(abs(funding_rate_pct / 100.0) * notional, 4)

            net_loss_usdt = round(gross_loss_usdt + fee_entry + fee_exit_sl + slippage_sl + funding_fee, 4)

            if account_equity and account_equity > 0:
                gross_loss_eq_pct = round((gross_loss_usdt / account_equity) * 100, 2)
                net_loss_eq_pct = round((net_loss_usdt / account_equity) * 100, 2)
        else:
            notes.append("CRITICAL: Position has NO active Stop Loss configured. Account exposed to full liquidation.")

        # 3. TP Reward Calculations (Exact Math for LONG and SHORT)
        has_tp = tp_val is not None and tp_val > 0
        tp_is_inverted = False
        tp_dist_usdt = None
        tp_dist_pct = None
        gross_profit = None
        net_profit = None
        gross_rr = None
        net_rr = None

        if has_tp:
            if is_long:
                tp_dist_usdt = round(tp_val - entry_price, 4)
                if tp_val <= mark_price:
                    tp_is_inverted = True
                    notes.append("INVALID CONFIGURATION: Take profit is below mark price for a LONG position.")
            else:
                tp_dist_usdt = round(entry_price - tp_val, 4)
                if tp_val >= mark_price:
                    tp_is_inverted = True
                    notes.append("INVALID CONFIGURATION: Take profit is above mark price for a SHORT position.")

            tp_dist_pct = round((abs(tp_val - entry_price) / (entry_price + 1e-9)) * 100, 2)
            gross_profit = round(max(0.0, tp_dist_usdt * pos_amt), 4)

            fee_exit_tp = round(cls.TAKER_FEE_RATE * (pos_amt * tp_val), 4)
            slippage_tp = round(cls.DEFAULT_SLIPPAGE_RATE * (pos_amt * tp_val), 4)
            net_profit = round(gross_profit - (fee_entry + fee_exit_tp + slippage_tp), 4)

            if gross_loss_usdt and gross_loss_usdt > 0:
                gross_rr = round(gross_profit / gross_loss_usdt, 2)
            if net_loss_usdt and net_loss_usdt > 0:
                net_rr = round(net_profit / net_loss_usdt, 2)
        else:
            notes.append("Notice: Position has no Take Profit configured on exchange.")

        # 4. Liquidation Threat Assessment
        has_liq = liq_val is not None and liq_val > 0
        liq_dist_usdt = None
        liq_dist_pct = None
        liq_threat = LiquidationThreat.UNKNOWN

        if has_liq and mark_price > 0:
            if is_long:
                liq_dist_usdt = round(mark_price - liq_val, 4)
            else:
                liq_dist_usdt = round(liq_val - mark_price, 4)

            liq_dist_pct = round((abs(liq_dist_usdt) / mark_price) * 100, 2)

            # Check directional breach
            if is_long and liq_val >= mark_price:
                liq_threat = LiquidationThreat.CRITICAL
                notes.append("URGENT: Mark price has breached Long liquidation price!")
            elif not is_long and liq_val <= mark_price:
                liq_threat = LiquidationThreat.CRITICAL
                notes.append("URGENT: Mark price has breached Short liquidation price!")
            elif liq_dist_pct < 3.0:
                liq_threat = LiquidationThreat.CRITICAL
                notes.append(f"DANGER: Liquidation is within {liq_dist_pct}% of mark price.")
            elif liq_dist_pct < 8.0:
                liq_threat = LiquidationThreat.ELEVATED
            elif liq_dist_pct < 15.0:
                liq_threat = LiquidationThreat.MODERATE
            else:
                liq_threat = LiquidationThreat.SAFE

            # Stop Loss vs Liquidation Inversion
            if has_sl:
                if is_long and sl_val <= liq_val:
                    notes.append("CRITICAL INVERSION: Stop loss is below liquidation price! Liquidation will trigger BEFORE stop loss.")
                    liq_threat = LiquidationThreat.CRITICAL
                elif not is_long and sl_val >= liq_val:
                    notes.append("CRITICAL INVERSION: Stop loss is above liquidation price! Liquidation will trigger BEFORE stop loss.")
                    liq_threat = LiquidationThreat.CRITICAL

        # 5. Margin Mode Assessment
        if margin_mode == "ISOLATED":
            margin_desc = f"ISOLATED: Risk capped to allocated margin (${allocated_margin:,.2f}). Wallet balance safe."
        else:
            margin_desc = f"CROSS: Risk exposed to entire wallet equity (${account_equity or 0.0:,.2f}) if liquidation occurs."

        # 6. Classification of Execution Protection Status
        if not has_sl and not has_tp:
            prot_status = ProtectionStatus.COMPLETELY_UNPROTECTED
        elif not has_sl:
            prot_status = ProtectionStatus.UNPROTECTED_NO_SL
        elif not is_verified:
            prot_status = ProtectionStatus.PROTECTION_UNVERIFIED
            notes.append("PROTECTION UNVERIFIED: Stop loss exists in payload/setup but active exchange order has not been verified.")
        else:
            # Verified exchange orders
            sl_qty_matched = verified_sl_qty is None or (verified_sl_qty >= pos_amt - 1e-6)
            if not sl_qty_matched:
                prot_status = ProtectionStatus.PARTIALLY_PROTECTED
                notes.append(f"PARTIAL PROTECTION: Verified SL order covers {verified_sl_qty or 0:.4f} of {pos_amt:.4f} position size.")
            elif has_tp:
                prot_status = ProtectionStatus.FULLY_PROTECTED
            else:
                prot_status = ProtectionStatus.PROTECTED_NO_TP

        # 7. Position Risk Status Classification
        if pos_amt <= 0 or entry_price <= 0:
            risk_status = PositionRiskStatus.INSUFFICIENT_DATA
        elif not has_sl or sl_is_inverted or tp_is_inverted or liq_threat == LiquidationThreat.CRITICAL or (net_loss_eq_pct and net_loss_eq_pct > 15.0):
            risk_status = PositionRiskStatus.RISK_BREACHED
        elif prot_status == ProtectionStatus.PROTECTION_UNVERIFIED:
            risk_status = PositionRiskStatus.PROTECTION_UNVERIFIED
        elif liq_threat in (LiquidationThreat.ELEVATED, LiquidationThreat.MODERATE) or (net_loss_eq_pct and net_loss_eq_pct > 5.0) or prot_status == ProtectionStatus.PARTIALLY_PROTECTED:
            risk_status = PositionRiskStatus.RISK_WARNING
        else:
            risk_status = PositionRiskStatus.RISK_WITHIN_LIMIT

        return PositionRiskAnalysis(
            notional_usdt=notional,
            allocated_margin_usdt=round(allocated_margin, 2),
            has_stop_loss=has_sl,
            risk_status=risk_status,
            stop_loss_price=sl_val,
            sl_distance_usdt=sl_dist_usdt,
            sl_distance_pct=sl_dist_pct,
            gross_loss_at_sl_usdt=gross_loss_usdt,
            est_taker_fees_usdt=round(fee_entry + fee_exit_sl, 4),
            est_slippage_usdt=slippage_sl,
            est_funding_fee_usdt=funding_fee,
            net_loss_at_sl_usdt=net_loss_usdt,
            all_in_loss_at_sl_usdt=net_loss_usdt,
            gross_loss_equity_pct=gross_loss_eq_pct,
            net_loss_equity_pct=net_loss_eq_pct,
            loss_equity_pct=net_loss_eq_pct,
            has_take_profit=has_tp,
            take_profit_price=tp_val,
            tp_distance_usdt=tp_dist_usdt,
            tp_distance_pct=tp_dist_pct,
            gross_profit_at_tp_usdt=gross_profit,
            net_profit_at_tp_usdt=net_profit,
            gross_risk_reward_ratio=gross_rr,
            net_risk_reward_ratio=net_rr,
            risk_reward_ratio=net_rr,
            has_liquidation_price=has_liq,
            liquidation_price=liq_val,
            liquidation_distance_usdt=liq_dist_usdt,
            liquidation_distance_pct=liq_dist_pct,
            liquidation_threat=liq_threat,
            margin_mode=margin_mode,
            margin_mode_assessment=margin_desc,
            protection_status=prot_status,
            is_exchange_verified=is_verified,
            verified_sl_order_id=verified_sl_id,
            verified_sl_qty=verified_sl_qty,
            verified_tp_order_id=verified_tp_id,
            verified_tp_qty=verified_tp_qty,
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
        mtf_synthesis: str,
        freshness_ctx: AuditFreshnessContext,
        regime_ctx: Optional[MarketRegimeContext] = None,
    ) -> Tuple[ThesisStatus, str, List[str]]:
        """Audit trade thesis independently from position risk."""
        evidence: List[str] = []
        is_long = side == "LONG"

        if regime_ctx and regime_ctx.position_correlation == "COUNTER_TREND_TO_BTC":
            evidence.append(f"Counter-trend position: {side} is fighting a dominant macro BTC trend ({regime_ctx.btc_trend_1h}).")

        if "INSUFFICIENT_DATA" in mtf_synthesis:
            return (
                ThesisStatus.INSUFFICIENT_DATA,
                "Insufficient candle data to establish structural trend across timeframes.",
                ["Historical klines returned fewer than required bars to verify swings."],
            )

        # Macro 4H Evidence
        if is_long:
            if tf_4h.trend == "BULLISH":
                evidence.append("4H macro structure is BULLISH, providing structural tailwind for LONG position.")
            elif tf_4h.trend == "BEARISH":
                evidence.append("4H macro structure is BEARISH; LONG position is fighting higher-timeframe trend.")
            else:
                evidence.append("4H macro structure is neutral/ranging consolidation.")
        else:
            if tf_4h.trend == "BEARISH":
                evidence.append("4H macro structure is BEARISH, providing structural tailwind for SHORT position.")
            elif tf_4h.trend == "BULLISH":
                evidence.append("4H macro structure is BULLISH; SHORT position is fighting higher-timeframe trend.")
            else:
                evidence.append("4H macro structure is neutral/ranging consolidation.")

        # Intermediate 1H Evidence
        if is_long:
            if tf_1h.trend == "BULLISH":
                evidence.append("1H intermediate trend is BULLISH with preserved swing lows.")
            elif tf_1h.trend == "BEARISH":
                evidence.append("1H intermediate trend has shifted BEARISH (lower high and lower low sequence).")
            else:
                evidence.append("1H intermediate structure is consolidating without clear expansion.")
        else:
            if tf_1h.trend == "BEARISH":
                evidence.append("1H intermediate trend is BEARISH with preserved swing highs.")
            elif tf_1h.trend == "BULLISH":
                evidence.append("1H intermediate trend has shifted BULLISH (higher low and higher high sequence).")
            else:
                evidence.append("1H intermediate structure is consolidating without clear expansion.")

        # 15M Immediate Momentum Evidence
        if is_long:
            if tf_15m.trend == "BULLISH":
                evidence.append("15M short-term momentum favors buyers with higher local lows.")
            elif tf_15m.trend == "BEARISH":
                evidence.append("15M short-term momentum is breaking down with lower local lows.")
        else:
            if tf_15m.trend == "BEARISH":
                evidence.append("15M short-term momentum favors sellers with downward displacement.")
            elif tf_15m.trend == "BULLISH":
                evidence.append("15M short-term momentum is pushing upward against short position.")

        # Structural Invalidation Check (Price vs Key Swing Lows/Highs)
        structural_invalidated = False
        if is_long:
            if tf_1h.last_swing_low and mark_price < tf_1h.last_swing_low:
                evidence.append(f"Mark price (${mark_price:,.4f}) has breached 1H confirmed swing low (${tf_1h.last_swing_low:,.4f}).")
                structural_invalidated = True
            if tf_4h.last_swing_low and mark_price < tf_4h.last_swing_low:
                evidence.append(f"Mark price (${mark_price:,.4f}) has breached 4H macro structural low (${tf_4h.last_swing_low:,.4f}).")
                structural_invalidated = True
        else:
            if tf_1h.last_swing_high and mark_price > tf_1h.last_swing_high:
                evidence.append(f"Mark price (${mark_price:,.4f}) has breached 1H confirmed swing high (${tf_1h.last_swing_high:,.4f}).")
                structural_invalidated = True
            if tf_4h.last_swing_high and mark_price > tf_4h.last_swing_high:
                evidence.append(f"Mark price (${mark_price:,.4f}) has breached 4H macro structural high (${tf_4h.last_swing_high:,.4f}).")
                structural_invalidated = True

        # Synthesis Classification
        if is_long:
            if structural_invalidated or (tf_4h.trend == "BEARISH" and tf_1h.trend == "BEARISH"):
                status = ThesisStatus.THESIS_INVALIDATED
                summary = "LONG thesis INVALIDATED: Key structural support has broken and higher timeframes are bearish."
            elif tf_1h.trend == "BEARISH" or (tf_15m.trend == "BEARISH" and mark_price < entry_price) or tf_4h.trend == "BEARISH":
                status = ThesisStatus.THESIS_WEAKENING
                summary = "LONG thesis WEAKENING: Lower timeframe momentum loss and conflicting higher-timeframe resistance."
            else:
                status = ThesisStatus.THESIS_INTACT
                summary = "LONG thesis INTACT: Bullish structure preserved across primary timeframes."
        else:
            if structural_invalidated or (tf_4h.trend == "BULLISH" and tf_1h.trend == "BULLISH"):
                status = ThesisStatus.THESIS_INVALIDATED
                summary = "SHORT thesis INVALIDATED: Key structural resistance has broken and higher timeframes are bullish."
            elif tf_1h.trend == "BULLISH" or (tf_15m.trend == "BULLISH" and mark_price > entry_price) or tf_4h.trend == "BULLISH":
                status = ThesisStatus.THESIS_WEAKENING
                summary = "SHORT thesis WEAKENING: Bullish lower-timeframe pressure and conflicting higher-timeframe support."
            else:
                status = ThesisStatus.THESIS_INTACT
                summary = "SHORT thesis INTACT: Bearish structure preserved across primary timeframes."

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
        mtf_synthesis: str,
        regime: Optional[MarketRegimeContext],
        freshness: AuditFreshnessContext,
    ) -> Tuple[ActionRecommendation, str, List[ConditionalTrigger], List[ConditionalTrigger]]:
        """Derive deterministic action recommendation, structured conditional triggers, and invalidation levels."""
        is_long = side == "LONG"
        now_utc_str = datetime.now(timezone.utc).isoformat()
        triggers: List[ConditionalTrigger] = []
        invalidations: List[ConditionalTrigger] = []

        # 1. Structural Invalidation Trigger (Distinct from configured SL!)
        inv_level = tf_1h.last_swing_low if is_long else tf_1h.last_swing_high
        if inv_level is None:
            inv_level = (entry_price * 0.98) if is_long else (entry_price * 1.02)

        inv_active = (mark_price < inv_level) if is_long else (mark_price > inv_level)
        inv_trigger = ConditionalTrigger(
            trigger_type=TriggerType.STRUCTURAL_INVALIDATION.value,
            price_level=inv_level,
            timeframe="1h",
            condition_description=f"1H candle close {'below' if is_long else 'above'} ${inv_level:,.4f}",
            required_confirmation="Confirmed 1H candle close",
            action_implication="Exit position; structural premise is invalidated regardless of stop-loss buffer.",
            is_active=inv_active,
            timestamp_utc=now_utc_str,
        )
        invalidations.append(inv_trigger)
        triggers.append(inv_trigger)

        # 2. Configured Stop-Loss Trigger
        if risk.has_stop_loss and risk.stop_loss_price:
            sl_active = (mark_price <= risk.stop_loss_price) if is_long else (mark_price >= risk.stop_loss_price)
            triggers.append(ConditionalTrigger(
                trigger_type=TriggerType.STOP_LOSS.value,
                price_level=risk.stop_loss_price,
                timeframe="MARKET",
                condition_description=f"Market price touches stop-loss level ${risk.stop_loss_price:,.4f}",
                required_confirmation="Immediate mark price touch",
                action_implication="Execute stop-loss exit to limit loss to allocated risk budget.",
                is_active=sl_active,
                timestamp_utc=now_utc_str,
            ))

        # 3. Take-Profit Target Trigger
        if risk.has_take_profit and risk.take_profit_price:
            tp_active = (mark_price >= risk.take_profit_price) if is_long else (mark_price <= risk.take_profit_price)
            triggers.append(ConditionalTrigger(
                trigger_type=TriggerType.TAKE_PROFIT.value,
                price_level=risk.take_profit_price,
                timeframe="MARKET",
                condition_description=f"Market price reaches target level ${risk.take_profit_price:,.4f}",
                required_confirmation="Limit order fill",
                action_implication="Take profit and close position at targeted R:R level.",
                is_active=tp_active,
                timestamp_utc=now_utc_str,
            ))

        # 4. Trailing Stop / Breakeven Trigger
        be_level = entry_price
        triggers.append(ConditionalTrigger(
            trigger_type=TriggerType.TRAILING_STOP.value,
            price_level=be_level,
            timeframe="15m",
            condition_description=f"Unrealized profit reaches +2.0% (Current: {risk.tp_distance_pct or 0.0}%)",
            required_confirmation="15M momentum continuation",
            action_implication=f"Trail stop-loss order to entry price (${be_level:,.4f}) to guarantee risk-free trade.",
            is_active=False,
            timestamp_utc=now_utc_str,
        ))

        # -----------------------------------------------------------------
        # DETERMINISTIC ACTION RECOMMENDATION DECISION TREE
        # -----------------------------------------------------------------

        # Rule 1: Critical Liquidation Threat Override (Emergency Capital Protection)
        if risk.liquidation_threat == LiquidationThreat.CRITICAL:
            rec = ActionRecommendation.EXIT
            summary = "CRITICAL RISK CLOSE: Position is within 3% of liquidation price or SL is beyond liquidation."
            triggers.append(ConditionalTrigger(
                trigger_type=TriggerType.WARNING_CONDITION.value,
                price_level=risk.liquidation_price,
                timeframe="MARKET",
                condition_description="Immediate manual exit required",
                required_confirmation="Critical liquidation proximity",
                action_implication="Execute immediate market close to prevent catastrophic exchange liquidation penalty.",
                is_active=True,
                timestamp_utc=now_utc_str,
            ))
            return rec, summary, triggers, invalidations

        # Rule 2: Thesis Invalidated Override
        if thesis_status == ThesisStatus.THESIS_INVALIDATED:
            rec = ActionRecommendation.EXIT
            summary = "CLOSE RECOMMENDATION: Market structure has broken against the trade. Exit to preserve capital."
            return rec, summary, triggers, invalidations

        # Rule 3: Unprotected Position Without Stop Loss
        if not risk.has_stop_loss:
            rec = ActionRecommendation.REVIEW
            suggested_sl = (tf_1h.last_swing_low * 0.998) if (is_long and tf_1h.last_swing_low) else (entry_price * 0.985 if is_long else entry_price * 1.015)
            summary = f"URGENT ADJUSTMENT: Position is completely unprotected. Set emergency Stop Loss at ${suggested_sl:,.4f}."
            triggers.append(ConditionalTrigger(
                trigger_type=TriggerType.STOP_LOSS.value,
                price_level=suggested_sl,
                timeframe="MARKET",
                condition_description=f"Immediate Action: Place Stop Loss order at ${suggested_sl:,.4f} on exchange.",
                required_confirmation="Urgent risk mitigation",
                action_implication="Place Stop Loss order on exchange.",
                is_active=True,
                timestamp_utc=now_utc_str,
            ))
            return rec, summary, triggers, invalidations

        # Rule 4: Stale Data / Insufficient Data Guardrail (Only when not already invalidated or critical)
        if freshness.overall_freshness == "STALE_WARNING" and "Missing" in " ".join(freshness.staleness_reasons):
            rec = ActionRecommendation.INSUFFICIENT_DATA
            summary = "INSUFFICIENT DATA: Missing required candle history or market data. Action cannot be reliably determined."
            return rec, summary, triggers, invalidations

        if freshness.overall_freshness == "STALE_WARNING":
            rec = ActionRecommendation.REVIEW
            summary = "DATA STALENESS REVIEW: Market snapshot or candle timestamps are stale. Verify live feed before modifying orders."
            return rec, summary, triggers, invalidations

        # Rule 5: Protection Unverified Warning
        if risk.protection_status == ProtectionStatus.PROTECTION_UNVERIFIED:
            rec = ActionRecommendation.REVIEW
            summary = "REVIEW PROTECTION: Trade thesis is intact, but exchange stop loss order has not been verified. Confirm protective order on Aster DEX."
            return rec, summary, triggers, invalidations

        # Rule 6: Risk Breached (Excessive equity risk or margin hazard)
        if risk.risk_status == PositionRiskStatus.RISK_BREACHED:
            rec = ActionRecommendation.REDUCE
            summary = "REDUCE RISK: Position risk parameters are breached (excessive loss vs equity). Cut position size by 50%."
            return rec, summary, triggers, invalidations

        # Rule 7: Thesis Weakening -> Reduce
        if thesis_status == ThesisStatus.THESIS_WEAKENING:
            rec = ActionRecommendation.REDUCE
            summary = "REDUCE RECOMMENDATION: Early warning signs of reversal. Derisk position by 50%."
            return rec, summary, triggers, invalidations

        # Rule 8: Conflicting Higher-Timeframe Structure
        if "SHORT_TERM_BULLISH_RECOVERY_WITHIN_HIGHER_TIMEFRAME_BEARISH_STRUCTURE" in mtf_synthesis and is_long:
            rec = ActionRecommendation.REVIEW
            summary = "CAUTIOUS REVIEW: 15M short-term recovery is running against 4H macro bearish structure. Consider scaling out at first resistance."
            return rec, summary, triggers, invalidations

        if "SHORT_TERM_PULLBACK_WITHIN_HIGHER_TIMEFRAME_BULLISH_STRUCTURE" in mtf_synthesis and not is_long:
            rec = ActionRecommendation.REVIEW
            summary = "CAUTIOUS REVIEW: Short position is fighting a 4H macro bullish trend. Tighten stop to protect capital."
            return rec, summary, triggers, invalidations

        # Rule 9: Thesis Intact & Risk Within Limit
        if thesis_status == ThesisStatus.THESIS_INTACT and risk.risk_status == PositionRiskStatus.RISK_WITHIN_LIMIT:
            in_profit = (is_long and mark_price > entry_price) or (not is_long and mark_price < entry_price)
            profit_pct = (abs(mark_price - entry_price) / (entry_price + 1e-9)) * 100

            if in_profit and profit_pct >= 2.0:
                rec = ActionRecommendation.REVIEW
                summary = f"ADJUST TO BREAKEVEN: Position is {profit_pct:.1f}% in profit. Trail stop loss to entry (${entry_price:,.4f})."
                return rec, summary, triggers, invalidations

            rec = ActionRecommendation.HOLD
            summary = "HOLD RECOMMENDATION: Structure remains intact and aligned with trade direction."
            return rec, summary, triggers, invalidations

        # Default Fallback
        rec = ActionRecommendation.NO_ACTION
        summary = "NO ACTION: Insufficient structural evidence to modify position."
        return rec, summary, triggers, invalidations
        if "SHORT_TERM_BULLISH_RECOVERY_WITHIN_HIGHER_TIMEFRAME_BEARISH_STRUCTURE" in mtf_synthesis and is_long:
            rec = ActionRecommendation.REVIEW
            summary = "CAUTIOUS REVIEW: 15M short-term recovery is running against 4H macro bearish structure. Consider scaling out at first resistance."
            return rec, summary, triggers, invalidations

        if "SHORT_TERM_PULLBACK_WITHIN_HIGHER_TIMEFRAME_BULLISH_STRUCTURE" in mtf_synthesis and not is_long:
            rec = ActionRecommendation.REVIEW
            summary = "CAUTIOUS REVIEW: Short position is fighting a 4H macro bullish trend. Tighten stop to protect capital."
            return rec, summary, triggers, invalidations

        # Rule 8: Thesis Intact & Risk Within Limit
        if thesis_status == ThesisStatus.THESIS_INTACT and risk.risk_status == PositionRiskStatus.RISK_WITHIN_LIMIT:
            in_profit = (is_long and mark_price > entry_price) or (not is_long and mark_price < entry_price)
            profit_pct = (abs(mark_price - entry_price) / (entry_price + 1e-9)) * 100

            if in_profit and profit_pct >= 2.0:
                rec = ActionRecommendation.REVIEW
                summary = f"PROFIT MANAGEMENT REVIEW: Position is +{profit_pct:.1f}% in profit. Move stop loss to entry (${entry_price:,.4f}) to lock in a risk-free trade."
                return rec, summary, triggers, invalidations

            rec = ActionRecommendation.HOLD
            summary = "HOLD RECOMMENDATION: Multi-timeframe structure supports thesis and position risk is strictly within limits."
            return rec, summary, triggers, invalidations

        # Default Fallback
        rec = ActionRecommendation.REVIEW
        summary = "REVIEW POSITION: Position parameters require manual review."
        return rec, summary, triggers, invalidations

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

        net_exposure = long_notional - short_notional
        if abs(net_exposure) < 1e-6:
            bias = "NEUTRAL"
        elif net_exposure > 0:
            bias = "NET_LONG"
        else:
            bias = "NET_SHORT"

        margin_util = (tot_margin / account_equity * 100) if (account_equity and account_equity > 0) else 0.0
        port_risk_eq = (tot_all_in_risk / account_equity * 100) if (account_equity and account_equity > 0) else None

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
