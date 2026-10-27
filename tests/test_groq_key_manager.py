"""Comprehensive Test Suite for Multi-Key Groq Fallback & Automatic Quota Rotation.

Tests:
1. Primary key succeeds — no fallback.
2. Primary quota exhausted — fallback A succeeds.
3. Primary and A exhausted — fallback B succeeds.
4. All keys exhausted — structured failure.
5. Temporary 429 — respects retry policy and backoff (not marked exhausted).
6. HTTP 401 — invalid key handling without quota rotation.
7. HTTP 400 — no unnecessary key rotation.
8. Timeout — bounded retry behavior.
9. Duplicate API keys — deduplicated.
10. Empty fallback keys — ignored.
11. Concurrent requests — no state corruption (process-local concurrency).
12. LIVE fail-closed invariant — no fabricated AI output or live orders when provider fails.
13. GroqAnalystClient multi-key rotation and quantitative fallback.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.ai.groq_key_manager import GroqKeyEntry, GroqKeyManager
from src.ai.groq_autotrader import (
    GroqAutoTrader,
    Stage2Result,
    Stage2FinalistItem,
    Stage3SetupOutput,
)
from src.ai_analyst.groq_client import GroqAnalystClient
from src.ai_analyst.types import QuantitativeMarketSnapshot
from config.settings import GroqAutoTraderConfig


# ==============================================================================
# 1. Primary key succeeds — no fallback
# ==============================================================================

def test_primary_key_succeeds_no_fallback():
    km = GroqKeyManager(
        primary_key="gsk-primary-111",
        fallback_a_key="gsk-fallback-a-222",
        fallback_b_key="gsk-fallback-b-333",
    )
    cfg = GroqAutoTraderConfig(
        api_key="gsk-primary-111",
        api_key_fallback_a="gsk-fallback-a-222",
        api_key_fallback_b="gsk-fallback-b-333",
    )
    trader = GroqAutoTrader(config=cfg, key_manager=km)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"status": "PASS", "confidence": 85})}}]
    }

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = trader._call_groq_json([{"role": "user", "content": "ping"}])

        assert result["status"] == "PASS"
        assert mock_post.call_count == 1
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer gsk-primary-111"

        primary_entry = km.get_key_by_alias("PRIMARY")
        assert primary_entry.total_successes == 1
        assert primary_entry.is_exhausted is False

        fallback_a = km.get_key_by_alias("FALLBACK_A")
        assert fallback_a.total_requests == 0


# ==============================================================================
# 2. Primary quota exhausted — fallback A succeeds
# ==============================================================================

def test_primary_quota_exhausted_fallback_a_succeeds():
    km = GroqKeyManager(
        primary_key="gsk-primary-111",
        fallback_a_key="gsk-fallback-a-222",
        fallback_b_key="gsk-fallback-b-333",
    )
    cfg = GroqAutoTraderConfig(
        api_key="gsk-primary-111",
        api_key_fallback_a="gsk-fallback-a-222",
        api_key_fallback_b="gsk-fallback-b-333",
    )
    trader = GroqAutoTrader(config=cfg, key_manager=km)

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"retry-after": "5.0"}
    resp_429.text = '{"error": {"message": "Rate limit reached: tokens_per_day limit reached for your tier"}}'

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"candidate": "ETHUSDT", "action": "EXECUTE"})}}]
    }

    with patch("requests.post", side_effect=[resp_429, resp_200]) as mock_post:
        result = trader._call_groq_json([{"role": "user", "content": "evaluate"}])

        assert result["candidate"] == "ETHUSDT"
        assert mock_post.call_count == 2

        # Primary failed with quota exhaustion
        primary = km.get_key_by_alias("PRIMARY")
        assert primary.is_exhausted is True
        assert "quota" in primary.exhausted_reason

        # Fallback A succeeded
        fallback_a = km.get_key_by_alias("FALLBACK_A")
        assert fallback_a.total_successes == 1
        assert fallback_a.is_exhausted is False

        # Fallback B never called
        fallback_b = km.get_key_by_alias("FALLBACK_B")
        assert fallback_b.total_requests == 0


# ==============================================================================
# 3. Primary and A exhausted — fallback B succeeds
# ==============================================================================

def test_primary_and_a_exhausted_fallback_b_succeeds():
    km = GroqKeyManager(
        primary_key="gsk-primary-111",
        fallback_a_key="gsk-fallback-a-222",
        fallback_b_key="gsk-fallback-b-333",
    )
    cfg = GroqAutoTraderConfig(
        api_key="gsk-primary-111",
        api_key_fallback_a="gsk-fallback-a-222",
        api_key_fallback_b="gsk-fallback-b-333",
    )
    trader = GroqAutoTrader(config=cfg, key_manager=km)

    resp_p_429 = MagicMock()
    resp_p_429.status_code = 429
    resp_p_429.headers = {}
    resp_p_429.text = '{"error": {"message": "requests_per_day quota exceeded"}}'

    resp_a_429 = MagicMock()
    resp_a_429.status_code = 429
    resp_a_429.headers = {}
    resp_a_429.text = '{"error": {"message": "insufficient_quota: account daily limit reached"}}'

    resp_b_200 = MagicMock()
    resp_b_200.status_code = 200
    resp_b_200.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"decision": "EXECUTE"})}}]
    }

    with patch("requests.post", side_effect=[resp_p_429, resp_a_429, resp_b_200]) as mock_post:
        result = trader._call_groq_json([{"role": "user", "content": "evaluate"}])

        assert result["decision"] == "EXECUTE"
        assert mock_post.call_count == 3

        primary = km.get_key_by_alias("PRIMARY")
        assert primary.is_exhausted is True

        fallback_a = km.get_key_by_alias("FALLBACK_A")
        assert fallback_a.is_exhausted is True

        fallback_b = km.get_key_by_alias("FALLBACK_B")
        assert fallback_b.total_successes == 1
        assert fallback_b.is_exhausted is False


# ==============================================================================
# 4. All keys exhausted — structured failure
# ==============================================================================

def test_all_keys_exhausted_structured_failure():
    km = GroqKeyManager(
        primary_key="gsk-primary-111",
        fallback_a_key="gsk-fallback-a-222",
        fallback_b_key="",
    )
    cfg = GroqAutoTraderConfig(
        api_key="gsk-primary-111",
        api_key_fallback_a="gsk-fallback-a-222",
        api_key_fallback_b="",
    )
    trader = GroqAutoTrader(config=cfg, key_manager=km)

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {}
    resp_429.text = '{"error": {"message": "tokens_per_day quota limit reached"}}'

    with patch("requests.post", side_effect=[resp_429, resp_429]):
        with pytest.raises(RuntimeError) as exc_info:
            trader._call_groq_json([{"role": "user", "content": "evaluate"}])

        assert "All Groq API attempts failed" in str(exc_info.value)

    assert km.get_key_by_alias("PRIMARY").is_exhausted is True
    assert km.get_key_by_alias("FALLBACK_A").is_exhausted is True
    assert len(km.get_available_keys()) == 0

    # Next attempt fails immediately before any HTTP calls
    with pytest.raises(RuntimeError) as exc_info2:
        trader._call_groq_json([{"role": "user", "content": "evaluate"}])
    assert "All configured Groq API keys are currently exhausted or invalid" in str(exc_info2.value)


# ==============================================================================
# 5. Temporary 429 — respects retry policy and backoff
# ==============================================================================

def test_temporary_429_respects_retry_policy_not_exhausted():
    km = GroqKeyManager(primary_key="gsk-primary-111")

    category, retry_delay = GroqKeyManager.classify_groq_error(
        status_code=429,
        response_text="Rate limit reached for requests per minute (RPM). Please try again in 4.2s.",
        headers={"Retry-After": "4.2"},
    )

    assert category == "RATE_LIMITED"
    assert 4.0 <= retry_delay <= 10.0

    km.mark_rate_limited("PRIMARY", retry_after_sec=retry_delay, reason="TPM limit")
    primary = km.get_key_by_alias("PRIMARY")

    # Key is temporarily rate limited but NOT permanently exhausted
    assert primary.is_exhausted is False
    assert primary.rate_limited_until > time.time()
    assert primary.is_available() is False

    # After cooldown expires, key becomes available again
    assert primary.is_available(now=time.time() + 15.0) is True


# ==============================================================================
# 6. HTTP 401 — invalid key handling without quota rotation
# ==============================================================================

def test_http_401_invalid_key_handling_without_quota_rotation():
    km = GroqKeyManager(
        primary_key="gsk-bad-primary",
        fallback_a_key="gsk-good-fallback",
    )
    cfg = GroqAutoTraderConfig(
        api_key="gsk-bad-primary",
        api_key_fallback_a="gsk-good-fallback",
    )
    trader = GroqAutoTrader(config=cfg, key_manager=km)

    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.headers = {}
    resp_401.text = '{"error": {"message": "Invalid API Key"}}'

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"status": "PASS"})}}]
    }

    with patch("requests.post", side_effect=[resp_401, resp_200]):
        result = trader._call_groq_json([{"role": "user", "content": "test"}])

        assert result["status"] == "PASS"

        primary = km.get_key_by_alias("PRIMARY")
        assert primary.is_invalid is True
        # Must NOT be marked as quota exhausted
        assert primary.is_exhausted is False

        fallback_a = km.get_key_by_alias("FALLBACK_A")
        assert fallback_a.total_successes == 1


# ==============================================================================
# 7. HTTP 400 — no unnecessary key rotation
# ==============================================================================

def test_http_400_bad_request_no_unnecessary_key_rotation():
    km = GroqKeyManager(
        primary_key="gsk-primary-111",
        fallback_a_key="gsk-fallback-a-222",
    )
    cfg = GroqAutoTraderConfig(
        api_key="gsk-primary-111",
        api_key_fallback_a="gsk-fallback-a-222",
    )
    trader = GroqAutoTrader(config=cfg, key_manager=km)

    resp_400 = MagicMock()
    resp_400.status_code = 400
    resp_400.headers = {}
    resp_400.text = '{"error": {"message": "Failed to generate JSON: schema violation"}}'

    with patch("requests.post", return_value=resp_400) as mock_post:
        with pytest.raises((RuntimeError, ValueError)):
            trader._call_groq_json([{"role": "user", "content": "bad"}])

        # HTTP 400 must NOT cycle through fallback keys (would only repeat bad request)
        primary = km.get_key_by_alias("PRIMARY")
        fallback_a = km.get_key_by_alias("FALLBACK_A")
        assert primary.is_exhausted is False
        assert fallback_a.total_requests == 0


# ==============================================================================
# 8. Timeout — bounded retry behavior
# ==============================================================================

def test_timeout_bounded_retry_behavior():
    km = GroqKeyManager(
        primary_key="gsk-primary-111",
        fallback_a_key="gsk-fallback-a-222",
    )
    cfg = GroqAutoTraderConfig(
        api_key="gsk-primary-111",
        api_key_fallback_a="gsk-fallback-a-222",
    )
    trader = GroqAutoTrader(config=cfg, key_manager=km)

    with patch("requests.post", side_effect=requests.exceptions.Timeout("Connection timed out")):
        with pytest.raises(RuntimeError) as exc_info:
            trader._call_groq_json([{"role": "user", "content": "test"}])

        assert "All Groq API attempts failed" in str(exc_info.value)

    # Provider temporary failure does NOT permanently mark exhausted
    primary = km.get_key_by_alias("PRIMARY")
    fallback_a = km.get_key_by_alias("FALLBACK_A")
    assert primary.is_exhausted is False
    assert fallback_a.is_exhausted is False


# ==============================================================================
# 9. Duplicate API keys — deduplicated
# ==============================================================================

def test_duplicate_api_keys_deduplicated():
    km = GroqKeyManager(
        primary_key="gsk-same-secret-123",
        fallback_a_key="gsk-same-secret-123",  # duplicate
        fallback_b_key="gsk-different-secret-456",
    )

    aliases = km.get_configured_aliases()
    assert aliases == ["PRIMARY", "FALLBACK_B"]
    assert len(aliases) == 2


# ==============================================================================
# 10. Empty fallback keys — ignored
# ==============================================================================

def test_empty_fallback_keys_ignored():
    km = GroqKeyManager(
        primary_key="gsk-primary-only",
        fallback_a_key="",
        fallback_b_key="   ",
    )

    aliases = km.get_configured_aliases()
    assert aliases == ["PRIMARY"]
    assert km.has_keys() is True


# ==============================================================================
# 11. Concurrent requests — thread-safety
# ==============================================================================

def test_concurrent_requests_no_state_corruption():
    km = GroqKeyManager(
        primary_key="gsk-primary-111",
        fallback_a_key="gsk-fallback-a-222",
        fallback_b_key="gsk-fallback-b-333",
    )

    def worker(worker_id: int):
        for i in range(50):
            avail = km.get_available_keys()
            if avail:
                alias = avail[0].alias
                if i % 5 == 0:
                    km.mark_rate_limited(alias, retry_after_sec=0.1)
                else:
                    km.mark_success(alias)
            status = km.get_status()
            assert status["total_configured_keys"] == 3

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, i) for i in range(10)]
        for f in futures:
            f.result()

    status = km.get_status()
    assert status["total_configured_keys"] == 3
    total_ops = sum(k["total_requests"] for k in status["keys"])
    assert total_ops > 0


# ==============================================================================
# 12. LIVE fail-closed invariant — no fabricated AI output or live orders
# ==============================================================================

def test_live_fail_closed_invariant():
    """Verify that in LIVE mode (is_live=True), Groq failure strictly blocks order execution."""
    km = GroqKeyManager(
        primary_key="gsk-exhausted",
        fallback_a_key="",
        fallback_b_key="",
    )
    km.mark_quota_exhausted("PRIMARY", "insufficient_quota")

    cfg = GroqAutoTraderConfig(
        api_key="gsk-exhausted",
        api_key_fallback_a="",
        api_key_fallback_b="",
    )

    mock_coord = MagicMock()
    mock_coord.current_equity = 100.0
    mock_coord.config.strategy.live_strategy_id = "15M_SWEEP_RECLAIM_SMC"
    mock_strat = MagicMock()
    mock_strat.strategy_id = "15M_SWEEP_RECLAIM_SMC"
    mock_strat.is_live = True
    mock_strat.evaluate.return_value = (None, "NO_SETUP")
    mock_coord.sweep_reclaim_strategy = mock_strat

    mock_specs = MagicMock(
        tick_size=0.01,
        step_size=0.01,
        min_qty=0.01,
        min_notional=5.0,
    )
    mock_adapter = MagicMock()
    mock_adapter.get_symbol_info.return_value = mock_specs
    mock_coord.exchange = mock_adapter

    mock_inspector = MagicMock()
    mock_inspector.inspect_coin.return_value = {
        "meta": {"mark_price": 100.0, "last_price": 100.0},
        "orderbook": {"bids": [], "asks": []},
        "indicators": {},
    }
    mock_inspector.get_symbol_specs.return_value = mock_specs

    trader = GroqAutoTrader(
        adapter=mock_adapter,
        config=cfg,
        key_manager=km,
        coordinator=mock_coord,
        inspector_service=mock_inspector,
    )

    from src.ai.groq_autotrader import Stage1Result
    mock_stage1 = Stage1Result(
        market_regime={"regime": "TRENDING_BULL"},
        top_10=[],
        total_screened_pairs=10,
        execution_time_ms=50.0,
    )

    mock_stage2 = Stage2Result(
        top_3=[
            Stage2FinalistItem(
                rank=1,
                symbol="SOLUSDT",
                category="TOP_PICK",
                score=85.0,
                thesis="Test SMC thesis",
            )
        ],
        eliminated_summary={},
        model_used="qwen/qwen3.8-27b",
        provider_failure=False,
    )

    # 1. LIVE_AUTO execution venue
    stage3_live, receipt, conf = trader.run_stage3_finalist_and_order(
        stage1=mock_stage1,
        stage2=mock_stage2,
        execution_venue="LIVE_AUTO",
        strategy_id="15M_SWEEP_RECLAIM_SMC",
    )

    # LIVE Invariant: Must return WAIT, REJECTED, and provider_failure=True
    assert stage3_live.decision == "WAIT"
    assert stage3_live.status == "REJECTED"
    assert stage3_live.provider_failure is True
    assert stage3_live.strategy_eligible is False
    assert receipt is not None
    assert receipt["status"] == "REJECTED"
    assert "PROVIDER_FAILURE" in receipt["reason"]

    # 2. PAPER execution venue - deterministic fallback allowed but marked provider_failure=True
    stage3_paper, p_receipt, _ = trader.run_stage3_finalist_and_order(
        stage1=mock_stage1,
        stage2=mock_stage2,
        execution_venue="PAPER",
        strategy_id="15M_SWEEP_RECLAIM_SMC",
    )
    assert stage3_paper.provider_failure is True


# ==============================================================================
# 13. GroqAnalystClient multi-key rotation and quantitative fallback
# ==============================================================================

def test_groq_analyst_client_key_rotation():
    """Verify GroqAnalystClient switches to Fallback A when Primary key is quota-exhausted."""
    km = GroqKeyManager(
        primary_key="gsk-primary-analyst",
        fallback_a_key="gsk-fallback-a-analyst",
        fallback_b_key="",
    )
    client = GroqAnalystClient(key_manager=km)

    snapshot = QuantitativeMarketSnapshot(
        symbol="SOLUSDT",
        timeframe="1h",
        current_price=101.50,
        volume_24h_usdt=5000000.0,
        spread_bps=2.0,
        atr_14=2.5,
        atr_pct=2.4,
        swing_high=105.0,
        swing_low=98.0,
        demand_zone_lower=99.0,
        demand_zone_upper=100.5,
        supply_zone_lower=104.0,
        supply_zone_upper=105.5,
        recent_liquidity_sweep=True,
        orderbook_bid_depth_25bps=50000.0,
        orderbook_ask_depth_25bps=30000.0,
        orderbook_imbalance_25bps=25.0,
        btc_price=80100.0,
        btc_zone_status="INSIDE_ZONE",
        relative_strength_vs_btc_24h=3.2,
        data_timestamp_ms=int(time.time() * 1000),
        candle_close_time_ms=int(time.time() * 1000) - 60000,
        data_age_sec=60.0,
        is_stale=False,
    )

    # Mock Groq client SDK responses
    mock_primary_sdk = MagicMock()
    mock_primary_sdk.chat.completions.create.side_effect = Exception("Rate limit 429: tokens_per_day limit reached")

    mock_fallback_sdk = MagicMock()
    mock_completion = MagicMock()
    mock_completion.choices = [
        MagicMock(message=MagicMock(content=json.dumps({
            "primary_bias": "BULLISH",
            "recommendation": "LONG",
            "confidence_score": 85,
            "uncertainty_score": 25,
            "executive_summary": "Strong bullish sweep and reclaim at 99.5 support.",
            "long_scenario": {
                "confluence_score": 88,
                "suggested_entry": 100.5,
                "suggested_stop_loss": 98.8,
                "take_profit_1": 104.0,
            },
            "short_scenario": {
                "confluence_score": 35,
                "suggested_entry": 104.5,
                "suggested_stop_loss": 106.0,
            },
            "penjelasan_indonesia": {
                "ringkasan_struktur": "Struktur bullish valid.",
            }
        })))
    ]
    mock_fallback_sdk.chat.completions.create.return_value = mock_completion

    # Cache the mock SDK instances
    client._client_cache["PRIMARY"] = mock_primary_sdk
    client._client_cache["FALLBACK_A"] = mock_fallback_sdk

    result = client.analyze(snapshot, risk_budget_usdt=1.00)

    # Primary quota was marked exhausted
    assert km.get_key_by_alias("PRIMARY").is_exhausted is True

    # Fallback A succeeded and returned result
    assert km.get_key_by_alias("FALLBACK_A").total_successes == 1
    assert result.recommendation == "LONG"
    assert result.confidence_score == 85


# ==============================================================================
# 14. HTTP 413 Payload Too Large / Token Rate Limit Classification & Rotation
# ==============================================================================

def test_http_413_payload_too_large_rate_limit_classification():
    """Verify Groq HTTP 413 token rate limit responses are classified as RATE_LIMITED and rotate keys."""
    cat, delay = GroqKeyManager.classify_groq_error(
        status_code=413,
        response_text='{"error":{"message":"Request too large for model `qwen/qwen3.8-27b` on input tokens per minute (ITPM): Limit 7000, Requested 15797","type":"tokens","code":"rate_limit_exceeded"}}',
        headers={"retry-after": "5"},
    )
    assert cat == "RATE_LIMITED"
    assert delay == 5.0

    km = GroqKeyManager(
        primary_key="gsk-primary-111",
        fallback_a_key="gsk-fallback-a-222",
    )
    trader = GroqAutoTrader(key_manager=km)

    resp_413 = MagicMock()
    resp_413.status_code = 413
    resp_413.text = '{"error":{"message":"Request too large on input tokens per minute (ITPM): Limit 7000","code":"rate_limit_exceeded"}}'
    resp_413.headers = {}

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"decision": "WAIT", "reasoning": "Compact prompt success"})}}]
    }
    resp_200.headers = {}

    with patch("requests.post", side_effect=[resp_413, resp_200]):
        out = trader._call_groq_json([{"role": "user", "content": "test"}])

    assert out["decision"] == "WAIT"
    assert out["_key_alias_used"] == "FALLBACK_A"
    assert km.get_key_by_alias("PRIMARY").total_failures >= 1
    assert km.get_key_by_alias("FALLBACK_A").total_successes == 1

