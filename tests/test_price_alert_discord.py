"""Unit and integration tests for Universal AsterDEX Perpetual Price Alerts - Discord Webhooks.

Validates:
1. Webhook configuration, masking, and credential redaction (tokens never leak).
2. Disabled behavior when environment variable is absent or URL invalid.
3. Rich embed payload structure, field formatting, color-coding, and staleness badges.
4. Synchronous delivery with mocked responses (HTTP 200/204).
5. Bounded retries (max 3), exponential backoff, and HTTP 429 Retry-After handling.
6. Persistent duplicate prevention across application restarts.
7. Asynchronous decoupled dispatch via daemon threads (no engine latency).
8. PriceAlertEngine integration on price crossing.
9. FastAPI endpoints: GET /api/alerts/status and POST /api/alerts/discord/test.
"""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
from typing import Any, Dict
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import pytest
import requests

from src.alerts.discord import AlertDiscordNotifier
from src.alerts.engine import PriceAlertEngine
from src.alerts.market_data import AlertMarketDataService
from src.alerts.models import (
    AlertDirection,
    AlertEvent,
    AlertSource,
    AlertStatus,
    AlertType,
    DiscordDeliveryStatus,
    PriceAlert,
)
from src.alerts.storage import AlertStorage
from src.api.app import alert_discord, app
from src.data.database import DatabaseManager


VALID_TEST_WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz0123456789"
VALID_APP_WEBHOOK = "https://discordapp.com/api/webhooks/987654321098765432/zyxwvutsrqponmlkjihgfedcba9876543210"


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_discord_alerts.db"
        db = DatabaseManager(db_path)
        yield db


@pytest.fixture
def storage(temp_db):
    return AlertStorage(temp_db)


@pytest.fixture
def sample_event():
    return AlertEvent(
        event_id=1,
        alert_id="alt_test_001",
        symbol="BTCUSDT",
        target_price=68500.0,
        trigger_price=68520.0,
        direction=AlertDirection.PRICE_ABOVE,
        price_source=AlertSource.MARK_PRICE,
        triggered_at="2026-10-18T12:00:00Z",
        note="Test breakout above resistance",
    )


class TestDiscordConfigAndSanitization:
    """Configuration, validation, and token masking tests."""

    def test_disabled_when_absent_or_empty(self):
        notifier1 = AlertDiscordNotifier(webhook_url=None)
        assert notifier1.enabled is False
        assert notifier1.webhook_url is None
        assert notifier1.mask_url() == "[NOT CONFIGURED]"
        status1 = notifier1.get_status()
        assert status1["enabled"] is False
        assert status1["webhook_configured"] is False
        assert status1["webhook_masked"] is None

        notifier2 = AlertDiscordNotifier(webhook_url="   ")
        assert notifier2.enabled is False
        assert notifier2.webhook_url is None

    def test_disabled_when_invalid_domain_or_format(self):
        invalid_urls = [
            "https://evil.com/api/webhooks/123/token",
            "http://discord.com/api/webhooks/123/token",  # non-https
            "https://discord.com/other/path/123/token",
            "not_a_url",
            "https://discord.com/api/webhooks/",  # missing parts
        ]
        for url in invalid_urls:
            notifier = AlertDiscordNotifier(webhook_url=url)
            assert notifier.enabled is False, f"Expected disabled for {url}"

    def test_enabled_when_valid_discord_webhook(self):
        for url in [VALID_TEST_WEBHOOK, VALID_APP_WEBHOOK]:
            notifier = AlertDiscordNotifier(webhook_url=url)
            assert notifier.enabled is True
            assert notifier.mask_url().startswith("https://discord")
            assert "abcdef" not in notifier.mask_url()
            assert "zyxwvu" not in notifier.mask_url()

    def test_token_redaction_in_status_and_masking(self):
        notifier = AlertDiscordNotifier(webhook_url=VALID_TEST_WEBHOOK)
        status = notifier.get_status()
        assert status["enabled"] is True
        assert status["webhook_configured"] is True
        masked = status["webhook_masked"]
        assert masked is not None
        assert "123456789012345678" not in masked
        assert "abcdefghijklmnopqrstuvwxyz" not in masked
        assert masked.endswith("6789")

    def test_disabled_send_notification_marks_db_disabled(self, storage, sample_event):
        storage.record_event(sample_event)
        event_id = sample_event.event_id

        notifier = AlertDiscordNotifier(webhook_url=None, storage=storage)
        delivered = notifier.send_notification(sample_event)
        assert delivered is False

        updated = storage.get_event(event_id)
        assert updated is not None
        assert updated.discord_status == DiscordDeliveryStatus.DISABLED.value
        assert updated.discord_attempts == 0


