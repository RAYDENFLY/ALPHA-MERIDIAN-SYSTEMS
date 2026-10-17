"""Tests for Discord Webhook Monitoring Integration, Mobile Embed Formatting,
Deduplication, Rate Limit Backoff, and Safety Invariants.
"""

from unittest.mock import MagicMock, patch
import pytest
import requests

from src.core.types import (
    CandidateStatus,
    Direction,
    MarketBias,
    RiskAssessment,
    SetupCandidate,
    StructureState,
)
from src.execution.events import (
    PaperEvent,
    PaperEventDispatcher,
    PaperEventType,
    format_pct,
    format_price,
    format_usdt,
    mask_webhook_url,
    validate_webhook_url,
)
from src.execution.paper_engine import PaperExecutionEngine
from src.scheduler.runner import ScannerRunner


# Sample mock webhook URL for testing (never a real secret)
MOCK_WEBHOOK_URL = "https://discord.com/api/webhooks/1234567890/mock_secret_token_abcdef1234"


class TestDiscordConfigurationAndMasking:
    """Verifies safe configuration handling, validation, and URL secret masking."""

    def test_webhook_configured_and_valid(self):
        assert validate_webhook_url(MOCK_WEBHOOK_URL) is True
        assert validate_webhook_url("https://discordapp.com/api/webhooks/999/token") is True

    def test_webhook_disabled_when_empty(self):
        assert validate_webhook_url(None) is False
        assert validate_webhook_url("") is False
        assert validate_webhook_url("   ") is False
        assert mask_webhook_url(None) == "[NOT CONFIGURED]"
        assert mask_webhook_url("") == "[NOT CONFIGURED]"

    def test_webhook_invalid_format_safely_handled(self):
        invalid_urls = [
            "http://discord.com/api/webhooks/123/token",  # Insecure HTTP
            "https://evil.com/webhook",
            "not_a_url",
            "https://discord.com/other/path",
        ]
        for url in invalid_urls:
            assert validate_webhook_url(url) is False

        # Dispatcher with invalid URL must not make HTTP request
        dispatcher = PaperEventDispatcher(discord_webhook_url="https://invalid.domain/hook")
        with patch("requests.post") as mock_post:
            dispatcher.dispatch(
                PaperEvent(
                    event_type=PaperEventType.ORDER_CREATED,
                    timestamp_ms=1700000000000,
                    symbol="BTCUSDT",
                    data={"test": 1},
                )
            )
            mock_post.assert_not_called()

    def test_webhook_url_masking_never_leaks_secret(self):
        masked = mask_webhook_url(MOCK_WEBHOOK_URL)
        assert "mock_secret_token" not in masked
        assert masked.startswith("https://discord.com/.../***")
        assert masked.endswith("1234")

        # Short URL fallback
        short_masked = mask_webhook_url("https://abc")
        assert "***" in short_masked


class TestEventDeduplicationAndIdempotency:
    """Verifies that redundant scans or polling do not spam Discord with duplicate alerts."""

    def test_setup_ready_deduplication_within_ttl(self):
        dispatcher = PaperEventDispatcher(discord_webhook_url=MOCK_WEBHOOK_URL, dedup_ttl_seconds=300.0)

        event = PaperEvent(
            event_type=PaperEventType.SETUP_READY,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            data={
                "candidate_id": "SETUP_BTCUSDT_1700000000000",
                "direction": "LONG",
                "entry_price": 67000.0,
            },
        )

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=204)

            # First dispatch: sent
            dispatcher.dispatch(event)
            assert mock_post.call_count == 1

            # Second dispatch with identical candidate ID: suppressed by deduplication
            dispatcher.dispatch(event)
            assert mock_post.call_count == 1

            # Third dispatch with force=True: sent
            dispatcher.dispatch(event, force=True)
            assert mock_post.call_count == 2

    def test_different_candidates_are_not_suppressed(self):
        dispatcher = PaperEventDispatcher(discord_webhook_url=MOCK_WEBHOOK_URL, dedup_ttl_seconds=300.0)

        event1 = PaperEvent(
            event_type=PaperEventType.SETUP_READY,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            data={"candidate_id": "SETUP_BTCUSDT_1"},
        )
        event2 = PaperEvent(
            event_type=PaperEventType.SETUP_READY,
            timestamp_ms=1700000000000,
            symbol="ETHUSDT",
            data={"candidate_id": "SETUP_ETHUSDT_1"},
        )

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=204)
            dispatcher.dispatch(event1)
            dispatcher.dispatch(event2)
            assert mock_post.call_count == 2


