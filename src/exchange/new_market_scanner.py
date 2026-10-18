"""New Market Opportunity Scanner for Model B.

Discovers and screens newly listed or newly active perpetual contracts across venues.
Applies strict liquidity feasibility and execution-cost gating against the risk budget.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook

logger = logging.getLogger(__name__)


@dataclass
class NewMarketCandidate:
    """Evaluation metrics for a newly listed or newly active perpetual market."""
    symbol: str
    venue: str
    listing_age_days: Optional[float]
    status: str
    spread_bps: float
    depth_10bps_usdt: float
    volume_24h_usdt: float
    funding_rate_8h: float
    is_feasible: bool
    rejection_reasons: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class NewMarketOpportunityScanner:
    """Screens new listings against Model B's strict risk and execution constraints."""

    def __init__(
        self,
        max_listing_age_days: float = 14.0,
        max_spread_bps: float = 20.0,
        min_depth_10bps_usdt: float = 5000.0,
        min_volume_24h_usdt: float = 500000.0,
        target_risk_usdt: float = 1.00,
        max_margin_usdt: float = 15.00,
    ):
        self.max_listing_age_days = max_listing_age_days
        self.max_spread_bps = max_spread_bps
        self.min_depth_10bps_usdt = min_depth_10bps_usdt
        self.min_volume_24h_usdt = min_volume_24h_usdt
        self.target_risk_usdt = target_risk_usdt
        self.max_margin_usdt = max_margin_usdt

    def evaluate_instrument(
        self,
        instrument: NormalizedInstrument,
        order_book: Optional[NormalizedOrderBook] = None,
        listing_time_ms: Optional[int] = None,
        now_ms: Optional[int] = None,
    ) -> NewMarketCandidate:
        """Evaluate a perpetual instrument for new market qualification."""
        if now_ms is None:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        rejections: List[str] = []

        # 1. Listing Age Check
        age_days = None
        if listing_time_ms and listing_time_ms > 0:
            age_days = (now_ms - listing_time_ms) / (86400.0 * 1000.0)
            if age_days > self.max_listing_age_days:
                rejections.append(f"Listing age ({age_days:.1f}d) exceeds new market threshold ({self.max_listing_age_days}d).")

        # 2. Spread and Depth Check
        spread_bps = 0.0
        depth_10bps = 0.0
        if order_book:
            spread_bps = order_book.spread_bps
            bid_d, ask_d = order_book.depth_at_bps(10.0)
            depth_10bps = bid_d + ask_d
            if spread_bps > self.max_spread_bps:
                rejections.append(f"Spread ({spread_bps:.1f} bps) exceeds limit ({self.max_spread_bps} bps).")
            if depth_10bps < self.min_depth_10bps_usdt:
                rejections.append(f"Order book depth within 10 bps (${depth_10bps:.2f}) is below minimum (${self.min_depth_10bps_usdt:.2f}).")
        else:
            rejections.append("No active L2 order book available for depth validation.")

        # 3. 24h Volume
        vol_24h = instrument.volume_24h_usdt or 0.0
        if vol_24h < self.min_volume_24h_usdt:
            rejections.append(f"24h volume (${vol_24h:,.0f}) is below requirement (${self.min_volume_24h_usdt:,.0f}).")

        # 4. Minimum order size feasibility vs $1.00 risk budget
        # If minimum order notional is high and typical 2% stop exceeds risk cap
        min_notional = instrument.min_notional or 5.0
        est_min_stop_loss = min_notional * 0.02 # 2% adverse move on minimum notional
        if est_min_stop_loss > self.target_risk_usdt * 1.5:
            rejections.append(
                f"Exchange min notional (${min_notional:.2f}) forces minimum 2% risk (${est_min_stop_loss:.2f}) "
                f"above Model B risk budget (${self.target_risk_usdt:.2f})."
            )

        # Margin feasibility
        min_margin_at_10x = min_notional / 10.0
        if min_margin_at_10x > self.max_margin_usdt:
            rejections.append(f"Minimum required margin (${min_margin_at_10x:.2f}) exceeds margin cap (${self.max_margin_usdt:.2f}).")

        is_feasible = len(rejections) == 0

        return NewMarketCandidate(
            symbol=instrument.symbol,
            venue=instrument.exchange_name,
            listing_age_days=age_days,
            status="ACTIVE" if instrument.is_active else "INACTIVE",
            spread_bps=round(spread_bps, 2),
            depth_10bps_usdt=round(depth_10bps, 2),
            volume_24h_usdt=round(vol_24h, 2),
            funding_rate_8h=round(instrument.funding_rate or 0.0, 6),
            is_feasible=is_feasible,
            rejection_reasons=rejections,
        )