class TestDiscordEmbedFormatting:
    """Rich embed building and formatting tests."""

    def test_embed_formatting_price_above(self, sample_event):
        notifier = AlertDiscordNotifier(webhook_url=VALID_TEST_WEBHOOK)
        alert = PriceAlert(
            alert_id=sample_event.alert_id,
            symbol=sample_event.symbol,
            direction=AlertDirection.PRICE_ABOVE,
            target_price=sample_event.target_price,
            alert_type=AlertType.RECURRING,
            created_at=sample_event.triggered_at,
            updated_at=sample_event.triggered_at,
        )

        embed = notifier.build_embed(sample_event, alert=alert, is_stale=False)

        assert embed["color"] == 0x10B981  # Emerald green
        assert "BTCUSDT" in embed["title"]
        assert "Crossed ABOVE" in embed["title"]
        assert embed["timestamp"] == sample_event.triggered_at

        # Verify fields
        field_map = {f["name"]: f["value"] for f in embed["fields"]}
        assert "BTCUSDT" in field_map["Contract / Market"]
        assert "AsterDEX USDT Perpetual" in field_map["Contract / Market"]
        assert "$68,500.00" in field_map["Target Price"]
        assert "$68,520.00" in field_map["Observed Trigger Price"]
        assert "Mark Price (Authoritative)" in field_map["Price Source"]
        assert "RECURRING" in field_map["Recurrence & Alert ID"]
        assert "Confirmed (Fresh" in field_map["Market Data Freshness"]
        assert "Test breakout above resistance" in field_map["User Note"]

    def test_embed_formatting_price_below(self):
        notifier = AlertDiscordNotifier(webhook_url=VALID_TEST_WEBHOOK)
        event = AlertEvent(
            alert_id="alt_eth_down",
            symbol="ETHUSDT",
            target_price=2450.0,
            trigger_price=2448.5,
            direction=AlertDirection.PRICE_BELOW,
            price_source=AlertSource.LAST_PRICE,
            triggered_at="2026-10-18T12:05:00Z",
        )
        embed = notifier.build_embed(event, alert=None, is_stale=False)

        assert embed["color"] == 0xEF4444  # Coral red
        assert "Crossed BELOW" in embed["title"]
        field_map = {f["name"]: f["value"] for f in embed["fields"]}
        assert "Last Traded Price" in field_map["Price Source"]
        assert "ONE_OFF" in field_map["Recurrence & Alert ID"]

    def test_embed_formatting_stale_data_warning(self, sample_event):
        notifier = AlertDiscordNotifier(webhook_url=VALID_TEST_WEBHOOK)
        embed = notifier.build_embed(sample_event, alert=None, is_stale=True)

        assert embed["color"] == 0xF59E0B  # Warning amber
        field_map = {f["name"]: f["value"] for f in embed["fields"]}
        assert "Uncertain / Stale Market Data" in field_map["Market Data Freshness"]


