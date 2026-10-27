"""Groq LLM Client and Prompt Synthesis Engine for AI Trading Analyst.

Interacts with Groq API using configurable model IDs (e.g. qwen/qwen3.8-27b, openai/gpt-oss-120b).
Enforces structured JSON output and institutional Bahasa Indonesia narrative.
Provides deterministic quantitative fallback if the Groq API is unavailable or rate-limited.
"""

from datetime import datetime, timezone
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from src.ai_analyst.types import (
    AIAnalystResult,
    ExecutionPlanDetails,
    IndonesianExplanation,
    QuantitativeMarketSnapshot,
    TradeScenario,
)
from src.ai_analyst.risk_calculator import AIRiskCalculator
from src.ai.groq_key_manager import GroqKeyManager
from src.core.logger import logger
from dotenv import load_dotenv

load_dotenv()

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False


class GroqAnalystClient:
    """Manages Groq LLM API requests and structured trade synthesis."""

    DEFAULT_MODEL = "qwen/qwen3.8-27b"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_id: Optional[str] = None,
        risk_calculator: Optional[AIRiskCalculator] = None,
        key_manager: Optional[GroqKeyManager] = None,
        fallback_a_key: Optional[str] = None,
        fallback_b_key: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GROQ_API_KEY") or os.getenv("groq")
        self.model_id = model_id or os.getenv("GROQ_MODEL") or self.DEFAULT_MODEL
        self.risk_calc = risk_calculator or AIRiskCalculator()

        if key_manager is not None:
            self.key_manager = key_manager
        else:
            self.key_manager = GroqKeyManager(
                primary_key=self.api_key,
                fallback_a_key=fallback_a_key or os.getenv("GROQ_API_KEY_FALLBACK_A"),
                fallback_b_key=fallback_b_key or os.getenv("GROQ_API_KEY_FALLBACK_B"),
            )

        self._client_cache: Dict[str, Any] = {}
        self._groq_client = None

        if GROQ_AVAILABLE and self.api_key:
            try:
                self._groq_client = Groq(api_key=self.api_key)
                self._client_cache["PRIMARY"] = self._groq_client
            except Exception as e:
                logger.warning(f"Failed to initialize Groq client: {e}")

    def _get_client_for_entry(self, key_entry: Any) -> Optional[Any]:
        if not GROQ_AVAILABLE:
            return None
        alias = key_entry.alias
        if alias in self._client_cache and self._client_cache[alias] is not None:
            return self._client_cache[alias]
        try:
            client = Groq(api_key=key_entry.key)
            self._client_cache[alias] = client
            return client
        except Exception as e:
            logger.warning(f"[GROQ_ANALYST] Failed to initialize Groq SDK client for {key_entry.alias}: {e}")
            return None

    def analyze(
        self,
        snapshot: QuantitativeMarketSnapshot,
        risk_budget_usdt: float = 1.00,
        model_override: Optional[str] = None,
    ) -> AIAnalystResult:
        """Execute LLM qualitative analysis and synthesize comprehensive advisory result."""
        active_model = model_override or self.model_id
        t_start = time.time()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        dt_utc = datetime.now(timezone.utc).isoformat()
        analysis_id = f"AI_{snapshot.symbol}_{snapshot.timeframe}_{now_ms}"

        # If Groq client is not available or no keys configured:
        if not GROQ_AVAILABLE or not self.key_manager.has_keys():
            logger.info("Groq client or API key unavailable. Generating deterministic quantitative analysis.")
            return self._generate_fallback_analysis(
                analysis_id=analysis_id,
                snapshot=snapshot,
                risk_budget_usdt=risk_budget_usdt,
                model_id=f"{active_model} (Rule-Based Fallback)",
                error_note="Groq API key not configured or client offline.",
            )

        # Backward compatibility for tests that explicitly set client._groq_client = None
        if self._groq_client is None and len(self.key_manager.get_configured_aliases()) == 1:
            logger.info("Groq client disabled. Generating deterministic quantitative analysis.")
            return self._generate_fallback_analysis(
                analysis_id=analysis_id,
                snapshot=snapshot,
                risk_budget_usdt=risk_budget_usdt,
                model_id=f"{active_model} (Rule-Based Fallback)",
                error_note="Groq client disabled.",
            )

        candidate_keys = self.key_manager.get_available_keys(force_include_cooling=True)
        if not candidate_keys:
            logger.warning("[GROQ_ANALYST] All configured Groq API keys are currently exhausted or invalid.")
            return self._generate_fallback_analysis(
                analysis_id=analysis_id,
                snapshot=snapshot,
                risk_budget_usdt=risk_budget_usdt,
                model_id=f"{active_model} (Fallback)",
                error_note="All configured Groq API keys exhausted or invalid.",
            )

        prompt = self._build_prompt(snapshot, risk_budget_usdt)
        last_error = None

        for key_idx, key_entry in enumerate(candidate_keys):
            key_alias = key_entry.alias
            next_key_entry = candidate_keys[key_idx + 1] if key_idx + 1 < len(candidate_keys) else None

            client = self._get_client_for_entry(key_entry)
            if not client:
                self.key_manager.mark_invalid(key_alias, "Failed to instantiate Groq SDK client")
                continue

            try:
                chat_completion = client.chat.completions.create(
                    model=active_model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an institutional cryptocurrency perpetual futures quantitative analyst. "
                                "You evaluate Smart Money Concepts (SMC), market structure, Level-2 order-book depth, "
                                "BTC market context, and risk/reward constraints. "
                                "You must evaluate BOTH the LONG and SHORT scenarios with equal rigor before recommending a dominant path. "
                                "You must respond STRICTLY with a valid JSON object matching the requested schema. "
                                "Include a comprehensive institutional analysis in Bahasa Indonesia under 'penjelasan_indonesia'."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                    max_tokens=950,
                )

                raw_text = chat_completion.choices[0].message.content
                parsed_json = json.loads(raw_text)

                self.key_manager.mark_success(key_alias)
                if key_alias != "PRIMARY":
                    logger.info(f"[GROQ_ANALYST] Request succeeded using {GroqKeyManager.friendly_name(key_alias).lower()}")

                return self._build_result_from_json(
                    analysis_id=analysis_id,
                    snapshot=snapshot,
                    data=parsed_json,
                    risk_budget_usdt=risk_budget_usdt,
                    model_id=active_model,
                    raw_response=raw_text,
                )

            except Exception as e:
                last_error = str(e)
                status_code = getattr(e, "status_code", None)
                response_obj = getattr(e, "response", None)
                headers = getattr(response_obj, "headers", None) if response_obj else None

                if status_code is None:
                    err_name = type(e).__name__
                    if "Timeout" in err_name or "Connection" in err_name:
                        status_code = 504
                    elif "429" in last_error or "rate_limit" in last_error.lower():
                        status_code = 429
                    elif "413" in last_error:
                        status_code = 413
                    elif "401" in last_error or "unauthorized" in last_error.lower():
                        status_code = 401
                    elif "400" in last_error:
                        status_code = 400

                err_type, retry_after = GroqKeyManager.classify_groq_error(
                    status_code=status_code or 500,
                    response_text=last_error,
                    headers=headers,
                )

                curr_friendly = GroqKeyManager.friendly_name(key_alias)
                next_friendly = GroqKeyManager.friendly_name(next_key_entry.alias) if next_key_entry else None

                if err_type == "QUOTA_EXHAUSTED":
                    self.key_manager.mark_quota_exhausted(key_alias, reason=last_error[:120])
                    if next_friendly:
                        logger.warning(f"[GROQ_ANALYST] {curr_friendly} failed: quota exhausted -> Switching to {next_friendly.lower()}")
                    else:
                        logger.warning(f"[GROQ_ANALYST] {curr_friendly} failed: quota exhausted and no further fallback keys configured.")

                elif err_type == "RATE_LIMITED":
                    self.key_manager.mark_rate_limited(key_alias, retry_after_sec=retry_after, reason=last_error[:120])
                    if next_friendly:
                        logger.info(f"[GROQ_ANALYST] {curr_friendly} failed: rate limited ({retry_after:.1f}s backoff) -> Switching to {next_friendly.lower()}")
                    else:
                        logger.warning(f"[GROQ_ANALYST] {curr_friendly} failed: rate limited ({retry_after:.1f}s backoff) and no fallback keys available.")

                elif err_type == "INVALID_KEY":
                    self.key_manager.mark_invalid(key_alias, reason="HTTP_401_UNAUTHORIZED")
                    if next_friendly:
                        logger.error(f"[GROQ_ANALYST] {curr_friendly} invalid (HTTP 401) -> Skipping to {next_friendly.lower()}")
                    else:
                        logger.error(f"[GROQ_ANALYST] {curr_friendly} invalid (HTTP 401) and no fallback keys configured.")

                elif err_type == "BAD_REQUEST":
                    logger.error(f"[GROQ_ANALYST] Request error HTTP {status_code}: {last_error[:120]}. Key rotation will not resolve schema/request errors.")
                    break

                else:  # PROVIDER_TEMPORARY / UNKNOWN
                    self.key_manager.mark_temporary_failure(key_alias, error_type=err_type)
                    if next_friendly:
                        logger.warning(f"[GROQ_ANALYST] {curr_friendly} temporary failure ({err_type}) -> Trying {next_friendly.lower()}")

        logger.warning(f"[GROQ_ANALYST] Groq API completion failed across all keys ({last_error}). Falling back to quantitative synthesis.")
        return self._generate_fallback_analysis(
            analysis_id=analysis_id,
            snapshot=snapshot,
            risk_budget_usdt=risk_budget_usdt,
            model_id=f"{active_model} (Fallback)",
            error_note=f"Groq API error: {last_error}",
        )

    def _build_prompt(self, s: QuantitativeMarketSnapshot, budget: float) -> str:
        return f"""
Analyze AsterDEX perpetual contract: {s.symbol} on {s.timeframe} timeframe.

### Quantitative Market Telemetry:
- Current Price: {s.current_price}
- 24H Turnover: ${s.volume_24h_usdt:,.0f} USDT
- Live Bid-Ask Spread: {s.spread_bps:.1f} bps
- Volatility (ATR 14): {s.atr_14:.4f} ({s.atr_pct:.2f}% of price)
- Nearest Confirmed Swing High (Resistance): {s.swing_high}
- Nearest Confirmed Swing Low (Support): {s.swing_low}
- Validated 1H Demand Zone: [{s.demand_zone_lower} – {s.demand_zone_upper}]
- Validated 1H Supply Zone: [{s.supply_zone_lower} – {s.supply_zone_upper}]
- Recent Liquidity Sweep & Reclaim: {"CONFIRMED" if s.recent_liquidity_sweep else "NONE DETECTED"}
- Order-Book Depth (Top 25 bps): Bids ${s.orderbook_bid_depth_25bps:,.0f} | Asks ${s.orderbook_ask_depth_25bps:,.0f} (Imbalance: {s.orderbook_imbalance_25bps:+.2f})
- BTC Benchmark Price: ${s.btc_price:,.1f} | BTC Reference Zone Status: {s.btc_zone_status}
- Relative Strength vs BTC 24H: {s.relative_strength_vs_btc_24h:+.2f}%
- Data Freshness: {s.data_age_sec:.1f} seconds (Stale: {s.is_stale})
- Advisory Risk Budget: ${budget:.2f} USDT

### Required JSON Schema:
Return a single JSON object with these EXACT keys:
{{
  "primary_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "recommendation": "LONG" | "SHORT" | "NEUTRAL_STAND_ASIDE",
  "confidence_score": 0-100,
  "uncertainty_score": 0-100,
  "executive_summary": "1-2 concise sentences summarizing the institutional setup in English.",
  "long_scenario": {{
    "confluence_score": 0-100,
    "probability_rating": "HIGH" | "MODERATE" | "LOW",
    "key_entry_zone": "string description of optimal entry range",
    "suggested_entry": float,
    "suggested_stop_loss": float,
    "take_profit_1": float,
    "take_profit_2": float,
    "key_catalysts": ["catalyst 1", "catalyst 2"],
    "risk_factors": ["risk 1", "risk 2"],
    "invalidation_level": float,
    "invalidation_condition": "string description"
  }},
  "short_scenario": {{
    "confluence_score": 0-100,
    "probability_rating": "HIGH" | "MODERATE" | "LOW",
    "key_entry_zone": "string description of optimal entry range",
    "suggested_entry": float,
    "suggested_stop_loss": float,
    "take_profit_1": float,
    "take_profit_2": float,
    "key_catalysts": ["catalyst 1", "catalyst 2"],
    "risk_factors": ["risk 1", "risk 2"],
    "invalidation_level": float,
    "invalidation_condition": "string description"
  }},
  "invalidation_conditions": ["condition 1", "condition 2"],
  "risk_factors": ["risk 1", "risk 2"],
  "penjelasan_indonesia": {{
    "ringkasan_struktur": "Analisis mendalam struktur pasar dalam Bahasa Indonesia.",
    "analisis_skenario_long": "Evaluasi lengkap peluang LONG dan level reaksinya.",
    "analisis_skenario_short": "Evaluasi lengkap peluang SHORT dan level tekanannya.",
    "rekomendasi_arah": "Penjelasan mengapa arah dominan dipilih atau mengapa harus netral.",
    "panduan_entry_dan_risiko": "Panduan taktis mengenai ladder entry, stop loss, dan alokasi margin.",
    "kondisi_pembatalan": "Kondisi teknikal yang membatalkan skenario ini.",
    "catatan_penting_kepatuhan": "Peringatan kepatuhan: Modul ini murni advisory dan tidak terhubung ke eksekusi otomatis."
  }}
}}
"""

    def _build_result_from_json(
        self,
        analysis_id: str,
        snapshot: QuantitativeMarketSnapshot,
        data: Dict[str, Any],
        risk_budget_usdt: float,
        model_id: str,
        raw_response: str,
    ) -> AIAnalystResult:
        dt_utc = datetime.now(timezone.utc).isoformat()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        # 1. Parse Scenarios
        long_data = data.get("long_scenario", {})
        l_entry = float(long_data.get("suggested_entry") or snapshot.demand_zone_upper or snapshot.current_price)
        l_sl = float(long_data.get("suggested_stop_loss") or (l_entry * 0.975))
        l_tp1 = float(long_data.get("take_profit_1") or (l_entry + abs(l_entry - l_sl) * 1.6))
        l_tp2 = float(long_data.get("take_profit_2") or (l_entry + abs(l_entry - l_sl) * 2.5))
        l_dist = abs(l_entry - l_sl) / l_entry * 100.0 if l_entry > 0 else 2.5
        l_gross_rr = (l_tp1 - l_entry) / (l_entry - l_sl) if (l_entry - l_sl) > 0 else 1.5
        l_net_rr = max(1.2, round(l_gross_rr * 0.95, 2))

        long_scenario = TradeScenario(
            direction="LONG",
            confluence_score=float(long_data.get("confluence_score") or 60.0),
            probability_rating=str(long_data.get("probability_rating") or "MODERATE"),
            key_entry_zone=str(long_data.get("key_entry_zone") or f"{snapshot.demand_zone_lower} - {snapshot.demand_zone_upper}"),
            suggested_entry=round(l_entry, 6),
            suggested_stop_loss=round(l_sl, 6),
            stop_distance_pct=round(l_dist, 2),
            take_profit_1=round(l_tp1, 6),
            take_profit_2=round(l_tp2, 6) if l_tp2 else None,
            gross_reward_to_risk=round(l_gross_rr, 2),
            net_reward_to_risk=l_net_rr,
            key_catalysts=long_data.get("key_catalysts", ["Demand zone support", "Positive BTC structure"]),
            risk_factors=long_data.get("risk_factors", ["Overhead supply resistance"]),
            invalidation_level=float(long_data.get("invalidation_level") or l_sl),
            invalidation_condition=str(long_data.get("invalidation_condition") or f"Decisive break below {l_sl}"),
        )

        short_data = data.get("short_scenario", {})
        s_entry = float(short_data.get("suggested_entry") or snapshot.supply_zone_lower or snapshot.current_price)
        s_sl = float(short_data.get("suggested_stop_loss") or (s_entry * 1.025))
        s_tp1 = float(short_data.get("take_profit_1") or (s_entry - abs(s_sl - s_entry) * 1.6))
        s_tp2 = float(short_data.get("take_profit_2") or (s_entry - abs(s_sl - s_entry) * 2.5))
        s_dist = abs(s_sl - s_entry) / s_entry * 100.0 if s_entry > 0 else 2.5
        s_gross_rr = (s_entry - s_tp1) / (s_sl - s_entry) if (s_sl - s_entry) > 0 else 1.5
        s_net_rr = max(1.2, round(s_gross_rr * 0.95, 2))

        short_scenario = TradeScenario(
            direction="SHORT",
            confluence_score=float(short_data.get("confluence_score") or 40.0),
            probability_rating=str(short_data.get("probability_rating") or "LOW"),
            key_entry_zone=str(short_data.get("key_entry_zone") or f"{snapshot.supply_zone_lower} - {snapshot.supply_zone_upper}"),
            suggested_entry=round(s_entry, 6),
            suggested_stop_loss=round(s_sl, 6),
            stop_distance_pct=round(s_dist, 2),
            take_profit_1=round(s_tp1, 6),
            take_profit_2=round(s_tp2, 6) if s_tp2 else None,
            gross_reward_to_risk=round(s_gross_rr, 2),
            net_reward_to_risk=s_net_rr,
            key_catalysts=short_data.get("key_catalysts", ["Supply zone rejection"]),
            risk_factors=short_data.get("risk_factors", ["Upward structural momentum"]),
            invalidation_level=float(short_data.get("invalidation_level") or s_sl),
            invalidation_condition=str(short_data.get("invalidation_condition") or f"Decisive break above {s_sl}"),
        )

        rec = str(data.get("recommendation", "LONG")).upper()
        if rec not in ("LONG", "SHORT", "NEUTRAL_STAND_ASIDE"):
            rec = "LONG" if long_scenario.confluence_score >= short_scenario.confluence_score else "SHORT"

        # 2. Build Plan for the Recommended Direction
        if rec == "LONG":
            recommended_plan = self.risk_calc.compute_plan(
                symbol=snapshot.symbol,
                direction="LONG",
                entry_price=long_scenario.suggested_entry,
                stop_loss_price=long_scenario.suggested_stop_loss,
                tp1_price=long_scenario.take_profit_1,
                tp2_price=long_scenario.take_profit_2,
                risk_budget_usdt=risk_budget_usdt,
            )
        elif rec == "SHORT":
            recommended_plan = self.risk_calc.compute_plan(
                symbol=snapshot.symbol,
                direction="SHORT",
                entry_price=short_scenario.suggested_entry,
                stop_loss_price=short_scenario.suggested_stop_loss,
                tp1_price=short_scenario.take_profit_1,
                tp2_price=short_scenario.take_profit_2,
                risk_budget_usdt=risk_budget_usdt,
            )
        else:
            recommended_plan = None

        # 3. Parse Indonesian narrative
        id_data = data.get("penjelasan_indonesia", {})
        indo_explanation = IndonesianExplanation(
            ringkasan_struktur=str(id_data.get("ringkasan_struktur") or f"Struktur {snapshot.symbol} pada timeframe {snapshot.timeframe} menunjukkan interaksi pada level kunci."),
            analisis_skenario_long=str(id_data.get("analisis_skenario_long") or "Skenario LONG berbasis area demand teknikal."),
            analisis_skenario_short=str(id_data.get("analisis_skenario_short") or "Skenario SHORT mengantisipasi reaksi penolakan dari area supply."),
            rekomendasi_arah=str(id_data.get("rekomendasi_arah") or f"Rekomendasi institusional adalah {rec} dengan pertimbangan rasio risiko/hasil."),
            panduan_entry_dan_risiko=str(id_data.get("panduan_entry_dan_risiko") or f"Alokasi risiko terencana ${risk_budget_usdt:.2f} USDT dengan proteksi stop loss."),
            kondisi_pembatalan=str(id_data.get("kondisi_pembatalan") or "Skenario batal jika level pembatalan struktural ditembus secara meyakinkan."),
            catatan_penting_kepatuhan=str(id_data.get("catatan_penting_kepatuhan") or "Peringatan: Analisis ini bersifat riset advisory murni. Tidak ada eksekusi order otomatis yang dikirim ke exchange."),
        )

        return AIAnalystResult(
            analysis_id=analysis_id,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            timestamp_ms=now_ms,
            datetime_utc=dt_utc,
            data_age_sec=snapshot.data_age_sec,
            is_stale=snapshot.is_stale,
            uncertainty_score=float(data.get("uncertainty_score") or 35.0),
            confidence_score=float(data.get("confidence_score") or 75.0),
            primary_bias=str(data.get("primary_bias") or "BULLISH").upper(),
            recommendation=rec,
            executive_summary=str(data.get("executive_summary") or f"Quantitative analysis for {snapshot.symbol} favors {rec}."),
            market_snapshot=snapshot,
            long_scenario=long_scenario,
            short_scenario=short_scenario,
            recommended_plan=recommended_plan,
            invalidation_conditions=data.get("invalidation_conditions", [f"Price break below {long_scenario.suggested_stop_loss}"]),
            risk_factors=data.get("risk_factors", ["Market volatility", "BTC impulse"]),
            penjelasan_indonesia=indo_explanation,
            model_id=model_id,
            raw_response=raw_response,
        )

    def _generate_fallback_analysis(
        self,
        analysis_id: str,
        snapshot: QuantitativeMarketSnapshot,
        risk_budget_usdt: float,
        model_id: str,
        error_note: str,
    ) -> AIAnalystResult:
        """Deterministic rule-based qualitative synthesis when Groq LLM is offline."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        dt_utc = datetime.now(timezone.utc).isoformat()

        # Decide dominant bias from demand proximity and relative strength
        dist_to_demand = (snapshot.current_price - snapshot.demand_zone_upper) / snapshot.current_price * 100.0 if snapshot.current_price > 0 else 5.0
        is_bullish = dist_to_demand <= 4.0 or snapshot.recent_liquidity_sweep or snapshot.relative_strength_vs_btc_24h > 0

        bias = "BULLISH" if is_bullish else "BEARISH"
        rec = "LONG" if is_bullish else "SHORT"

        # LONG Setup
        l_entry = snapshot.demand_zone_upper if snapshot.demand_zone_upper < snapshot.current_price else snapshot.current_price
        l_sl = round(snapshot.demand_zone_lower * 0.995, 6)
        if abs(l_entry - l_sl) / l_entry < 0.02:
            l_sl = round(l_entry * 0.975, 6)
        l_tp1 = round(l_entry + abs(l_entry - l_sl) * 1.65, 6)
        l_tp2 = round(l_entry + abs(l_entry - l_sl) * 2.50, 6)
        l_dist = round(abs(l_entry - l_sl) / l_entry * 100.0, 2)
        l_gross_rr = round((l_tp1 - l_entry) / abs(l_entry - l_sl), 2)
        l_net_rr = max(1.5, round(l_gross_rr * 0.95, 2))

        long_scenario = TradeScenario(
            direction="LONG",
            confluence_score=78.0 if is_bullish else 45.0,
            probability_rating="HIGH" if is_bullish else "MODERATE",
            key_entry_zone=f"{snapshot.demand_zone_lower:.4f} – {snapshot.demand_zone_upper:.4f}",
            suggested_entry=l_entry,
            suggested_stop_loss=l_sl,
            stop_distance_pct=l_dist,
            take_profit_1=l_tp1,
            take_profit_2=l_tp2,
            gross_reward_to_risk=l_gross_rr,
            net_reward_to_risk=l_net_rr,
            key_catalysts=[
                f"1H technical demand zone [{snapshot.demand_zone_lower:.4f} – {snapshot.demand_zone_upper:.4f}]",
                f"Relative strength vs BTC: {snapshot.relative_strength_vs_btc_24h:+.2f}%",
                "Order-book resting bid support near zone boundary",
            ],
            risk_factors=["Potential broader market BTC weakness", "Spread volatility on high-speed moves"],
            invalidation_level=l_sl,
            invalidation_condition=f"Penutupan candle 1H di bawah level {l_sl:.4f} membatalkan struktur demand.",
        )

        # SHORT Setup
        s_entry = snapshot.supply_zone_lower
        s_sl = round(snapshot.supply_zone_upper * 1.005, 6)
        if abs(s_sl - s_entry) / s_entry < 0.02:
            s_sl = round(s_entry * 1.025, 6)
        s_tp1 = round(s_entry - abs(s_sl - s_entry) * 1.65, 6)
        s_tp2 = round(s_entry - abs(s_sl - s_entry) * 2.50, 6)
        s_dist = round(abs(s_sl - s_entry) / s_entry * 100.0, 2)
        s_gross_rr = round((s_entry - s_tp1) / abs(s_sl - s_entry), 2)
        s_net_rr = max(1.5, round(s_gross_rr * 0.95, 2))

        short_scenario = TradeScenario(
            direction="SHORT",
            confluence_score=45.0 if is_bullish else 75.0,
            probability_rating="MODERATE" if not is_bullish else "LOW",
            key_entry_zone=f"{snapshot.supply_zone_lower:.4f} – {snapshot.supply_zone_upper:.4f}",
            suggested_entry=s_entry,
            suggested_stop_loss=s_sl,
            stop_distance_pct=s_dist,
            take_profit_1=s_tp1,
            take_profit_2=s_tp2,
            gross_reward_to_risk=s_gross_rr,
            net_reward_to_risk=s_net_rr,
            key_catalysts=[
                f"Overhead supply zone [{snapshot.supply_zone_lower:.4f} – {snapshot.supply_zone_upper:.4f}]",
                "Potential rejection at structural swing high",
            ],
            risk_factors=["Strong upward momentum could pierce through supply", "Trend continuation risk"],
            invalidation_level=s_sl,
            invalidation_condition=f"Penutupan candle 1H di atas level {s_sl:.4f} membatalkan skenario short.",
        )

        recommended_plan = self.risk_calc.compute_plan(
            symbol=snapshot.symbol,
            direction=rec,
            entry_price=long_scenario.suggested_entry if rec == "LONG" else short_scenario.suggested_entry,
            stop_loss_price=long_scenario.suggested_stop_loss if rec == "LONG" else short_scenario.suggested_stop_loss,
            tp1_price=long_scenario.take_profit_1 if rec == "LONG" else short_scenario.take_profit_1,
            tp2_price=long_scenario.take_profit_2 if rec == "LONG" else short_scenario.take_profit_2,
            risk_budget_usdt=risk_budget_usdt,
        )

        indo_explanation = IndonesianExplanation(
            ringkasan_struktur=(
                f"Pasar {snapshot.symbol} pada timeframe {snapshot.timeframe} saat ini berada di harga {snapshot.current_price:.4f}. "
                f"Struktur teknikal menunjukkan keberadaan zona demand di [{snapshot.demand_zone_lower:.4f} – {snapshot.demand_zone_upper:.4f}] "
                f"dengan volatilitas rata-rata (ATR14) sebesar {snapshot.atr_14:.4f} ({snapshot.atr_pct:.2f}%). "
                f"Konteks pasar Bitcoin berada pada status {snapshot.btc_zone_status} dengan harga ${snapshot.btc_price:,.1f}."
            ),
            analisis_skenario_long=(
                f"Peluang LONG didukung oleh zona demand teknikal yang tervalidasi. "
                f"Area entry ideal berada di kisaran {long_scenario.key_entry_zone} dengan target pertama (TP1) di {long_scenario.take_profit_1:.4f} "
                f"yang memberikan rasio Net R:R sebesar {long_scenario.net_reward_to_risk:.2f}R setelah memperhitungkan fee maker/taker dan slippage."
            ),
            analisis_skenario_short=(
                f"Peluang SHORT dapat dipertimbangkan jika terjadi penolakan kuat di area supply [{snapshot.supply_zone_lower:.4f} – {snapshot.supply_zone_upper:.4f}]. "
                f"Namun, skenario ini memiliki risiko tinggi jika tren naik dominan berlanjut."
            ),
            rekomendasi_arah=(
                f"Berdasarkan matriks kuantitatif dan struktur likuiditas, skenario {rec} diprioritaskan "
                f"karena memiliki konfluensi yang lebih unggul ({long_scenario.confluence_score if rec == 'LONG' else short_scenario.confluence_score:.0f}/100) "
                f"serta proteksi struktural yang jelas."
            ),
            panduan_entry_dan_risiko=(
                f"Gunakan rencana ladder entry: Limit 1 pada harga {recommended_plan.primary_entry:.4f} (60% alokasi) "
                f"dan Limit 2 pada {recommended_plan.entry_ladder[1].price:.4f} (40% alokasi). "
                f"Ukuran posisi dihitung dari anggaran risiko ${risk_budget_usdt:.2f} USDT dengan margin sekitar "
                f"${recommended_plan.required_margin_usdt:.2f} USDT pada leverage {recommended_plan.leverage:.0f}x."
            ),
            kondisi_pembatalan=(
                f"Tesis analisis ini BATAL sepenuhnya jika harga menembus level pembatalan "
                f"({recommended_plan.stop_loss:.4f}) dengan penutupan candle yang tegas."
            ),
            catatan_penting_kepatuhan=(
                f"Peringatan Kepatuhan: Analisis ini dihasilkan secara murni advisory ({error_note}). "
                "Sistem tidak akan mengeksekusi order apapun tanpa konfirmasi manual dari operator."
            ),
        )

        return AIAnalystResult(
            analysis_id=analysis_id,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            timestamp_ms=now_ms,
            datetime_utc=dt_utc,
            data_age_sec=snapshot.data_age_sec,
            is_stale=snapshot.is_stale,
            uncertainty_score=30.0,
            confidence_score=75.0,
            primary_bias=bias,
            recommendation=rec,
            executive_summary=f"{bias} structural bias for {snapshot.symbol} on {snapshot.timeframe} with {rec} setup opportunity.",
            market_snapshot=snapshot,
            long_scenario=long_scenario,
            short_scenario=short_scenario,
            recommended_plan=recommended_plan,
            invalidation_conditions=[long_scenario.invalidation_condition if rec == "LONG" else short_scenario.invalidation_condition],
            risk_factors=["Volatilitas pasar crypto", "Pergerakan impulsif BTC"],
            penjelasan_indonesia=indo_explanation,
            model_id=model_id,
            raw_response=None,
        )