class TestRateLimitingAndResilience:
    """Verifies HTTP 429 rate limit backoff retry and safe exception handling."""

    def test_rate_limit_429_backoff_and_retry(self):
        dispatcher = PaperEventDispatcher(discord_webhook_url=MOCK_WEBHOOK_URL)

        resp_429 = MagicMock(status_code=429, headers={"Retry-After": "0.05"})
        resp_200 = MagicMock(status_code=200)

        with patch("requests.post", side_effect=[resp_429, resp_200]) as mock_post, \
             patch("time.sleep") as mock_sleep:
            event = PaperEvent(
                event_type=PaperEventType.ORDER_CREATED,
                timestamp_ms=1700000000000,
                symbol="BTCUSDT",
                data={"order_id": "ORD_1"},
            )
            dispatcher.dispatch(event)

            assert mock_post.call_count == 2
            mock_sleep.assert_called_once_with(0.05)

    def test_rate_limit_429_excessive_drop(self):
        dispatcher = PaperEventDispatcher(discord_webhook_url=MOCK_WEBHOOK_URL)

        # Retry-After is 15 seconds: should NOT block scanner, should drop gracefully
        resp_429 = MagicMock(status_code=429, headers={"Retry-After": "15.0"})

        with patch("requests.post", return_value=resp_429) as mock_post, \
             patch("time.sleep") as mock_sleep:
            event = PaperEvent(
                event_type=PaperEventType.ORDER_CREATED,
                timestamp_ms=1700000000000,
                symbol="BTCUSDT",
                data={"order_id": "ORD_2"},
            )
            dispatcher.dispatch(event)

            assert mock_post.call_count == 1
            mock_sleep.assert_not_called()

    def test_network_failure_safety_timeout_and_error(self):
        dispatcher = PaperEventDispatcher(discord_webhook_url=MOCK_WEBHOOK_URL)

        failures = [
            requests.Timeout("Connection timed out after 5.0s"),
            requests.ConnectionError("Failed to establish a new connection"),
            Exception("Unexpected OS socket error"),
        ]

        for failure in failures:
            with patch("requests.post", side_effect=failure):
                # Must never raise exception to caller
                dispatcher.dispatch(
                    PaperEvent(
                        event_type=PaperEventType.SYSTEM_ERROR,
                        timestamp_ms=1700000000000,
                        symbol="BTCUSDT",
                        data={"error": "test"},
                    ),
                    force=True,
                )


