"""Discord Webhook Notification Dispatcher for Universal AsterDEX Perpetual Price Alerts.

Provides asynchronous, rate-limit resilient, and duplicate-protected delivery
of price crossing alerts to Discord channels via rich embeds.
"""

import asyncio
from datetime import datetime, timezone
import os
import threading
import time
from typing import Any, Dict, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from src.alerts.models import (
    AlertDirection,
    AlertEvent,
    AlertSource,
    DiscordDeliveryStatus,
    PriceAlert,
)
from src.alerts.storage import AlertStorage
from src.core.logger import logger
from src.execution.events import mask_webhook_url, validate_webhook_url


class AlertDiscordNotifier:
    """Dispatches real-time price alert crossing notifications to Discord."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        storage: Optional[AlertStorage] = None,
        request_timeout: float = 8.0,
        max_retries: int = 3,
        retry_delay_sec: float = 1.0,
    ) -> None:
        raw_url = webhook_url if webhook_url is not None else os.getenv("DISCORD_PRICE_ALERT_WEBHOOK_URL")
        self.webhook_url: Optional[str] = raw_url.strip() if raw_url and raw_url.strip() else None
        self.storage: Optional[AlertStorage] = storage
        self.request_timeout: float = request_timeout
        self.max_retries: int = max_retries
        self.retry_delay_sec: float = retry_delay_sec

        self.enabled: bool = bool(self.webhook_url and validate_webhook_url(self.webhook_url))
        if self.webhook_url and not self.enabled:
            logger.warning("Discord price alert webhook URL format is invalid. Notifications disabled.")

        # Delivery telemetry counters
        self._delivered_count: int = 0
        self._failed_count: int = 0
        self._last_delivery_time: Optional[str] = None
        self._last_error: Optional[str] = None

        # Configure session with connection pooling
        self.session = requests.Session()
        retries = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=5, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def mask_url(self) -> str:
        """Return redacted webhook URL for logging and UI display."""
        return mask_webhook_url(self.webhook_url)

    def build_embed(
        self,
        event: AlertEvent,
        alert: Optional[PriceAlert] = None,
        is_stale: bool = False,
    ) -> Dict[str, Any]:
        """Construct structured mobile-friendly Discord embed."""
        is_above = event.direction == AlertDirection.PRICE_ABOVE
        direction_icon = "🟢" if is_above else "🔴"
        direction_text = "Crossed ABOVE" if is_above else "Crossed BELOW"
        condition_label = "Price rose to or above target (≥ Target)" if is_above else "Price dropped to or below target (≤ Target)"

        # Embed color: Emerald Green (0x10B981) for ABOVE, Coral Red (0xEF4444) for BELOW, Warning Amber (0xF59E0B) if stale
        if is_stale:
            color = 0xF59E0B
        elif is_above:
            color = 0x10B981
        else:
            color = 0xEF4444

        source_label = "Mark Price (Authoritative)" if event.price_source == AlertSource.MARK_PRICE else "Last Traded Price"
        mode_label = alert.alert_type.value if alert else "ONE_OFF"

        freshness_label = "⚠️ Uncertain / Stale Market Data" if is_stale else "✅ Confirmed (Fresh bulk stream, <5s age)"

        # Format prices cleanly
        def _fmt(p: float) -> str:
            if abs(p) >= 100:
                return f"${p:,.2f}"
            if abs(p) >= 1:
                return f"${p:,.4f}"
            return f"${p:,.6f}"

        fields = [
            {"name": "Contract / Market", "value": f"**{event.symbol}** (AsterDEX USDT Perpetual)", "inline": True},
            {"name": "Trigger Condition", "value": f"{direction_icon} **{direction_text}**\n*{condition_label}*", "inline": True},
            {"name": "Target Price", "value": f"**{_fmt(event.target_price)}**", "inline": True},
            {"name": "Observed Trigger Price", "value": f"**{_fmt(event.trigger_price)}**", "inline": True},
            {"name": "Price Source", "value": source_label, "inline": True},
            {"name": "Recurrence & Alert ID", "value": f"`{mode_label}` • `{event.alert_id}`", "inline": True},
            {"name": "Market Data Freshness", "value": freshness_label, "inline": False},
        ]

        if event.note:
            fields.append({"name": "User Note", "value": f"*{event.note}*", "inline": False})

        embed = {
            "title": f"{direction_icon} AsterDEX Price Alert: {event.symbol} {direction_text}",
            "description": f"Price crossed **{_fmt(event.target_price)}** on AsterDEX perpetuals tape.",
            "color": color,
            "fields": fields,
            "footer": {
                "text": "SignalGenerator • Universal AsterDEX Perpetual Price Alerts • Observational Telemetry Only"
            },
            "timestamp": event.triggered_at,
        }
        return embed

    def send_notification(
        self,
        event: AlertEvent,
        alert: Optional[PriceAlert] = None,
        is_stale: bool = False,
    ) -> bool:
        """
        Synchronously deliver notification with duplicate protection and rate-limit backoff.
        Must be called from a worker thread or async executor.
        """
        if not self.enabled or not self.webhook_url:
            if self.storage and event.event_id is not None:
                self.storage.update_event_discord_status(
                    event.event_id,
                    status=DiscordDeliveryStatus.DISABLED.value,
                    attempts=0,
                )
            return False

        # 1. Duplicate Prevention Check (including across application restarts)
        if self.storage and event.event_id is not None:
            persisted = self.storage.get_event(event.event_id)
            if persisted and persisted.discord_status == DiscordDeliveryStatus.SENT.value:
                logger.info(f"Duplicate alert event {event.event_id} for {event.symbol} already delivered. Skipping.")
                return True

        masked_url = self.mask_url()
        embed = self.build_embed(event, alert=alert, is_stale=is_stale)
        payload = {
            "username": "AsterDEX Price Alerts",
            "embeds": [embed],
        }

        # Record PENDING status
        if self.storage and event.event_id is not None:
            self.storage.update_event_discord_status(
                event.event_id,
                status=DiscordDeliveryStatus.PENDING.value,
                attempts=0,
            )

        attempts = 0
        last_error: Optional[str] = None

        for attempt in range(1, self.max_retries + 1):
            attempts = attempt
            try:
                resp = self.session.post(self.webhook_url, json=payload, timeout=self.request_timeout)
                if resp.status_code in (200, 204):
                    sent_at = datetime.now(timezone.utc).isoformat()
                    self._delivered_count += 1
                    self._last_delivery_time = sent_at
                    logger.info(
                        f"Delivered Discord price alert for {event.symbol} (Event ID: {event.event_id}) to {masked_url}."
                    )
                    if self.storage and event.event_id is not None:
                        self.storage.update_event_discord_status(
                            event.event_id,
                            status=DiscordDeliveryStatus.SENT.value,
                            attempts=attempts,
                            sent_at=sent_at,
                        )
                    return True

                elif resp.status_code == 429:
                    # Rate limited: check Retry-After
                    retry_hdr = resp.headers.get("Retry-After", "1.0")
                    try:
                        retry_after = float(retry_hdr)
                    except (ValueError, TypeError):
                        retry_after = 1.0

                    if retry_after <= 2.0 and attempt < self.max_retries:
                        logger.warning(f"Discord rate limit (429). Backing off for {retry_after:.2f}s before retry.")
                        time.sleep(retry_after)
                        continue
                    else:
                        last_error = f"Rate limit exceeded (HTTP 429, Retry-After: {retry_after:.2f}s)"
                        logger.warning(f"{last_error} for {masked_url}.")
                        break

                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
                    logger.warning(f"Discord webhook failed with {last_error} for {masked_url}.")
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay_sec * attempt)

            except requests.Timeout:
                last_error = f"Request timed out after {self.request_timeout}s"
                logger.warning(f"Discord webhook timeout for {masked_url}.")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_sec * attempt)
            except requests.RequestException as req_err:
                err_msg = str(req_err)
                if self.webhook_url in err_msg:
                    err_msg = err_msg.replace(self.webhook_url, masked_url)
                last_error = err_msg
                logger.warning(f"Discord webhook network error: {last_error}.")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_sec * attempt)
            except Exception as e:
                err_msg = str(e)
                if self.webhook_url in err_msg:
                    err_msg = err_msg.replace(self.webhook_url, masked_url)
                last_error = err_msg
                logger.error(f"Unexpected error in Discord dispatch: {last_error}.")
                break

        # If exhausted all retries without confirmation
        self._failed_count += 1
        self._last_error = last_error
        if self.storage and event.event_id is not None:
            self.storage.update_event_discord_status(
                event.event_id,
                status=DiscordDeliveryStatus.FAILED.value,
                attempts=attempts,
                error=last_error,
            )

        return False

    def dispatch_async(
        self,
        event: AlertEvent,
        alert: Optional[PriceAlert] = None,
        is_stale: bool = False,
    ) -> None:
        """
        Asynchronously dispatch alert notification in a detached daemon thread.
        Guarantees that Discord network latency never delays the price evaluation engine.
        """
        if not self.enabled:
            return

        thread = threading.Thread(
            target=self.send_notification,
            args=(event, alert, is_stale),
            daemon=True,
            name=f"discord-alert-{event.event_id or event.alert_id}",
        )
        thread.start()

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic health and masking status for API responses."""
        return {
            "enabled": self.enabled,
            "webhook_configured": bool(self.webhook_url),
            "webhook_masked": self.mask_url() if self.enabled else None,
            "delivered_count": self._delivered_count,
            "failed_count": self._failed_count,
            "last_delivery_time": self._last_delivery_time,
            "last_error": self._last_error,
        }
