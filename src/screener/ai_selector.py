"""Gemini Argon AI Top 3 Futures Pair Selector for Screener.

Autonomous pair selection engine analyzing the complete multi-exchange perpetual
market universe to identify the Top 3 best candidates for deep inspection on /inspector.
Integrates with OpenRouter 'google/gemini-4-argon' using existing environment credentials,
with deterministic multi-factor quantitative confluence fallback.
Strictly READ-ONLY & Advisory.
"""

from datetime import datetime, timezone
from enum import Enum
import json
import logging
import math
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
import requests
from dotenv import load_dotenv

load_dotenv()

from src.screener.service import MultiExchangeMarketScreener, ScreenerFilterParams

logger = logging.getLogger(__name__)


class SelectionCategory(str, Enum):
    MOMENTUM_CONTINUATION = "MOMENTUM_CONTINUATION"
    LAGGARD_CATCH_UP = "LAGGARD_CATCH_UP"
    LIQUIDITY_SWEEP_RECLAIM = "LIQUIDITY_SWEEP_RECLAIM"
    BREAKOUT_WATCH = "BREAKOUT_WATCH"
    MEAN_REVERSION = "MEAN_REVERSION"
    CONTRARIAN_REVERSAL = "CONTRARIAN_REVERSAL"
    NO_CLEAR_SETUP = "NO_CLEAR_SETUP"