class TestMobileEmbedFormatting:
    """Verifies embed colors, lock-screen previews, SMC/SnR evidence, and NO TRADE labels."""

    def test_setup_ready_embed_long_and_short(self):
        dispatcher = PaperEventDispatcher(discord_webhook_url=MOCK_WEBHOOK_URL)

        # LONG Setup
        event_long = PaperEvent(
            event_type=PaperEventType.SETUP_READY,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            data={
                "candidate_id": "SETUP_BTC_LONG",
                "direction": "LONG",
                "setup_type": "SMC/SnR (1H)",
                "entry_price": 67500.0,
                "stop_loss": 66800.0,
                "take_profit": 68900.0,
                "gross_rr": 2.0,
                "break_even_price": 67567.53,
                "invalidation_price": 66750.0,
                "reference_price": 67450.0,
                "reference_data_age_hours": 0.25,
                "bias_4h": "BULLISH",
                "structure_1h": "UPTREND",
                "evidence": "• 1H Bullish Order Block\n• Asian session liquidity swept",
                "ml_score": "68.5%",
            },
        )
        embed_long = dispatcher._build_embed(event_long)
        assert embed_long["color"] == 0x2ECC71  # Green
        assert "LONG" in embed_long["title"]
        assert "67,500.00" in embed_long["description"]
        assert any(f["name"] == "Est. Fee Break-Even" for f in embed_long["fields"])
        assert any(f["name"] == "SMC & SnR Evidence" for f in embed_long["fields"])
        assert any("Informative only — not an execution gate" in f["value"] for f in embed_long["fields"])

        # SHORT Setup
        event_short = PaperEvent(
            event_type=PaperEventType.SETUP_READY,
            timestamp_ms=1700000000000,
            symbol="ETHUSDT",
            data={
                "candidate_id": "SETUP_ETH_SHORT",
                "direction": "SHORT",
                "setup_type": "SMC/SnR (1H)",
                "entry_price": 2650.0,
                "stop_loss": 2700.0,
                "take_profit": 2550.0,
                "gross_rr": 2.0,
                "break_even_price": 2647.35,
                "invalidation_price": 2705.0,
                "reference_price": 2645.0,
                "bias_4h": "BEARISH",
                "structure_1h": "DOWNTREND",
                "ml_score": "71.2%",
            },
        )
        embed_short = dispatcher._build_embed(event_short)
        assert embed_short["color"] == 0xE74C3C  # Red
        assert "SHORT" in embed_short["title"]

    def test_setup_rejected_no_trade_label(self):
        dispatcher = PaperEventDispatcher(discord_webhook_url=MOCK_WEBHOOK_URL)
        event = PaperEvent(
            event_type=PaperEventType.SETUP_REJECTED,
            timestamp_ms=1700000000000,
            symbol="SOLUSDT",
            data={
                "candidate_id": "REJ_SOL_1",
                "direction": "LONG",
                "reason": "1H low breached structural invalidation level.",
                "status": "INVALIDATED",
                "reference_price": 155.20,
            },
        )
        embed = dispatcher._build_embed(event)
        assert embed["color"] == 0xF39C12  # Amber
        assert "[NO TRADE]" in embed["title"]
        assert "[NO TRADE]" in embed["description"]
        assert any(f["name"] == "Status" and "NO TRADE" in f["value"] for f in embed["fields"])

    def test_order_lifecycle_embeds(self):
        dispatcher = PaperEventDispatcher(discord_webhook_url=MOCK_WEBHOOK_URL)

        # ORDER_CREATED
        ev_created = PaperEvent(
            event_type=PaperEventType.ORDER_CREATED,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            data={
                "order_id": "PORD_BTC_1",
                "direction": "LONG",
                "order_type": "LIMIT",
                "price": 67000.0,
                "quantity": 0.001,
                "notional_usdt": 67.0,
                "leverage": 10.0,
                "stop_loss": 66500.0,
                "take_profit": 68000.0,
                "ttl_bars": 12,
            },
        )
        embed_created = dispatcher._build_embed(ev_created)
        assert embed_created["color"] == 0x3498DB  # Blue
        assert "ORDER PENDING" in embed_created["title"]

        # ORDER_FILLED
        ev_filled = PaperEvent(
            event_type=PaperEventType.ORDER_FILLED,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            data={
                "order_id": "PORD_BTC_1",
                "direction": "LONG",
                "fill_price": 67000.0,
                "planned_price": 67000.0,
                "slippage_usdt": 0.0,
                "slippage_pct": 0.0,
                "quantity": 0.001,
                "fee": 0.0134,
                "stop_loss": 66500.0,
                "take_profit": 68000.0,
                "trade_id": "PTRD_BTC_1",
            },
        )
        embed_filled = dispatcher._build_embed(ev_filled)
        assert embed_filled["color"] == 0x2ECC71  # Green
        assert "ENTRY FILLED" in embed_filled["title"]

        # TP_HIT
        ev_tp = PaperEvent(
            event_type=PaperEventType.TP_HIT,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            data={
                "trade_id": "PTRD_BTC_1",
                "direction": "LONG",
                "exit_price": 68000.0,
                "planned_tp": 68000.0,
                "pnl_gross": 1.0,
                "pnl_net": 0.95,
                "fee_paid": 0.05,
                "exit_reason": "TAKE_PROFIT",
                "realized_rr": 2.0,
                "duration": "3.5h",
            },
        )
        embed_tp = dispatcher._build_embed(ev_tp)
        assert embed_tp["color"] == 0x2ECC71
        assert "TAKE PROFIT HIT" in embed_tp["title"]

        # SL_HIT
        ev_sl = PaperEvent(
            event_type=PaperEventType.SL_HIT,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            data={
                "trade_id": "PTRD_BTC_1",
                "direction": "LONG",
                "exit_price": 66500.0,
                "planned_sl": 66500.0,
                "pnl_gross": -0.5,
                "pnl_net": -0.55,
                "fee_paid": 0.05,
                "exit_reason": "STOP_LOSS",
                "realized_rr": -1.0,
                "duration": "2.0h",
            },
        )
        embed_sl = dispatcher._build_embed(ev_sl)
        assert embed_sl["color"] == 0xE74C3C  # Red
        assert "STOP LOSS HIT" in embed_sl["title"]

        # POSITION_CLOSED
        ev_closed = PaperEvent(
            event_type=PaperEventType.POSITION_CLOSED,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            data={
                "trade_id": "PTRD_BTC_1",
                "direction": "LONG",
                "exit_price": 67200.0,
                "exit_reason": "EMERGENCY_EXIT",
                "pnl_gross": 0.2,
                "pnl_net": 0.15,
                "fee_paid": 0.05,
                "duration": "1.0h",
            },
        )
        embed_closed = dispatcher._build_embed(ev_closed)
        assert embed_closed["color"] == 0x9B59B6  # Purple
        assert "POSITION CLOSED" in embed_closed["title"]

    def test_risk_blocked_and_system_error_embeds(self):
        dispatcher = PaperEventDispatcher(discord_webhook_url=MOCK_WEBHOOK_URL)

        # RISK_BLOCKED
        ev_risk = PaperEvent(
            event_type=PaperEventType.RISK_BLOCKED,
            timestamp_ms=1700000000000,
            symbol="SOLUSDT",
            data={
                "action": "ORDER_ENTRY",
                "rule": "Max Open Positions",
                "reason": "Max open positions limit (3) reached.",
                "details": "Active positions: 3 / 3 allowed",
            },
        )
        embed_risk = dispatcher._build_embed(ev_risk)
        assert embed_risk["color"] == 0xE67E22  # Warning Orange
        assert "RISK BLOCKED" in embed_risk["title"]

        # SYSTEM_ERROR
        ev_err = PaperEvent(
            event_type=PaperEventType.SYSTEM_ERROR,
            timestamp_ms=1700000000000,
            symbol="SYSTEM",
            data={
                "component": "ScannerRunner",
                "severity": "CRITICAL",
                "error_message": "Database lock timeout after 30s",
            },
        )
        embed_err = dispatcher._build_embed(ev_err)
        assert embed_err["color"] == 0xC0392B  # Crimson
        assert "SYSTEM ERROR" in embed_err["title"]

        # SCANNER_STATUS
        ev_status = PaperEvent(
            event_type=PaperEventType.SCANNER_STATUS,
            timestamp_ms=1700000000000,
            symbol="SYSTEM",
            data={
                "mode": "PAPER",
                "scanner_state": "IDLE",
                "equity": 16.50,
                "open_positions": 1,
                "pending_orders": 2,
                "candle_age_seconds": 45,
            },
        )
        embed_status = dispatcher._build_embed(ev_status)
        assert embed_status["color"] == 0x1ABC9C  # Teal
        assert "HEARTBEAT" in embed_status["title"]