class TestDiscordDeliveryAndRetry:
    """HTTP delivery, retry, and rate limit backoff tests."""

    def test_successful_delivery_mocked(self, storage, sample_event):
        storage.record_event(sample_event)
        event_id = sample_event.event_id

        notifier = AlertDiscordNotifier(
            webhook_url=VALID_TEST_WEBHOOK,
            storage=storage,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 204

        with patch.object(notifier.session, "post", return_value=mock_resp) as mock_post:
            success = notifier.send_notification(sample_event)

        assert success is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["username"] == "AsterDEX Price Alerts"
        assert len(call_kwargs["json"]["embeds"]) == 1

        # Check DB update
        updated = storage.get_event(event_id)
        assert updated.discord_status == DiscordDeliveryStatus.SENT.value
        assert updated.discord_attempts == 1
        assert updated.discord_sent_at is not None
        assert updated.discord_error is None

        # Check telemetry counters
        status = notifier.get_status()
        assert status["delivered_count"] == 1
        assert status["failed_count"] == 0
        assert status["last_delivery_time"] is not None

    def test_rate_limit_429_handling_and_retry(self, storage, sample_event):
        storage.record_event(sample_event)
        event_id = sample_event.event_id

        notifier = AlertDiscordNotifier(
            webhook_url=VALID_TEST_WEBHOOK,
            storage=storage,
            max_retries=3,
            retry_delay_sec=0.01,
        )

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "0.05"}

        resp_200 = MagicMock()
        resp_200.status_code = 200

        with patch.object(notifier.session, "post", side_effect=[resp_429, resp_200]) as mock_post:
            with patch("time.sleep") as mock_sleep:
                success = notifier.send_notification(sample_event)

        assert success is True
        assert mock_post.call_count == 2
        mock_sleep.assert_called_with(0.05)

        updated = storage.get_event(event_id)
        assert updated.discord_status == DiscordDeliveryStatus.SENT.value
        assert updated.discord_attempts == 2

    def test_server_error_and_bounded_retries_fail(self, storage, sample_event):
        storage.record_event(sample_event)
        event_id = sample_event.event_id

        notifier = AlertDiscordNotifier(
            webhook_url=VALID_TEST_WEBHOOK,
            storage=storage,
            max_retries=3,
            retry_delay_sec=0.01,
        )

        resp_500 = MagicMock()
        resp_500.status_code = 500
        resp_500.text = "Internal Server Error"

        with patch.object(notifier.session, "post", return_value=resp_500) as mock_post:
            with patch("time.sleep"):
                success = notifier.send_notification(sample_event)

        assert success is False
        assert mock_post.call_count == 3

        updated = storage.get_event(event_id)
        assert updated.discord_status == DiscordDeliveryStatus.FAILED.value
        assert updated.discord_attempts == 3
        assert "HTTP 500" in updated.discord_error

        status = notifier.get_status()
        assert status["failed_count"] == 1
        assert "HTTP 500" in status["last_error"]

    def test_network_exception_handling_and_redaction(self, storage, sample_event):
        storage.record_event(sample_event)
        event_id = sample_event.event_id

        notifier = AlertDiscordNotifier(
            webhook_url=VALID_TEST_WEBHOOK,
            storage=storage,
            max_retries=2,
            retry_delay_sec=0.01,
        )

        # Exception string that contains the raw webhook URL
        err = requests.RequestException(f"Connection failed to {VALID_TEST_WEBHOOK} on socket")

        with patch.object(notifier.session, "post", side_effect=err):
            with patch("time.sleep"):
                success = notifier.send_notification(sample_event)

        assert success is False
        updated = storage.get_event(event_id)
        assert updated.discord_status == DiscordDeliveryStatus.FAILED.value
        # Raw token must NOT leak into the database error column
        assert "abcdefghijklmnopqrstuvwxyz" not in updated.discord_error
        assert "Connection failed to https://discord.com" in updated.discord_error

    def test_duplicate_prevention_across_restart(self, storage, sample_event):
        """If an event was already sent to Discord in a previous run, do not send again."""
        storage.record_event(sample_event)
        event_id = sample_event.event_id

        # Mark as SENT in DB (simulating prior delivery before restart)
        storage.update_event_discord_status(
            event_id,
            status=DiscordDeliveryStatus.SENT.value,
            attempts=1,
            sent_at="2026-10-18T11:00:00Z",
        )

        notifier = AlertDiscordNotifier(
            webhook_url=VALID_TEST_WEBHOOK,
            storage=storage,
        )

        with patch.object(notifier.session, "post") as mock_post:
            success = notifier.send_notification(sample_event)

        assert success is True
        mock_post.assert_not_called()  # Did not send duplicate webhook