class SelectionBias(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class EntryReadiness(str, Enum):
    READY_FOR_INSPECTOR = "READY_FOR_INSPECTOR"
    WATCHLIST = "WATCHLIST"
    SKIP = "SKIP"


class PairCandidateOutput(BaseModel):
    """Schema for a single top-ranked futures candidate."""
    rank: int = Field(..., ge=1, le=3, description="Priority rank 1 to 3")
    symbol: str = Field(..., description="USDT perpetual contract symbol (e.g. SOLUSDT)")
    category: SelectionCategory
    bias: SelectionBias
    entry_readiness: EntryReadiness
    selection_score: float = Field(..., ge=0.0, le=100.0, description="Multi-factor score 0-100")
    price: Optional[float] = None
    price_change_24h_pct: Optional[float] = None
    turnover_24h_usdt: Optional[float] = None
    open_interest_usdt: Optional[float] = None
    funding_rate_8h: Optional[float] = None
    spread_bps: Optional[float] = None
    relative_strength_vs_btc_pct: Optional[float] = None
    venues_listed: List[str] = Field(default_factory=list)
    why_selected: str = Field(description="Comparative explanation against the whole market")
    what_to_verify_in_inspector: str = Field(description="Specific technical conditions to check on /inspector")
    invalidation_and_risk: str = Field(description="Conditions that invalidate the selection thesis")


class MarketRegimeSummary(BaseModel):
    """Summary of market regime anchors (BTC & ETH)."""
    btc_price: Optional[float] = None
    btc_change_24h_pct: Optional[float] = None
    eth_price: Optional[float] = None
    eth_change_24h_pct: Optional[float] = None
    regime_label: str = "Market Regime Active"
    altcoin_market_implication: str = ""


class AITop3SelectionResponse(BaseModel):
    """Top-level response payload for AI Top 3 Pair Selection."""
    status: str = "SUCCESS"
    model_used: str = "google/gemini-4-argon"
    provider: str = "OpenRouter (Gemini Argon)"
    analysis_timestamp_utc: str
    market_snapshot_id: str
    total_universe_screened: int
    venues_included: List[str] = Field(default_factory=lambda: ["asterdex", "binance", "bybit", "hyperliquid"])
    market_regime: MarketRegimeSummary
    candidates: List[PairCandidateOutput]
    disclaimer: str = (
        "Selection scores reflect multi-factor liquidity, momentum, and execution feasibility for research, "
        "NOT probability of profit or financial advice. Read-only candidate discovery."
    )


class GeminiArgonPairSelector:
    """Orchestrates market snapshot extraction, prompt generation, LLM querying, and fallback scoring."""

    DEFAULT_MODEL = "google/gemini-4-argon"
    MAX_TOKENS = 2500
    REQUEST_TIMEOUT_SECONDS = 35

    def __init__(self, screener: Optional[MultiExchangeMarketScreener] = None) -> None:
        self.screener = screener or MultiExchangeMarketScreener()
        self._cached_result: Optional[AITop3SelectionResponse] = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl_seconds: float = 60.0  # 1-minute TTL for cached results

    def _get_api_key(self) -> Optional[str]:
        return os.getenv("openrouter") or os.getenv("OPENROUTER_API_KEY")

    def get_latest_selection(self) -> Optional[Dict[str, Any]]:
        if self._cached_result is not None:
            return self._cached_result.model_dump()
        return None

    def select_top3(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Main entry point to perform Top 3 selection across the full screener universe."""
        now = time.time()
        if not force_refresh and self._cached_result is not None and (now - self._cache_timestamp < self._cache_ttl_seconds):
            return self._cached_result.model_dump()

        # 1. Fetch complete market snapshot from MultiExchangeMarketScreener
        filter_params = ScreenerFilterParams(
            min_volume_usdt=0.0,
            page_size=100,
            sort_by="volume_desc",
        )
        snapshot_result = self.screener.filter_and_paginate(params=filter_params, force_refresh=force_refresh)
        
        cached = getattr(self.screener, "_cached_assets", None)
        if isinstance(cached, dict):
            all_assets_cache = list(cached.values())
        elif isinstance(cached, list):
            all_assets_cache = cached
        else:
            all_assets_cache = []

        total_universe = len(all_assets_cache) if all_assets_cache else snapshot_result.get("total_universe_count", 0)
        snapshot_time = snapshot_result.get("last_fetch_utc") or datetime.now(timezone.utc).isoformat()
        snapshot_id = f"SNAP-{int(time.time())}-{total_universe}ASSETS"

        # 2. Extract Anchor Assets (BTC & ETH) for Market Regime
        btc_asset = None
        eth_asset = None
        for a in all_assets_cache:
            sym = a.normalized_symbol.upper()
            if sym in ("BTCUSDT", "BTC"):
                btc_asset = a
            elif sym in ("ETHUSDT", "ETH"):
                eth_asset = a
            if btc_asset and eth_asset:
                break

        btc_price = btc_asset.benchmark_price if btc_asset else None
        btc_chg = btc_asset.price_change_24h_pct if btc_asset else 0.0
        eth_price = eth_asset.benchmark_price if eth_asset else None
        eth_chg = eth_asset.price_change_24h_pct if eth_asset else 0.0

        regime_summary = self._build_regime_summary(btc_price, btc_chg, eth_price, eth_chg)

        # 3. Filter candidates for quantitative candidate pool
        # Select liquid altcoins (> $500k volume, valid price, exclude pure stablecoins & synthetics)
        candidates_pool = []
        for a in all_assets_cache:
            sym = a.normalized_symbol.upper()
            # Exclude BTC, ETH, USD synthetics, or stable pairs
            if sym in ("BTCUSDT", "BTC", "ETHUSDT", "ETH", "USDCUSDT", "FDUSDUSDT", "USDTUSDT"):
                continue
            if sym.endswith("USD1") or "USD1" in sym:
                continue  # Skip stock/commodity synthetics
            
            vol = a.total_volume_24h_usdt or 0.0
            if vol < 500000.0:  # Minimum $500K quote turnover
                continue
            
            spread = a.best_spread_bps
            if spread is not None and spread > 30.0:  # Exclude extreme illiquid spreads
                continue

            price = a.benchmark_price
            chg = a.price_change_24h_pct
            oi = a.total_open_interest_usdt
            funding = None
            if a.venues:
                ast_v = a.venues.get("asterdex")
                if ast_v:
                    funding = getattr(ast_v, "normalized_8h_funding_rate", None) if not isinstance(ast_v, dict) else ast_v.get("normalized_8h_funding_rate")
                if funding is None:
                    for v in a.venues.values():
                        val = getattr(v, "normalized_8h_funding_rate", None) if not isinstance(v, dict) else v.get("normalized_8h_funding_rate")
                        if val is not None:
                            funding = val
                            break

            rs_btc = (chg - (btc_chg or 0.0)) if chg is not None else None
            venues_present = list(a.venues.keys()) if a.venues else []

            candidates_pool.append({
                "symbol": sym,
                "price": price,
                "price_change_24h_pct": chg,
                "turnover_24h_usdt": vol,
                "open_interest_usdt": oi,
                "funding_rate_8h": funding,
                "spread_bps": spread,
                "relative_strength_vs_btc_pct": round(rs_btc, 2) if rs_btc is not None else None,
                "venues": venues_present,
            })

        # Sort candidate pool by 24h turnover
        candidates_pool.sort(key=lambda x: x["turnover_24h_usdt"], reverse=True)
        top_pool = candidates_pool[:25]  # Top 25 most liquid candidates

        # 4. Attempt Gemini Argon LLM Generation via OpenRouter
        response_obj: Optional[AITop3SelectionResponse] = None
        api_key = self._get_api_key()
        
        if api_key and top_pool:
            try:
                response_obj = self._call_gemini_argon(
                    api_key=api_key,
                    regime=regime_summary,
                    candidates_pool=top_pool,
                    total_screened=total_universe,
                    snapshot_id=snapshot_id,
                    snapshot_time=snapshot_time,
                )
            except Exception as e:
                logger.warning(f"Gemini Argon LLM call failed ({e}); falling back to deterministic confluence engine.")
                response_obj = None

        # 5. Deterministic Quantitative Confluence Fallback if LLM unavailable or invalid
        if response_obj is None:
            response_obj = self._generate_deterministic_top3(
                regime=regime_summary,
                candidates_pool=top_pool if top_pool else candidates_pool,
                total_screened=total_universe,
                snapshot_id=snapshot_id,
                snapshot_time=snapshot_time,
            )

        self._cached_result = response_obj
        self._cache_timestamp = time.time()
        return response_obj.model_dump()

    def _build_regime_summary(
        self,
        btc_price: Optional[float],
        btc_chg: Optional[float],
        eth_price: Optional[float],
        eth_chg: Optional[float],
    ) -> MarketRegimeSummary:
        btc_c = btc_chg or 0.0
        eth_c = eth_chg or 0.0
        
        if btc_c > 1.5 and eth_c > 1.5:
            label = "Bullish Expansion"
            implication = "Market is risk-on. Altcoin momentum continuation and breakout setups are favored. Maintain disciplined trailing stops."
        elif btc_c < -1.5 and eth_c < -1.5:
            label = "Bearish Trend Pressure"
            implication = "Market is risk-off. Short momentum or liquidity sweep-reclaim at deep discount levels are favored. Avoid chasing green wicks."
        elif eth_c > btc_c + 0.5:
            label = "Altcoin Leadership / Mild Bullish"
            implication = "ETH is outperforming BTC, demonstrating constructive altcoin risk appetite. Favor high-liquidity altcoins with healthy funding rates."
        else:
            label = "Rangebound Consolidation"
            implication = "Major assets are compressing within horizontal ranges. Mean-reversion, mid-range sweeps, and laggard catch-up setups are favored over breakout chasing."

        return MarketRegimeSummary(
            btc_price=btc_price,
            btc_change_24h_pct=btc_chg,
            eth_price=eth_price,
            eth_change_24h_pct=eth_chg,
            regime_label=label,
            altcoin_market_implication=implication,
        )

    def _call_gemini_argon(
        self,
        api_key: str,
        regime: MarketRegimeSummary,
        candidates_pool: List[Dict[str, Any]],
        total_screened: int,
        snapshot_id: str,
        snapshot_time: str,
    ) -> AITop3SelectionResponse:
        """Query Gemini Argon via OpenRouter with structured JSON schema prompt."""
        prompt_data = {
            "market_regime": {
                "btc_price": regime.btc_price,
                "btc_change_24h_pct": regime.btc_change_24h_pct,
                "eth_price": regime.eth_price,
                "eth_change_24h_pct": regime.eth_change_24h_pct,
                "regime_label": regime.regime_label,
            },
            "candidate_pool_sample": candidates_pool,
            "account_context": {
                "balance_usdt": 15.72,
                "target_margin_usdt": "1.00 - 1.50",
                "leverage": "Isolated 10x",
            },
            "selection_rubric": {
                "liquidity_weight": 0.25,
                "relative_strength_weight": 0.20,
                "momentum_quality_weight": 0.20,
                "oi_and_funding_weight": 0.15,
                "technical_potential_weight": 0.20,
            },
        }

        system_prompt = (
            "You are Gemini Argon, an elite Quantitative Crypto Futures Screener & Pair Selection Analyst for SignalGenerator.\n"
            "Your task is to analyze the complete market screener snapshot provided and select the TOP 3 best USDT-PERP futures pairs for deep inspection on /inspector.\n"
            "Rules:\n"
            "1. Output exactly 3 candidates ranked 1, 2, 3.\n"
            "2. Score each candidate from 0.0 to 100.0 based on the 5-factor rubric. A higher score does NOT mean guaranteed profit; it measures multi-factor alignment.\n"
            "3. Category MUST be one of: 'MOMENTUM_CONTINUATION', 'LAGGARD_CATCH_UP', 'LIQUIDITY_SWEEP_RECLAIM', 'BREAKOUT_WATCH', 'MEAN_REVERSION', 'CONTRARIAN_REVERSAL', 'NO_CLEAR_SETUP'.\n"
            "4. Bias MUST be: 'LONG', 'SHORT', or 'NEUTRAL'.\n"
            "5. Entry Readiness MUST be: 'READY_FOR_INSPECTOR', 'WATCHLIST', or 'SKIP'.\n"
            "6. Penalize overextended pairs (e.g. pumped +40% without pullback) and poor spread/liquidity.\n"
            "7. Explanations and notes must be in clear Bahasa Indonesia with professional quantitative terminology.\n"
            "8. Respond with PURE JSON matching the schema below. No markdown backticks, no preamble."
        )

        user_content = (
            f"Screen the market dataset and produce the Top 3 selections:\n{json.dumps(prompt_data, indent=2)}\n\n"
            "Required JSON Schema:\n"
            "{\n"
            '  "regime_altcoin_implication": "string in Bahasa Indonesia",\n'
            '  "candidates": [\n'
            '    {\n'
            '      "rank": 1,\n'
            '      "symbol": "SOLUSDT",\n'
            '      "category": "LIQUIDITY_SWEEP_RECLAIM",\n'
            '      "bias": "LONG",\n'
            '      "entry_readiness": "READY_FOR_INSPECTOR",\n'
            '      "selection_score": 92.0,\n'
            '      "price": 101.87,\n'
            '      "price_change_24h_pct": 1.54,\n'
            '      "turnover_24h_usdt": 34850000.0,\n'
            '      "open_interest_usdt": 155400000.0,\n'
            '      "funding_rate_8h": 0.000011,\n'
            '      "spread_bps": 0.98,\n'
            '      "relative_strength_vs_btc_pct": 0.83,\n'
            '      "venues_listed": ["asterdex", "binance", "bybit", "hyperliquid"],\n'
            '      "why_selected": "detailed explanation in Indonesian",\n'
            '      "what_to_verify_in_inspector": "detailed conditions in Indonesian",\n'
            '      "invalidation_and_risk": "detailed risk/invalidation in Indonesian"\n'
            '    }\n'
            '  ]\n'
            "}"
        )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://signalgenerator.local",
            "X-Title": "SignalGenerator Screener",
        }

        payload = {
            "model": self.DEFAULT_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": self.MAX_TOKENS,
            "temperature": 0.2,
        }

        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.REQUEST_TIMEOUT_SECONDS,
        )

        if resp.status_code != 200:
            raise RuntimeError(f"OpenRouter returned HTTP {resp.status_code}: {resp.text[:300]}")

        resp_data = resp.json()
        raw_content = resp_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not raw_content:
            raise ValueError("OpenRouter returned empty content")

        # Parse JSON from LLM output
        clean_json = raw_content.strip()
        if clean_json.startswith("```"):
            clean_json = re.sub(r"^```[a-zA-Z]*\n?", "", clean_json)
            clean_json = re.sub(r"\n?```$", "", clean_json).strip()

        parsed = json.loads(clean_json)
        candidates_raw = parsed.get("candidates", [])
        if not candidates_raw:
            raise ValueError("LLM returned 0 candidates")

        # Update regime implication if provided
        if parsed.get("regime_altcoin_implication"):
            regime.altcoin_market_implication = parsed["regime_altcoin_implication"]

        parsed_candidates: List[PairCandidateOutput] = []
        for i, c in enumerate(candidates_raw[:3]):
            rank = i + 1
            cat_str = str(c.get("category", "MOMENTUM_CONTINUATION")).upper()
            try:
                category = SelectionCategory(cat_str)
            except ValueError:
                category = SelectionCategory.MOMENTUM_CONTINUATION

            bias_str = str(c.get("bias", "LONG")).upper()
            try:
                bias = SelectionBias(bias_str)
            except ValueError:
                bias = SelectionBias.LONG

            readiness_str = str(c.get("entry_readiness", "READY_FOR_INSPECTOR")).upper()
            try:
                readiness = EntryReadiness(readiness_str)
            except ValueError:
                readiness = EntryReadiness.READY_FOR_INSPECTOR

            parsed_candidates.append(
                PairCandidateOutput(
                    rank=rank,
                    symbol=c.get("symbol", "").upper(),
                    category=category,
                    bias=bias,
                    entry_readiness=readiness,
                    selection_score=float(c.get("selection_score", 85.0)),
                    price=float(c["price"]) if c.get("price") is not None else None,
                    price_change_24h_pct=float(c["price_change_24h_pct"]) if c.get("price_change_24h_pct") is not None else None,
                    turnover_24h_usdt=float(c["turnover_24h_usdt"]) if c.get("turnover_24h_usdt") is not None else None,
                    open_interest_usdt=float(c["open_interest_usdt"]) if c.get("open_interest_usdt") is not None else None,
                    funding_rate_8h=float(c["funding_rate_8h"]) if c.get("funding_rate_8h") is not None else None,
                    spread_bps=float(c["spread_bps"]) if c.get("spread_bps") is not None else None,
                    relative_strength_vs_btc_pct=float(c["relative_strength_vs_btc_pct"]) if c.get("relative_strength_vs_btc_pct") is not None else None,
                    venues_listed=c.get("venues_listed", []),
                    why_selected=c.get("why_selected", "Selected based on superior liquidity and relative strength."),
                    what_to_verify_in_inspector=c.get("what_to_verify_in_inspector", "Verify 15m/1h FVG, EMA alignment, and order book depth."),
                    invalidation_and_risk=c.get("invalidation_and_risk", "Loss of local support pivot or sudden macro market reversal."),
                )
            )

        return AITop3SelectionResponse(
            status="SUCCESS",
            model_used=self.DEFAULT_MODEL,
            provider="OpenRouter (Gemini Argon)",
            analysis_timestamp_utc=snapshot_time,
            market_snapshot_id=snapshot_id,
            total_universe_screened=total_screened,
            market_regime=regime,
            candidates=parsed_candidates,
        )

    def _generate_deterministic_top3(
        self,
        regime: MarketRegimeSummary,
        candidates_pool: List[Dict[str, Any]],
        total_screened: int,
        snapshot_id: str,
        snapshot_time: str,
    ) -> AITop3SelectionResponse:
        """Deterministic multi-factor scoring fallback matching user's exact 5-factor rubric."""
        scored_candidates = []

        for c in candidates_pool:
            sym = c["symbol"]
            vol = c.get("turnover_24h_usdt") or 0.0
            chg = c.get("price_change_24h_pct") or 0.0
            spread = c.get("spread_bps") if c.get("spread_bps") is not None else 10.0
            oi = c.get("open_interest_usdt") or 0.0
            funding = c.get("funding_rate_8h") or 0.0
            rs_btc = c.get("relative_strength_vs_btc_pct") or 0.0

            # 1. Liquidity & Turnover (25 pts max)
            # Logarithmic scaling from $500K (12 pts) to $50M+ (25 pts)
            if vol >= 50000000:
                score_liq = 25.0
            elif vol >= 10000000:
                score_liq = 23.0
            elif vol >= 5000000:
                score_liq = 21.0
            elif vol >= 2000000:
                score_liq = 18.0
            elif vol >= 1000000:
                score_liq = 15.0
            else:
                score_liq = 12.0
            
            # Spread penalty on liquidity
            if spread < 1.0:
                score_liq = min(25.0, score_liq + 1.0)
            elif spread > 10.0:
                score_liq = max(5.0, score_liq - 4.0)

            # 2. Relative Strength vs BTC (20 pts max)
            # Positive RS favored (+0.5% to +6% is sweet spot)
            if 0.5 <= rs_btc <= 6.0:
                score_rs = 19.0
            elif rs_btc > 6.0 and rs_btc <= 15.0:
                score_rs = 17.0
            elif 0.0 <= rs_btc < 0.5:
                score_rs = 15.0
            elif -3.0 <= rs_btc < 0.0:
                score_rs = 12.0
            else:
                score_rs = 8.0  # Excessive lag or severe dump

            # 3. Momentum Quality & Room to Move (20 pts max)
            # Penalize extreme extensions (>20% pump = late entry risk)
            if 1.0 <= chg <= 8.0:
                score_mom = 18.5
            elif 8.0 < chg <= 18.0:
                score_mom = 15.0
            elif chg > 18.0:
                score_mom = 9.0  # Heavy penalty for chasing the top
            elif -4.0 <= chg < 1.0:
                score_mom = 16.0  # Solid consolidation / mid-range
            else:
                score_mom = 10.0  # Dump / knife catch

            # 4. Open Interest & Funding Rate Health (15 pts max)
            # Balanced funding (close to 0 or 0.0001) is healthy; extreme rates penalized
            abs_fund = abs(funding)
            if abs_fund <= 0.0001:
                score_oi_fund = 14.5
            elif abs_fund <= 0.0003:
                score_oi_fund = 12.0
            else:
                score_oi_fund = 7.0  # Crowded positioning penalty

            # 5. Technical Potential & Small Account Execution (20 pts max)
            # Altcoins with clean spreads (< 3 bps) and high venue presence
            score_tech = 17.0
            if len(c.get("venues", [])) >= 3:
                score_tech += 2.0
            if spread <= 2.0:
                score_tech += 1.0
            score_tech = min(20.0, score_tech)

            total_score = round(score_liq + score_rs + score_mom + score_oi_fund + score_tech, 1)

            # Assign category
            if rs_btc >= 1.5 and chg >= 2.0:
                category = SelectionCategory.MOMENTUM_CONTINUATION
            elif -2.0 <= rs_btc <= 1.0 and -1.0 <= chg <= 2.0:
                category = SelectionCategory.LAGGARD_CATCH_UP
            elif rs_btc > 0.0 and chg < 2.0:
                category = SelectionCategory.LIQUIDITY_SWEEP_RECLAIM
            elif chg > 5.0:
                category = SelectionCategory.BREAKOUT_WATCH
            else:
                category = SelectionCategory.MEAN_REVERSION

            scored_candidates.append({
                "candidate": c,
                "score": total_score,
                "category": category,
            })

        # Sort by total score descending
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)

        # Build top 3
        top_outputs: List[PairCandidateOutput] = []
        for idx, item in enumerate(scored_candidates[:3]):
            rank = idx + 1
            cand = item["candidate"]
            sym = cand["symbol"]
            score = item["score"]
            cat = item["category"]

            spread_val = cand.get("spread_bps")
            spread_str = f"{spread_val:.2f} bps" if spread_val is not None else "N/A"
            fund_val = cand.get("funding_rate_8h")
            fund_str = f"{fund_val:.6f}" if fund_val is not None else "N/A"
            rs_val = cand.get("relative_strength_vs_btc_pct")
            rs_str = f"{rs_val:+.2f}%" if rs_val is not None else "N/A"
            vol_val = cand.get("turnover_24h_usdt")
            vol_str = f"${vol_val/1e6:.1f}M" if vol_val is not None else "N/A"

            # Specialized rationale per rank
            if rank == 1:
                why = (
                    f"{sym} meraih skor tertinggi ({score}/100) berkat kombinasi likuiditas prima ({vol_str} "
                    f"turnover), spread sangat rapat ({spread_str}), dan relative strength positif ({rs_str} vs BTC). "
                    f"Struktur momentum sehat tanpa tanda overextension ekstrem."
                )
                verify = "Verifikasi level support 1H/15M, konfirmasi apakah retest EMA20 berhasil mempertahankan higher-low, dan periksa order book depth."
                inval = f"Thesis invalid jika harga breakdown menembus level swing support lokal atau jika BTC mengalami tekanan jual tajam."
            elif rank == 2:
                why = (
                    f"{sym} terpilih di peringkat kedua dengan likuiditas institusional yang solid, basis spread stabil ({spread_str}), "
                    f"serta funding rate normal ({fund_str}) yang menunjukkan positioning belum crowded."
                )
                verify = "Cek apakah terdapat FVG 15M yang belum tertutup, pantau divergensi RSI pada 1H, dan pastikan tidak ada resistance masif tepat di atas harga."
                inval = "Penembusan ke bawah garis konfluensi EMA50 1H atau lonjakan funding rate abnormal ke area ekstrem."
            else:
                why = (
                    f"{sym} menyajikan profil risk-to-reward simetris sebagai kandidat {cat.value}. Berada di area konsolidasi terukur "
                    f"dengan turnover {vol_str} dan spread efisien ({spread_str}), cocok untuk sizing akun micro tanpa late entry risk."
                )
                verify = "Pastikan volume akumulasi meningkat pada retest range ekuilibrium 15M dan konfirmasi spread tetap stabil saat volatilitas naik."
                inval = "Breakdown ke bawah range swing low 48 jam atau volume harian turun drastis di bawah ambang likuid."

            cand_chg = cand.get("price_change_24h_pct")
            cand_bias = SelectionBias.LONG if (cand_chg is not None and cand_chg >= 0) else SelectionBias.NEUTRAL

            top_outputs.append(
                PairCandidateOutput(
                    rank=rank,
                    symbol=sym,
                    category=cat,
                    bias=cand_bias,
                    entry_readiness=EntryReadiness.READY_FOR_INSPECTOR,
                    selection_score=score,
                    price=cand.get("price"),
                    price_change_24h_pct=cand.get("price_change_24h_pct"),
                    turnover_24h_usdt=cand.get("turnover_24h_usdt"),
                    open_interest_usdt=cand.get("open_interest_usdt"),
                    funding_rate_8h=cand.get("funding_rate_8h"),
                    spread_bps=cand.get("spread_bps"),
                    relative_strength_vs_btc_pct=cand.get("relative_strength_vs_btc_pct"),
                    venues_listed=cand.get("venues", []),
                    why_selected=why,
                    what_to_verify_in_inspector=verify,
                    invalidation_and_risk=inval,
                )
            )

        return AITop3SelectionResponse(
            status="SUCCESS",
            model_used="deterministic_confluence_engine",
            provider="Deterministic Confluence Engine (Rubric 5-Factor)",
            analysis_timestamp_utc=snapshot_time,
            market_snapshot_id=snapshot_id,
            total_universe_screened=total_screened,
            market_regime=regime,
            candidates=top_outputs,
        )
