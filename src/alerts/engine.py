"""Price Alert Engine.

Manages alert lifecycle, deterministic crossing trigger evaluation,
recurring re-arming semantics, startup edge-cases, and event notification dispatch.
"""

from collections import deque
from datetime import datetime, timezone
import time
from typing import Any, Callable, Deque, Dict, List, Optional
import uuid

from src.alerts.models import (
    AlertCreateRequest,
    AlertDirection,
    AlertEvent,
    AlertSource,
    AlertStatus,
    AlertType,
    AlertUpdateRequest,
    PriceAlert,
)
from src.alerts.market_data import AlertMarketDataService
from src.alerts.storage import AlertStorage
from src.core.logger import logger


class PriceAlertEngine:
    """Core engine evaluating price crossing conditions and managing alert states."""

    def __init__(
        self,
        storage: AlertStorage,
        market_data: AlertMarketDataService,
        discord_notifier: Optional[Any] = None,
        cooldown_sec: float = 60.0,
        max_recent_events: int = 100,
    ) -> None:
        self.storage = storage
        self.market_data = market_data
        self.discord_notifier = discord_notifier
        self.cooldown_sec = cooldown_sec
        self._listeners: List[Callable[[AlertEvent], Any]] = []
        self._recent_events: Deque[AlertEvent] = deque(maxlen=max_recent_events)

        # Connect tick listener to market data service
        self.market_data.subscribe_tick(self.on_market_tick)

    def register_listener(self, listener: Callable[[AlertEvent], Any]) -> None:
        """Register a notification listener callback."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def create_alert(self, req: AlertCreateRequest) -> PriceAlert:
        """
        Create and persist a new price alert.
        Validates symbol existence and initializes armed state deterministically.
        """
        symbol_clean = req.symbol.upper().strip()
        now_iso = datetime.now(timezone.utc).isoformat()
        alert_id = f"pa_{uuid.uuid4().hex[:12]}"

        # Check current price for deterministic startup arming
        curr_price, is_stale = self.market_data.get_symbol_price(symbol_clean, req.price_source)

        armed = True
        last_price = None
        status = AlertStatus.ACTIVE

        if curr_price is not None and curr_price > 0 and not is_stale:
            last_price = curr_price
            # If created when price is ALREADY beyond threshold, do not trigger false positive
            if req.direction == AlertDirection.PRICE_ABOVE and curr_price >= req.target_price:
                armed = False
                logger.info(
                    f"Alert {alert_id} for {symbol_clean} ABOVE {req.target_price} created unarmed "
                    f"(current price {curr_price} >= target)."
                )
            elif req.direction == AlertDirection.PRICE_BELOW and curr_price <= req.target_price:
                armed = False
                logger.info(
                    f"Alert {alert_id} for {symbol_clean} BELOW {req.target_price} created unarmed "
                    f"(current price {curr_price} <= target)."
                )
            else:
                armed = True
        elif is_stale or curr_price is None:
            # If price data unavailable yet, arm by default but mark as STALE initially
            status = AlertStatus.STALE

        alert = PriceAlert(
            alert_id=alert_id,
            symbol=symbol_clean,
            target_price=req.target_price,
            direction=req.direction,
            price_source=req.price_source,
            alert_type=req.alert_type,
            note=req.note.strip(),
            is_active=True,
            status=status,
            armed=armed,
            last_checked_price=last_price,
            last_checked_at=now_iso if last_price is not None else None,
            trigger_count=0,
            last_triggered_at=None,
            created_at=now_iso,
            updated_at=now_iso,
        )

        saved = self.storage.create_alert(alert)
        logger.info(
            f"Created PriceAlert: {saved.alert_id} ({saved.symbol} {saved.direction.value} {saved.target_price}, armed={saved.armed})"
        )
        return self.enrich_alert(saved)

    def update_alert(self, alert_id: str, req: AlertUpdateRequest) -> Optional[PriceAlert]:
        """Update existing alert configuration."""
        alert = self.storage.get_alert(alert_id)
        if not alert:
            return None

        if req.target_price is not None:
            alert.target_price = req.target_price
            # Re-evaluate armed status based on current price
            curr_price, _ = self.market_data.get_symbol_price(alert.symbol, alert.price_source)
            if curr_price is not None:
                if alert.direction == AlertDirection.PRICE_ABOVE and curr_price >= alert.target_price:
                    alert.armed = False
                elif alert.direction == AlertDirection.PRICE_BELOW and curr_price <= alert.target_price:
                    alert.armed = False
                else:
                    alert.armed = True

        if req.direction is not None:
            alert.direction = req.direction
        if req.price_source is not None:
            alert.price_source = req.price_source
        if req.alert_type is not None:
            alert.alert_type = req.alert_type
        if req.note is not None:
            alert.note = req.note.strip()
        if req.is_active is not None:
            alert.is_active = req.is_active
            if req.is_active and alert.status in (AlertStatus.DISABLED, AlertStatus.TRIGGERED):
                alert.status = AlertStatus.ACTIVE
                alert.armed = True
            elif not req.is_active:
                alert.status = AlertStatus.DISABLED
                alert.armed = False

        self.storage.update_alert(alert)
        logger.info(f"Updated PriceAlert {alert_id}")
        return self.enrich_alert(alert)

    def toggle_alert(self, alert_id: str) -> Optional[PriceAlert]:
        """Toggle an alert's active status."""
        alert = self.storage.get_alert(alert_id)
        if not alert:
            return None

        alert.is_active = not alert.is_active
        if alert.is_active:
            alert.status = AlertStatus.ACTIVE
            alert.armed = True
        else:
            alert.status = AlertStatus.DISABLED
            alert.armed = False

        self.storage.update_alert(alert)
        return self.enrich_alert(alert)

    def delete_alert(self, alert_id: str) -> bool:
        """Delete an alert from persistent storage."""
        return self.storage.delete_alert(alert_id)

    def get_alert(self, alert_id: str) -> Optional[PriceAlert]:
        """Get an enriched alert by ID."""
        alert = self.storage.get_alert(alert_id)
        if not alert:
            return None
        return self.enrich_alert(alert)

    def list_alerts(
        self,
        symbol: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> List[PriceAlert]:
        """List and enrich all matching alerts."""
        alerts = self.storage.list_alerts(symbol=symbol, is_active=is_active)
        return [self.enrich_alert(a) for a in alerts]

    def enrich_alert(self, alert: PriceAlert) -> PriceAlert:
        """Enrich alert model with live exchange price, distance, and staleness."""
        curr_price, is_stale = self.market_data.get_symbol_price(alert.symbol, alert.price_source)
        alert.current_price = curr_price
        alert.is_stale = is_stale

        if is_stale:
            if alert.is_active and alert.status not in (AlertStatus.DISABLED, AlertStatus.TRIGGERED):
                alert.status = AlertStatus.STALE
        else:
            if alert.is_active and alert.status == AlertStatus.STALE:
                alert.status = AlertStatus.ACTIVE

        if curr_price is not None and curr_price > 0:
            diff = curr_price - alert.target_price
            alert.distance_usdt = round(diff, 6)
            alert.distance_pct = round((diff / alert.target_price) * 100, 3)

        return alert

    def evaluate_alert(
        self,
        alert: PriceAlert,
        current_price: float,
        is_stale: bool,
    ) -> Optional[AlertEvent]:
        """
        Evaluate crossing condition for a single alert.
        Handles startup price boundaries, one-off vs recurring semantics, and hysteresis re-arming.
        """
        # 1. Skip inactive or disabled alerts
        if not alert.is_active or alert.status == AlertStatus.DISABLED:
            return None

        # 2. Never trigger on stale or invalid data
        if is_stale or current_price is None or current_price <= 0:
            if alert.status == AlertStatus.ACTIVE:
                alert.status = AlertStatus.STALE
                self.storage.update_alert(alert)
            return None

        # If previously stale, restore to active
        if alert.status == AlertStatus.STALE:
            alert.status = AlertStatus.ACTIVE

        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()
        prev_price = alert.last_checked_price
        target = alert.target_price

        # 3. Handle uninitialized prior price (startup / recovery)
        if prev_price is None:
            # Check whether price is currently beyond threshold
            if alert.direction == AlertDirection.PRICE_ABOVE and current_price >= target:
                alert.armed = False
            elif alert.direction == AlertDirection.PRICE_BELOW and current_price <= target:
                alert.armed = False
            else:
                alert.armed = True

            alert.last_checked_price = current_price
            alert.last_checked_at = now_iso
            self.storage.update_alert(alert)
            return None

        # 4. Check re-arming for unarmed recurring alerts
        if not alert.armed:
            if alert.alert_type == AlertType.RECURRING:
                # Check cooldown period
                cooldown_ok = True
                if alert.last_triggered_at:
                    try:
                        last_trig_dt = datetime.fromisoformat(alert.last_triggered_at)
                        elapsed = (now_utc - last_trig_dt).total_seconds()
                        if elapsed < self.cooldown_sec:
                            cooldown_ok = False
                    except Exception:
                        cooldown_ok = True

                # Check crossing back to opposite side
                crossed_back = False
                if alert.direction == AlertDirection.PRICE_ABOVE:
                    # Must drop strictly below target to re-arm
                    crossed_back = (current_price < target)
                elif alert.direction == AlertDirection.PRICE_BELOW:
                    # Must rise strictly above target to re-arm
                    crossed_back = (current_price > target)

                if cooldown_ok and crossed_back:
                    alert.armed = True
                    logger.info(
                        f"Recurring alert {alert.alert_id} for {alert.symbol} re-armed: "
                        f"price {current_price} crossed back across target {target}."
                    )
            # Update check price and return
            alert.last_checked_price = current_price
            alert.last_checked_at = now_iso
            self.storage.update_alert(alert)
            return None

        # 5. Evaluate crossing conditions for armed alert
        triggered = False
        if alert.direction == AlertDirection.PRICE_ABOVE:
            # Crosses to or above target: prev_price < target <= current_price
            if prev_price < target and current_price >= target:
                triggered = True
        elif alert.direction == AlertDirection.PRICE_BELOW:
            # Crosses to or below target: prev_price > target >= current_price
            if prev_price > target and current_price <= target:
                triggered = True

        if not triggered:
            alert.last_checked_price = current_price
            alert.last_checked_at = now_iso
            self.storage.update_alert(alert)
            return None

        # 6. Crossing Triggered!
        event = AlertEvent(
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            target_price=target,
            trigger_price=current_price,
            direction=alert.direction,
            price_source=alert.price_source,
            triggered_at=now_iso,
            note=alert.note,
            acknowledged=False,
        )
        saved_event = self.storage.record_event(event)

        alert.trigger_count += 1
        alert.last_triggered_at = now_iso
        alert.last_checked_price = current_price
        alert.last_checked_at = now_iso

        if alert.alert_type == AlertType.ONE_OFF:
            alert.is_active = False
            alert.status = AlertStatus.TRIGGERED
            alert.armed = False
        else:
            alert.status = AlertStatus.ACTIVE
            alert.armed = False  # Disarmed until it crosses back

        self.storage.update_alert(alert)

        # Record in memory queue
        self._recent_events.appendleft(saved_event)

        logger.info(
            f"ALERT TRIGGERED! {alert.symbol} {alert.direction.value} {target} "
            f"(Trigger Price: {current_price}, Source: {alert.price_source.value})"
        )

        # Dispatch to notification listeners
        for listener in self._listeners:
            try:
                res = listener(saved_event)
                # If coroutine, user may handle in event loop
            except Exception as e:
                logger.error(f"Error in alert listener: {e}")

        # Dispatch asynchronously to Discord Webhook if configured and enabled
        if self.discord_notifier and getattr(self.discord_notifier, "enabled", False):
            try:
                self.discord_notifier.dispatch_async(saved_event, alert=alert, is_stale=is_stale)
            except Exception as d_err:
                logger.warning(f"Failed to dispatch Discord price alert notification: {d_err}")

        return saved_event

    def on_market_tick(self, prices: Dict[str, Dict[str, Any]]) -> List[AlertEvent]:
        """Invoked on each market data poll cycle to evaluate all active alerts."""
        active_alerts = self.storage.list_alerts(is_active=True)
        events_generated: List[AlertEvent] = []

        for alert in active_alerts:
            curr_price, is_stale = self.market_data.get_symbol_price(alert.symbol, alert.price_source)
            if curr_price is not None:
                ev = self.evaluate_alert(alert, curr_price, is_stale)
                if ev:
                    events_generated.append(ev)

        return events_generated

    def get_recent_events(self, limit: int = 50) -> List[AlertEvent]:
        """Fetch latest triggered events from database."""
        return self.storage.list_events(limit=limit)

    def acknowledge_events(self, event_ids: Optional[List[int]] = None) -> int:
        """Mark events as acknowledged."""
        count = self.storage.acknowledge_events(event_ids=event_ids)
        # Update in-memory queue
        for ev in self._recent_events:
            if event_ids is None or (ev.event_id in event_ids):
                ev.acknowledged = True
        return count

    def clear_events(self) -> int:
        """Clear all event history."""
        self._recent_events.clear()
        return self.storage.clear_events()