class TestAsynchronousDecoupling:
    """Verify background daemon dispatch does not block price evaluation."""

    def test_dispatch_async_decoupled(self, storage, sample_event):
        notifier = AlertDiscordNotifier(
            webhook_url=VALID_TEST_WEBHOOK,
            storage=storage,
        )

        def slow_send(*args, **kwargs):
            time.sleep(0.1)
            return True

        with patch.object(notifier, "send_notification", side_effect=slow_send) as mock_send:
            start = time.perf_counter()
            notifier.dispatch_async(sample_event)
            elapsed = time.perf_counter() - start

            # dispatch_async should return almost instantaneously without awaiting sleep
            assert elapsed < 0.05

            # Allow worker thread to execute
            time.sleep(0.15)
            mock_send.assert_called_once()


class TestEngineIntegrationWithDiscord:
    """Verify PriceAlertEngine dispatches to Discord on price crossing."""

    def test_engine_dispatches_discord_on_crossing(self, temp_db):
        storage = AlertStorage(temp_db)
        market_data = AlertMarketDataService(base_url="https://fapi.asterdex.com")
        notifier = AlertDiscordNotifier(webhook_url=VALID_TEST_WEBHOOK, storage=storage)

        engine = PriceAlertEngine(
            storage=storage,
            market_data=market_data,
            discord_notifier=notifier,
            cooldown_sec=1.0,
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        alert = PriceAlert(
            alert_id="alt_cross_001",
            symbol="BTCUSDT",
            direction=AlertDirection.PRICE_ABOVE,
            target_price=65000.0,
            alert_type=AlertType.ONE_OFF,
            created_at=now_iso,
            updated_at=now_iso,
        )
        storage.create_alert(alert)

        # Seed initial price below target
        now_ts = time.time()
        market_data._prices["BTCUSDT"] = {
            "mark_price": 64800.0,
            "last_price": 64800.0,
            "mark_timestamp": now_ts,
            "last_timestamp": now_ts,
        }
        engine.on_market_tick(market_data._prices)

        # Seed crossing price above target
        now_ts2 = time.time()
        market_data._prices["BTCUSDT"] = {
            "mark_price": 65100.0,
            "last_price": 65100.0,
            "mark_timestamp": now_ts2,
            "last_timestamp": now_ts2,
        }

        with patch.object(notifier, "dispatch_async") as mock_dispatch:
            events = engine.on_market_tick(market_data._prices)

        assert len(events) == 1
        mock_dispatch.assert_called_once()
        args, kwargs = mock_dispatch.call_args
        dispatched_event = args[0]
        dispatched_alert = kwargs.get("alert")
        is_stale = kwargs.get("is_stale")
        assert dispatched_event.symbol == "BTCUSDT"
        assert dispatched_event.trigger_price == 65100.0
        assert dispatched_alert.alert_id == "alt_cross_001"
        assert is_stale is False


class TestApiDiscordEndpoints:
    """Test REST API status and diagnostic test endpoints."""

    def test_status_endpoint_includes_discord_telemetry(self):
        client = TestClient(app)
        res = client.get("/api/alerts/status")
        assert res.status_code == 200
        data = res.json()
        assert "discord" in data
        assert "enabled" in data["discord"]
        assert "webhook_configured" in data["discord"]
        assert "webhook_masked" in data["discord"]

    def test_test_endpoint_fails_when_disabled(self):
        client = TestClient(app)
        # Force alert_discord to disabled
        with patch.object(alert_discord, "enabled", False):
            res = client.post("/api/alerts/discord/test")
            assert res.status_code == 400
            assert "disabled or not configured" in res.json()["detail"]

    def test_test_endpoint_succeeds_when_enabled(self):
        client = TestClient(app)
        with patch.object(alert_discord, "enabled", True):
            with patch.object(alert_discord, "send_notification", return_value=True) as mock_send:
                with patch.object(alert_discord, "mask_url", return_value="https://discord.com/.../***6789"):
                    res = client.post("/api/alerts/discord/test")
                    assert res.status_code == 200
                    data = res.json()
                    assert data["success"] is True
                    assert data["webhook_masked"] == "https://discord.com/.../***6789"
                    mock_send.assert_called_once()
