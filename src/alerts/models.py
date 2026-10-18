"""Data models and enums for Universal AsterDEX Perpetual Price Alerts.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AlertDirection(str, Enum):
    """Direction for price crossing trigger."""
    PRICE_ABOVE = "PRICE_ABOVE"  # Triggers when price crosses to or above target (P_prev < T <= P_curr)
    PRICE_BELOW = "PRICE_BELOW"  # Triggers when price crosses to or below target (P_prev > T >= P_curr)


class AlertSource(str, Enum):
    """Source of price data for evaluation."""
    MARK_PRICE = "MARK_PRICE"  # Mark price (authoritative for perp liquidations & index tracking)
    LAST_PRICE = "LAST_PRICE"  # Last traded price from orderbook trades


class AlertType(str, Enum):
    """Recurrence mode of the alert."""
    ONE_OFF = "ONE_OFF"      # Triggers once, then sets status=TRIGGERED and deactivates
    RECURRING = "RECURRING"  # Re-arms after price crosses back to opposite side and cooldown passes


class AlertStatus(str, Enum):
    """Lifecycle and operational status of the alert."""
    ACTIVE = "ACTIVE"        # Active and monitoring
    TRIGGERED = "TRIGGERED"  # Triggered (for one-off alerts)
    DISABLED = "DISABLED"    # Manually disabled by user
    STALE = "STALE"          # Price data stale (>60s) or missing
    ERROR = "ERROR"          # Symbol invalid or delisted


class DiscordDeliveryStatus(str, Enum):
    """Status of Discord webhook delivery for an alert event."""
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


class PriceAlert(BaseModel):
    """Persistent user-configured price alert."""
    alert_id: str
    symbol: str
    target_price: float
    direction: AlertDirection
    price_source: AlertSource = AlertSource.MARK_PRICE
    alert_type: AlertType = AlertType.ONE_OFF
    note: str = ""
    is_active: bool = True
    status: AlertStatus = AlertStatus.ACTIVE
    armed: bool = True
    last_checked_price: Optional[float] = None
    last_checked_at: Optional[str] = None
    trigger_count: int = 0
    last_triggered_at: Optional[str] = None
    created_at: str
    updated_at: str

    # Dynamic / computed fields for UI/API presentation
    current_price: Optional[float] = None
    distance_usdt: Optional[float] = None
    distance_pct: Optional[float] = None
    is_stale: bool = False


class AlertEvent(BaseModel):
    """Record of a triggered alert event."""
    event_id: Optional[int] = None
    alert_id: str
    symbol: str
    target_price: float
    trigger_price: float
    direction: AlertDirection
    price_source: AlertSource
    triggered_at: str
    note: str = ""
    acknowledged: bool = False
    discord_status: str = "DISABLED"
    discord_attempts: int = 0
    discord_sent_at: Optional[str] = None
    discord_error: Optional[str] = None


class SymbolCatalogItem(BaseModel):
    """Exchange metadata and current prices for an eligible perpetual symbol."""
    symbol: str
    base_asset: str
    quote_asset: str = "USDT"
    price_precision: int = 2
    quantity_precision: int = 2
    tick_size: float = 0.01
    min_qty: float = 0.01
    min_notional: float = 5.0
    status: str = "TRADING"
    mark_price: Optional[float] = None
    last_price: Optional[float] = None
    last_updated_at: Optional[str] = None
    is_stale: bool = False


# API Request / Response schemas
class AlertCreateRequest(BaseModel):
    symbol: str
    target_price: float = Field(..., gt=0, description="Target crossing price (must be positive)")
    direction: AlertDirection
    price_source: AlertSource = AlertSource.MARK_PRICE
    alert_type: AlertType = AlertType.ONE_OFF
    note: str = Field(default="", max_length=200)


class AlertUpdateRequest(BaseModel):
    target_price: Optional[float] = Field(default=None, gt=0)
    direction: Optional[AlertDirection] = None
    price_source: Optional[AlertSource] = None
    alert_type: Optional[AlertType] = None
    note: Optional[str] = Field(default=None, max_length=200)
    is_active: Optional[bool] = None


class AlertAcknowledgeRequest(BaseModel):
    event_ids: Optional[List[int]] = None  # None means acknowledge all