class TestDiagnosticPing:
    """Verifies operational diagnostic verification ping helper."""

    def test_diagnostic_ping_success(self):
        dispatcher = PaperEventDispatcher(discord_webhook_url=MOCK_WEBHOOK_URL)
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=204)
            result = dispatcher.send_diagnostic_ping()
            assert result is True
            assert mock_post.call_count == 1
            payload = mock_post.call_args[1]["json"]
            assert "DIAGNOSTIC TEST" in payload["embeds"][0]["title"]

    def test_diagnostic_ping_disabled(self):
        dispatcher = PaperEventDispatcher(discord_webhook_url=None)
        assert dispatcher.send_diagnostic_ping() is False


class TestNumberFormattingHelpers:
    """Verifies format_price, format_usdt, and format_pct helpers."""

    def test_format_helpers(self):
        assert format_price(67500.5) == "$67,500.50"
        assert format_price(0.08234) == "$0.0823"
        assert format_price(None) == "N/A"

        assert format_usdt(12.3456) == "12.3456 USDT"
        assert format_usdt(12.3456, sign=True) == "+12.3456 USDT"
        assert format_usdt(-5.2, sign=True) == "-5.2000 USDT"
        assert format_usdt(None) == "N/A"

        assert format_pct(0.0185) == "1.85%"
        assert format_pct(0.0185, sign=True) == "+1.85%"
        assert format_pct(-0.025, sign=True) == "-2.50%"
        assert format_pct(None) == "N/A"
