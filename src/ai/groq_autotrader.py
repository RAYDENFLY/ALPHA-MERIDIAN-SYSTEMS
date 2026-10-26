"""Automated Groq AI (qwen/qwen3.8-27b) 10 -> 3 -> 1 Funnel Screener & Auto-Limit Order Pipeline.

A hybrid Python + 2-step Groq LLM architecture respecting Groq Free Tier TPM limits:
- Stage 1: Deterministic Python Pre-Screener (All Aster DEX Pairs -> Top 10 Candidates)
- Stage 2: Groq LLM Call #1 (Narrow Top 10 -> Top 3 Finalists)
- Stage 3: Groq LLM Call #2 (Deep Inspector Analysis: Top 3 -> 1 Best Setup)
- Stage 4: Deterministic Strategy Evaluation (EMA20_RECLAIM_5M_V1) & LIVE_MICRO_V1 Sizing
- Auto-Dispatch Limit Order (Supervised LIVE on Aster DEX Mainnet with Operator Confirmation Gate or Paper Mode)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
import requests
from dotenv import load_dotenv

from config.settings import LiveMicroV1Config, load_live_micro_v1_config
from src.core.types import Candle, Direction
from src.exchange.aster_adapter import AsterAdapter
from src.intelligence.coin_inspector import (
    CoinInspectorService,
    SymbolSpecs,
    get_dynamic_precision,
    round_price_dyn,
)
from src.journal.automated_journal import AutomatedTradeJournal
from src.journal.models import AutomatedTradeRecord
from src.risk.global_lock import GlobalPositionLock
from src.risk.live_micro_sizing import LiveMicroSizingEngine, LiveMicroSizingResult
from src.execution.live_session import ExecutionMode, LiveAutoSessionManager, LiveSessionState
from src.strategy.ema20_reclaim_5m import EMA20ReclaimSetup, EMA20ReclaimStrategy

if TYPE_CHECKING:
    from src.scheduler.live_micro_runner import LiveMicroCoordinator

load_dotenv()
logger = logging.getLogger(__name__)


class RiskPreset(str, Enum):
    CONSERVATIVE_035 = "CONSERVATIVE_035"
    GROWTH_080 = "GROWTH_080"
    LIVE_MICRO_020 = "LIVE_MICRO_020"


class ExecutionVenue(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"
    LIVE_MANUAL = "LIVE_MANUAL"
    LIVE_AUTO = "LIVE_AUTO"


class Top10CandidateItem(BaseModel):
    symbol: str
    mark_price: float
    change_24h_pct: float
    rel_strength_vs_btc: float
    turnover_24h_m: float
    spread_bps: float
    funding_rate_pct: float
    range_72h_pct: float
    trend_15m: str
    trend_1h: str
    trend_4h: str
    rsi_15m: float
    rsi_1h: float
    reason_selected: str


class Stage1Result(BaseModel):
    market_regime: Dict[str, Any]
    top_10: List[Top10CandidateItem]
    total_screened_pairs: int
    execution_time_ms: float


class Stage2FinalistItem(BaseModel):
    rank: int
    symbol: str
    category: str
    score: float
    thesis: str
    verification_questions: List[str] = Field(default_factory=list)


class Stage2Result(BaseModel):
    top_3: List[Stage2FinalistItem]
    eliminated_summary: Dict[str, str] = Field(default_factory=dict)
    model_used: str


class Stage3SetupOutput(BaseModel):
    decision: str = "EXECUTE"  # "EXECUTE", "WAIT", "REJECTED"
    symbol: str
    direction: str = "LONG"    # "LONG" or "SHORT"
    order_type: str = "LIMIT"
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    qty: float
    leverage: int = 10
    margin_mode: str = "ISOLATED"
    max_risk_usdt: float
    stop_distance_pct: float
    rr_ratio: float
    notional_usdt: float
    isolated_margin_usdt: float
    execution_venue: str = "PAPER"
    notes: str = ""
    reasoning: str = ""
    strategy_id: str = "EMA20_RECLAIM_5M_V1"
    strategy_eligible: bool = True
    strategy_rejection_reason: str = ""
    expected_net_rr: float = 0.0
    estimated_costs_usd: float = 0.0
    requires_confirmation: bool = False
    status: str = "PREVIEW"  # PREVIEW, READY, REJECTED, SUBMITTING, OPEN, PROTECTED, CLOSED, ERROR, KILL_SWITCH_HALTED


class GroqAutoTradePipelineResult(BaseModel):
    status: str  # PREVIEW, READY, REJECTED, SUBMITTING, OPEN, PROTECTED, CLOSED, ERROR, KILL_SWITCH_HALTED
    risk_preset: str
    execution_venue: str
    strategy_id: str = "EMA20_RECLAIM_5M_V1"
    stage1: Stage1Result
    stage2: Stage2Result
    stage3: Stage3SetupOutput
    execution_receipt: Optional[Dict[str, Any]] = None
    confirmation_payload: Optional[Dict[str, Any]] = None
    risk_gate_status: Dict[str, Any] = Field(default_factory=dict)
    timestamp_utc: str


class GroqAutoTrader:
    """Master Coordinator for 10 -> 3 -> 1 Groq AI Trade Selection & Supervised Execution."""

    DEFAULT_MODEL = "qwen/qwen3.8-27b"
    FALLBACK_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    GROQ_BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(
        self,
        adapter: Optional[AsterAdapter] = None,
        inspector_service: Optional[CoinInspectorService] = None,
        api_key: Optional[str] = None,
        model_id: Optional[str] = None,
        base_url: Optional[str] = None,
        coordinator: Optional[LiveMicroCoordinator] = None,
        automated_journal: Optional[AutomatedTradeJournal] = None,
        session_manager: Optional[Any] = None,
    ) -> None:
        self.adapter = adapter or AsterAdapter()
        self.inspector = inspector_service or CoinInspectorService(adapter=self.adapter)
        self.api_key = api_key or os.getenv("GROQ_API_KEY") or os.getenv("groq")
        self.model_id = model_id or os.getenv("GROQ_MODEL") or self.DEFAULT_MODEL
        self.base_url = base_url or os.getenv("GROQ_BASE_URL") or self.GROQ_BASE_URL
        if coordinator is not None:
            self.coordinator = coordinator
        else:
            from src.scheduler.live_micro_runner import LiveMicroCoordinator
            self.coordinator = LiveMicroCoordinator(exchange=self.adapter)
        self.automated_journal = automated_journal
        self.session_manager = session_manager or LiveAutoSessionManager(
            coordinator=self.coordinator,
            exchange_adapter=self.adapter,
            automated_journal=self.automated_journal,
        )
        self._telemetry_lock = threading.RLock()
        self._active_cycle_telemetry: Optional[Dict[str, Any]] = None
        self._last_cycle_telemetry: Optional[Dict[str, Any]] = None

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _cycle_start(self) -> Dict[str, Any]:
        cycle = {
            "cycle_id": f"cycle-{uuid.uuid4().hex}",
            "start_utc": self._utc_now(), "end_utc": None,
            "status": "RUNNING", "current_stage": "pipeline",
            "current_stage_started_utc": self._utc_now(),
            "stages": {}, "model_requests": [], "_started_mono": time.monotonic(),
        }
        with self._telemetry_lock:
            self._active_cycle_telemetry = cycle
        return cycle

    def _stage_start(self, name: str) -> float:
        cycle = self._active_cycle_telemetry
        started = time.monotonic()
        if cycle is not None:
            cycle["current_stage"] = name
            cycle["current_stage_started_utc"] = self._utc_now()
            cycle["stages"].setdefault(name, {})["start_utc"] = cycle["current_stage_started_utc"]
        return started

    def _stage_end(self, name: str, started: float) -> None:
        cycle = self._active_cycle_telemetry
        if cycle is not None:
            entry = cycle["stages"].setdefault(name, {})
            entry["end_utc"] = self._utc_now()
            entry["duration_ms"] = round((time.monotonic() - started) * 1000, 1)

    def _finish_cycle(self, cycle: Dict[str, Any], status: str) -> None:
        cycle["status"] = status
        cycle["end_utc"] = self._utc_now()
        cycle["duration_ms"] = round((time.monotonic() - cycle["_started_mono"]) * 1000, 1)
        cycle["stages"].setdefault("total_pipeline", {})["start_utc"] = cycle["start_utc"]
        cycle["stages"]["total_pipeline"].update(end_utc=cycle["end_utc"], duration_ms=cycle["duration_ms"])
        cycle.pop("_started_mono", None)
        with self._telemetry_lock:
            self._active_cycle_telemetry = None
            self._last_cycle_telemetry = cycle

    def get_cycle_telemetry(self) -> Dict[str, Any]:
        with self._telemetry_lock:
            cycle = self._active_cycle_telemetry or self._last_cycle_telemetry
            if not cycle:
                return {"last_cycle_status": "IDLE", "current_stage": "IDLE"}
            out = {k: v for k, v in cycle.items() if k != "_started_mono"}
            out["current_stage"] = cycle.get("current_stage", "IDLE") if self._active_cycle_telemetry else "IDLE"
            out["last_completed_cycle_id"] = self._last_cycle_telemetry.get("cycle_id") if self._last_cycle_telemetry else None
            out["last_completed_cycle_utc"] = self._last_cycle_telemetry.get("end_utc") if self._last_cycle_telemetry else None
            out["last_cycle_duration_ms"] = self._last_cycle_telemetry.get("duration_ms") if self._last_cycle_telemetry else None
            out["last_cycle_status"] = self._last_cycle_telemetry.get("status", "IDLE") if self._last_cycle_telemetry else ("RUNNING" if self._active_cycle_telemetry else "IDLE")
            return out

    def _call_groq_json(self, messages: List[Dict[str, str]], model_override: Optional[str] = None, max_tokens: int = 400) -> Dict[str, Any]:
        """Make an OpenAI-compatible JSON completion call to Groq with rate-limit backoff and model fallback."""
        key = self.api_key or os.getenv("GROQ_API_KEY") or os.getenv("groq")
        if not key:
            raise ValueError("GROQ_API_KEY is not configured in environment or .env.")

        models_to_try = [model_override or self.model_id] + self.FALLBACK_MODELS
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        last_error = None
        for model in models_to_try:
            for attempt in range(2):
                request_started = time.monotonic()
                try:
                    payload = {
                        "model": model,
                        "messages": messages,
                        "temperature": 0.1,
                        "response_format": {"type": "json_object"},
                        "max_tokens": max_tokens,
                    }
                    resp = requests.post(
                        f"{self.base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=25.0,
                    )
                    if resp.status_code == 200:
                        self._record_model_request(model, attempt + 1, resp.status_code, request_started, None, model != models_to_try[0])
                        data = resp.json()
                        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        clean_content = content.strip()
                        if clean_content.startswith("```"):
                            clean_content = re.sub(r"^```[a-zA-Z]*\n?", "", clean_content)
                            clean_content = re.sub(r"\n?```$", "", clean_content).strip()
                        return json.loads(clean_content)
                    elif resp.status_code == 429 and attempt == 0:
                        self._record_model_request(model, attempt + 1, resp.status_code, request_started, "HTTP_429_RATE_LIMIT", model != models_to_try[0])
                        wait_sec = 3.5
                        match = re.search(r"try again in ([\d\.]+)s", resp.text)
                        if match:
                            wait_sec = min(7.0, float(match.group(1)) + 0.5)
                        logger.info(f"Groq 429 rate limit on {model}. Sleeping {wait_sec:.1f}s before retry...")
                        time.sleep(wait_sec)
                        continue
                    else:
                        self._record_model_request(model, attempt + 1, resp.status_code, request_started, f"HTTP_{resp.status_code}", model != models_to_try[0])
                        last_error = f"Groq {model} HTTP {resp.status_code}: {resp.text[:250]}"
                        logger.warning(last_error)
                        break
                except Exception as e:
                    self._record_model_request(model, attempt + 1, None, request_started, type(e).__name__, model != models_to_try[0])
                    last_error = str(e)
                    logger.warning(f"Groq API call error with model {model} (attempt {attempt}): {e}")
                    break

        raise RuntimeError(f"All Groq API attempts failed. Last error: {last_error}")

    def _record_model_request(self, model: str, attempt: int, http_status: Optional[int], started: float, retry_reason: Optional[str], fallback: bool) -> None:
        cycle = self._active_cycle_telemetry
        if cycle is not None:
            cycle["model_requests"].append({"model": model, "attempt": attempt, "retry_reason": retry_reason,
                "fallback_used": fallback, "http_status": http_status,
                "latency_ms": round((time.monotonic() - started) * 1000, 1)})

    def run_stage1_prescreen(self) -> Stage1Result:
        """Stage 1: Deterministic Python Pre-Screener (All Aster DEX Pairs -> Top 10 Candidates)."""
        t0 = time.time()
        total_started = self._stage_start("stage1_total")
        started = self._stage_start("market_snapshot")
        snapshots = self.adapter.get_bulk_market_snapshot()
        self._stage_end("market_snapshot", started)
        started = self._stage_start("instrument_discovery")
        instruments = self.adapter.discover_instruments()
        self._stage_end("instrument_discovery", started)
        inst_map = {getattr(i, "symbol", i.get("symbol") if isinstance(i, dict) else ""): i for i in instruments}

        # 1. Macro Anchor Analysis (BTC & ETH)
        btc_snap = snapshots.get("BTCUSDT", {})
        eth_snap = snapshots.get("ETHUSDT", {})
        btc_chg = btc_snap.get("price_change_pct", 0.0) or 0.0
        eth_chg = eth_snap.get("price_change_pct", 0.0) or 0.0

        if btc_chg > 1.2 and eth_chg > 1.2:
            regime_label = "RISK_ON_BULLISH"
        elif btc_chg < -1.5 and eth_chg < -1.5:
            regime_label = "RISK_OFF_BEARISH"
        elif eth_chg > btc_chg + 0.5:
            regime_label = "ALTCOIN_LEADERSHIP_BULLISH"
        else:
            regime_label = "RANGEBOUND_CONSOLIDATION"

        market_regime = {
            "regime_label": regime_label,
            "btc_price": btc_snap.get("last_price"),
            "btc_change_24h_pct": btc_chg,
            "eth_price": eth_snap.get("last_price"),
            "eth_change_24h_pct": eth_chg,
        }

        # 2. Candidate Filtering Gates
        eligible_symbols = []
        for sym, snap in snapshots.items():
            sym_clean = sym.strip().upper()
            if not sym_clean.endswith("USDT"):
                continue
            if sym_clean in ("BTCUSDT", "USDCUSDT", "FDUSDUSDT", "USDTUSDT"):
                continue
            if "USD1" in sym_clean or sym_clean.startswith("XAU") or sym_clean.startswith("CL"):
                continue

            # Anti-Trap Gate #1: Chop blacklist
            if sym_clean in ("BNBUSDT",):
                continue

            # Anti-Trap Gate #2: Extreme pump/dump knife traps
            chg = snap.get("price_change_pct", 0.0) or 0.0
            if abs(chg) > 22.0:
                continue

            # Execution Quality Gate: Spread & Turnover
            spread = snap.get("spread_bps", 999.0) or 999.0
            vol = snap.get("volume_24h_usdt", 0.0) or 0.0
            if spread > 2.5 or vol < 500000.0:
                continue

            # Micro-Account Feasibility Gate: min_qty * mark_price <= $25.00
            inst = inst_map.get(sym_clean, {})
            min_qty = getattr(inst, "min_qty", 0.001) if hasattr(inst, "min_qty") else (inst.get("minQty", 0.001) if isinstance(inst, dict) else 0.001)
            price = snap.get("last_price") or snap.get("mid_price") or 0.0
            if price <= 0.0:
                continue
            min_cost = float(min_qty) * price
            if min_cost > 25.0:
                continue

            eligible_symbols.append((sym_clean, vol, chg, spread, price))

        # Sort by 24h turnover descending and select top 14 for deep indicator extraction
        eligible_symbols.sort(key=lambda x: x[1], reverse=True)
        top_slice = eligible_symbols[:14]

        def compute_coin_metrics(item):
            s, v, c, sp, p = item
            try:
                k1h = self.adapter.get_klines(s, timeframe="1h", limit=72)
                k15 = self.adapter.get_klines(s, timeframe="15m", limit=30)
                prem = self.adapter.get_premium_index(s) or {}
                funding_pct = float(prem.get("last_funding_rate", 0.0)) * 100

                closes_1h = [float(k.close if hasattr(k, "close") else k[4]) for k in k1h]
                highs_1h = [float(k.high if hasattr(k, "high") else k[2]) for k in k1h]
                lows_1h = [float(k.low if hasattr(k, "low") else k[3]) for k in k1h]
                closes_15 = [float(k.close if hasattr(k, "close") else k[4]) for k in k15]

                def calc_rsi(closes, period=14):
                    if len(closes) <= period:
                        return 50.0
                    gains, losses = [], []
                    for i in range(1, len(closes)):
                        diff = closes[i] - closes[i - 1]
                        if diff >= 0:
                            gains.append(diff)
                            losses.append(0.0)
                        else:
                            gains.append(0.0)
                            losses.append(abs(diff))
                    avg_gain = sum(gains[-period:]) / period
                    avg_loss = sum(losses[-period:]) / period
                    if avg_loss == 0:
                        return 100.0
                    rs = avg_gain / avg_loss
                    return round(100.0 - (100.0 / (1.0 + rs)), 1)

                rsi_15m = calc_rsi(closes_15)
                rsi_1h = calc_rsi(closes_1h)

                def calc_ema(closes, period):
                    if len(closes) < period:
                        return closes[-1] if closes else 0.0
                    k = 2.0 / (period + 1.0)
                    ema = closes[0]
                    for pr in closes[1:]:
                        ema = (pr * k) + (ema * (1.0 - k))
                    return ema

                ema20_15 = calc_ema(closes_15, 20)
                ema50_15 = calc_ema(closes_15, 50) if len(closes_15) >= 50 else ema20_15
                c15 = closes_15[-1] if closes_15 else p
                trend_15m = "BULLISH" if c15 >= ema20_15 >= ema50_15 else ("BEARISH" if c15 <= ema20_15 <= ema50_15 else "NEUTRAL")

                ema20_1h = calc_ema(closes_1h, 20)
                ema50_1h = calc_ema(closes_1h, 50)
                c1h = closes_1h[-1] if closes_1h else p
                trend_1h = "BULLISH" if c1h >= ema20_1h >= ema50_1h else ("BEARISH" if c1h <= ema20_1h <= ema50_1h else "NEUTRAL")

                closes_4h = closes_1h[::4] if len(closes_1h) >= 8 else closes_1h
                ema10_4h = calc_ema(closes_4h, 10)
                trend_4h = "BULLISH" if closes_4h[-1] >= ema10_4h else "BEARISH"

                h72 = max(highs_1h) if highs_1h else p
                l72 = min(lows_1h) if lows_1h else p
                range_72h_pct = round(((p - l72) / (h72 - l72 + 1e-9)) * 100, 1)

                rel_str = round(c - btc_chg, 2)
                reason = f"Turnover ${v/1e6:.1f}M, spread {sp:.2f}bps, 72h range {range_72h_pct}%"

                return Top10CandidateItem(
                    symbol=s,
                    mark_price=p,
                    change_24h_pct=round(c, 2),
                    rel_strength_vs_btc=rel_str,
                    turnover_24h_m=round(v / 1e6, 2),
                    spread_bps=round(sp, 2),
                    funding_rate_pct=round(funding_pct, 4),
                    range_72h_pct=range_72h_pct,
                    trend_15m=trend_15m,
                    trend_1h=trend_1h,
                    trend_4h=trend_4h,
                    rsi_15m=rsi_15m,
                    rsi_1h=rsi_1h,
                    reason_selected=reason,
                )
            except Exception as e:
                logger.warning(f"Error computing metrics for {s}: {e}")
                return None

        started = self._stage_start("metric_extraction")
        with ThreadPoolExecutor(max_workers=8) as executor:
            metric_results = list(executor.map(compute_coin_metrics, top_slice))
        self._stage_end("metric_extraction", started)

        valid_top10 = [m for m in metric_results if m is not None][:10]
        exec_time = round((time.time() - t0) * 1000, 1)

        result = Stage1Result(
            market_regime=market_regime,
            top_10=valid_top10,
            total_screened_pairs=len(snapshots),
            execution_time_ms=exec_time,
        )
        self._stage_end("stage1_total", total_started)
        return result

    def run_stage2_rank_top3(self, stage1: Stage1Result, model_override: Optional[str] = None) -> Stage2Result:
        """Stage 2: Groq LLM Call #1 (Narrow Top 10 -> Top 3 Finalists)."""
        prompt_payload = {
            "market_compass": stage1.market_regime,
            "top_10_candidates": [c.model_dump() for c in stage1.top_10],
            "rubric": {
                "rule_1_eliminate_late_entry": "Reject coins at > 78% of 72H range for LONGs unless clear premium sweep short setup. Reject < 20% range for SHORTs.",
                "rule_2_three_setup_categories": [
                    "LIQUIDITY_SWEEP_RECLAIM: Near 15m/1h swing low with oversold RSI and supportive funding",
                    "MOMENTUM_CONTINUATION: Strong RS vs BTC, healthy bull trend, pulling back to EMA20/50 support",
                    "LAGGARD_CATCH_UP or SMC_DISCOUNT_REVERSAL: Inside 20%-55% range, low funding, coiled for expansion",
                ],
            },
        }

        system_prompt = (
            "You are Groq Qwen Quantitative Screener. Narrow the 10 candidates to the exact TOP 3 best setups.\n"
            "Enforce:\n"
            "1. Rank 1, 2, and 3 from different complimentary categories.\n"
            "2. Reject coins at > 78% of 72H dealing range for LONG (avoid late entry FOMO).\n"
            "3. Output strictly valid JSON matching this schema:\n"
            "{\n"
            '  "top_3": [\n'
            '    {\n'
            '      "rank": 1,\n'
            '      "symbol": "SOLUSDT",\n'
            '      "category": "LIQUIDITY_SWEEP_RECLAIM",\n'
            '      "score": 92.5,\n'
            '      "thesis": "Concise thesis in English or Indonesian",\n'
            '      "verification_questions": ["Verify 15m FVG fill", "Check L2 bid wall depth"]\n'
            '    }\n'
            '  ],\n'
            '  "eliminated_summary": {\n'
            '    "SYMBOL": "Reason for elimination"\n'
            '  }\n'
            "}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Analyze candidates and output Top 3 in pure JSON:\n{json.dumps(prompt_payload, indent=2)}"},
        ]

        active_model = model_override or self.model_id
        stage2_started = self._stage_start("stage2_groq_request")
        try:
            raw_json = self._call_groq_json(messages, model_override=active_model, max_tokens=350)
            self._stage_end("stage2_groq_request", stage2_started)
            top_3_raw = raw_json.get("top_3", [])
            eliminated = raw_json.get("eliminated_summary", {})

            finalists = []
            for i, item in enumerate(top_3_raw[:3]):
                finalists.append(
                    Stage2FinalistItem(
                        rank=i + 1,
                        symbol=item.get("symbol", "").upper(),
                        category=item.get("category", "MOMENTUM_CONTINUATION"),
                        score=float(item.get("score", 85.0)),
                        thesis=item.get("thesis", "Solid technical structure and liquidity."),
                        verification_questions=item.get("verification_questions", []),
                    )
                )

            if len(finalists) == 3:
                return Stage2Result(top_3=finalists, eliminated_summary=eliminated, model_used=active_model)
        except Exception as e:
            self._stage_end("stage2_groq_request", stage2_started)
            logger.warning(f"Groq Stage 2 call failed ({e}); invoking deterministic fallback.")

        # Deterministic Algorithmic Fallback for Stage 2
        sorted_candidates = sorted(stage1.top_10, key=lambda c: (c.range_72h_pct <= 75.0, c.turnover_24h_m), reverse=True)
        fallback_finalists = []
        categories = ["LIQUIDITY_SWEEP_RECLAIM", "MOMENTUM_CONTINUATION", "LAGGARD_CATCH_UP"]
        for idx, cand in enumerate(sorted_candidates[:3]):
            fallback_finalists.append(
                Stage2FinalistItem(
                    rank=idx + 1,
                    symbol=cand.symbol,
                    category=categories[idx % len(categories)],
                    score=round(90.0 - (idx * 4.0), 1),
                    thesis=f"{cand.symbol} qualifies with {cand.turnover_24h_m}M turnover and balanced 72h range ({cand.range_72h_pct}%).",
                    verification_questions=["Verify 15m EMA20 support", "Check FVG confluence on 1H"],
                )
            )

        return Stage2Result(top_3=fallback_finalists, eliminated_summary={}, model_used="deterministic_fallback")

    def evaluate_candidate_with_strategy(
        self,
        symbol: str,
        coordinator: LiveMicroCoordinator,
        direction: str = "LONG",
        strategy_id: str = "EMA20_RECLAIM_5M_V1",
    ) -> Tuple[Optional[EMA20ReclaimSetup], str]:
        """Evaluate candidate with deterministic strategy rules (EMA20_RECLAIM_5M_V1).
        
        Strict Invariants:
        - Strategy must be EMA20_RECLAIM_5M_V1. SWEEP_1M_V1 is strictly rejected.
        - Candle series must have at least 25 5m bars.
        - Candles must not be stale (>15m).
        """
        if strategy_id != "EMA20_RECLAIM_5M_V1":
            return None, f"UNAUTHORIZED_STRATEGY: Strategy {strategy_id} is prohibited from live execution."

        # Fetch 5m candles
        candles_5m = self.adapter.get_klines(symbol, timeframe="5m", limit=35)
        if not candles_5m or len(candles_5m) < 25:
            return None, "INSUFFICIENT_DATA: Need at least 25 5m candles"

        # Staleness check
        last_candle = candles_5m[-1]
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        close_ts = getattr(last_candle, "close_time_ms", 0)
        if close_ts > 0 and (now_ms - close_ts > 900000):  # 15 min
            return None, f"STALE_MARKET_DATA: Last 5m candle is older than 15 minutes ({now_ms - close_ts}ms)"

        specs = self.inspector.get_symbol_specs(symbol)
        step_size = specs.step_size or 0.001
        min_notional = specs.min_notional or 5.0
        tick_size = specs.tick_size or 0.01

        setup, reason = coordinator.ema_strategy.evaluate(
            symbol=symbol,
            candles_5m=candles_5m,
            tick_size=tick_size,
            step_size=step_size,
            min_notional=min_notional,
            account_equity=coordinator.current_equity,
        )
        return setup, reason

    def run_stage3_finalist_and_order(
        self,
        stage1: Stage1Result,
        stage2: Stage2Result,
        risk_preset: RiskPreset = RiskPreset.GROWTH_080,
        execution_venue: Any = ExecutionVenue.PAPER,
        model_override: Optional[str] = None,
        auto_fire_limit_order: bool = False,
        operator_confirmed: bool = False,
        strategy_id: str = "EMA20_RECLAIM_5M_V1",
        journal: Optional[Any] = None,
        runners: Optional[Dict[str, Any]] = None,
        live_adapter: Optional[Any] = None,
        coordinator: Optional[LiveMicroCoordinator] = None,
        automated_journal: Optional[AutomatedTradeJournal] = None,
        session_manager: Optional[Any] = None,
        selected_setup: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Stage3SetupOutput, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Stage 3 & 4: Deep Candidate Analysis + EMA20_RECLAIM_5M_V1 Strategy Validation + Supervised Execution."""
        coord = coordinator or self.coordinator
        auto_journal = automated_journal or self.automated_journal
        sess_mgr = session_manager or self.session_manager
        live_adapter = live_adapter or (getattr(coord, "exchange", None) if coord else None) or self.adapter

        # Strict Security Invariant: Strategy authorization check
        if strategy_id == "SWEEP_1M_V1":
            raise PermissionError("CRITICAL SECURITY INVARIANT: SWEEP_1M_V1 is SHADOW-only and strictly prohibited from live execution.")
        if strategy_id != "EMA20_RECLAIM_5M_V1":
            raise ValueError(f"Strategy '{strategy_id}' is not authorized for live execution.")

        venue_str = execution_venue.value if hasattr(execution_venue, "value") else str(execution_venue).strip().upper()
        if venue_str in ("LIVE", "LIVE_MANUAL"):
            mode = ExecutionMode.LIVE_MANUAL
        elif venue_str == "LIVE_AUTO":
            mode = ExecutionMode.LIVE_AUTO
        else:
            mode = ExecutionMode.PAPER

        is_live = mode in (ExecutionMode.LIVE_MANUAL, ExecutionMode.LIVE_AUTO)
        target_risk_usdt = 0.20 if is_live else (0.35 if risk_preset == RiskPreset.CONSERVATIVE_035 else 0.80)

        # 1. Gather Deep Inspector Snapshots for Top 3
        deep_snapshots = []
        for finalist in stage2.top_3:
            sym = finalist.symbol
            try:
                insp = self.inspector.inspect_coin(sym, account_balance_usdt=coord.current_equity)
                deep_snapshots.append({
                    "symbol": sym,
                    "rank": finalist.rank,
                    "category": finalist.category,
                    "meta": insp.get("meta", {}),
                    "smc_structure": insp.get("smc_structure", {}),
                    "order_book": insp.get("order_book", {}),
                    "recent_candles_15m": insp.get("candles", {}).get("recent_15m", [])[-6:],
                    "recent_candles_1h": insp.get("candles", {}).get("recent_1h", [])[-4:],
                    "micro_sizing": insp.get("bot_signals_and_micro_sizing", {}).get("micro_sizing", {}),
                })
            except Exception as e:
                logger.warning(f"Error gathering deep inspector data for {sym}: {e}")

        # 2. Advisory LLM Setup Formulation
        system_prompt = (
            "You are Groq Qwen Master SMC Futures Strategist for a micro-account ($0.20 fixed dollar risk, 10x ISOLATED).\n"
            "Analyze the 3 deep snapshots and formulate the single BEST Limit Order candidate setup.\n"
            "Rules:\n"
            "1. NEVER Chase Market Tops: entry_price MUST be a LIMIT order placed at structural pullback/reclaim:\n"
            "   - For LONG: Place entry_price at nearest confluence of 15m/5m EMA20 support or unmitigated bull FVG (0.15% to 0.70% below mark).\n"
            "   - For SHORT: Only allow if regime is BEARISH; entry at bear FVG.\n"
            "2. Stop Distance & R:R Protection:\n"
            "   - stop_distance_pct strictly between 0.40% and 0.80% beyond swing low.\n"
            "   - take_profit_price at 1.80R to 2.25R with 100% full exit.\n"
            "3. Output strictly valid JSON schema:\n"
            "{\n"
            '  "decision": "EXECUTE",\n'
            '  "symbol": "SOLUSDT",\n'
            '  "direction": "LONG",\n'
            '  "order_type": "LIMIT",\n'
            '  "entry_price": 101.40,\n'
            '  "stop_loss_price": 100.80,\n'
            '  "take_profit_price": 102.75,\n'
            '  "notes": "SOL 5m EMA20 reclaim with structural support",\n'
            '  "reasoning": "Strong bid wall confluence and unmitigated FVG"\n'
            "}\n"
            'If all 3 coins have terrible R:R or are mid-air, return {"decision": "WAIT", "reasoning": "No clean pullback"}.'
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Market Regime: {json.dumps(stage1.market_regime)}\n\nDeep Candidate Snapshots:\n{json.dumps(deep_snapshots, indent=2)}\n\nTarget Risk: ${target_risk_usdt:.2f} USDT on 10x ISOLATED. Formulate candidate in pure JSON:",
            },
        ]

        active_model = model_override or self.model_id
        raw_llm_setup = {}
        try:
            raw_llm_setup = self._call_groq_json(messages, model_override=active_model, max_tokens=350)
        except Exception as e:
            logger.warning(f"Groq Stage 3 call failed ({e}); formulating algorithmic fallback setup.")

        if not raw_llm_setup or raw_llm_setup.get("decision") != "EXECUTE" or not raw_llm_setup.get("symbol"):
            best_snap = deep_snapshots[0] if deep_snapshots else {}
            win_sym = best_snap.get("symbol", stage2.top_3[0].symbol if stage2.top_3 else "SOLUSDT")
            mark = best_snap.get("meta", {}).get("mark_price", 100.0)
            entry_p = mark * 0.9965
            sl_p = entry_p * (1.0 - 0.0055)  # 0.55% stop within [0.40%, 0.80%] bounds
            tp_p = entry_p + (entry_p - sl_p) * 2.25
            raw_llm_setup = {
                "decision": "EXECUTE",
                "symbol": win_sym,
                "direction": "LONG",
                "order_type": "LIMIT",
                "entry_price": entry_p,
                "stop_loss_price": sl_p,
                "take_profit_price": tp_p,
                "notes": f"{win_sym} 5m pullback entry at structural support",
                "reasoning": "Algorithmic structural confluence at 5m EMA support.",
            }

        # Override with operator selected setup if supplied
        if selected_setup and isinstance(selected_setup, dict):
            raw_llm_setup.update(selected_setup)

        # 3. Deterministic Strategy Evaluation (EMA20_RECLAIM_5M_V1) & Sizing
        win_symbol = raw_llm_setup["symbol"].strip().upper()
        specs = self.inspector.get_symbol_specs(win_symbol)

        # Evaluate candidate against EMA20_RECLAIM_5M_V1
        strategy_setup, strat_reason = self.evaluate_candidate_with_strategy(
            symbol=win_symbol,
            coordinator=coord,
            direction=raw_llm_setup.get("direction", "LONG"),
            strategy_id="EMA20_RECLAIM_5M_V1",
        )

        # If winner didn't trigger, evaluate remaining finalists in Top 3 to find if another coin triggers
        if not strategy_setup:
            for alt_cand in stage2.top_3:
                alt_sym = alt_cand.symbol.strip().upper()
                if alt_sym == win_symbol:
                    continue
                alt_setup, alt_reason = self.evaluate_candidate_with_strategy(
                    symbol=alt_sym,
                    coordinator=coord,
                    direction="LONG",
                    strategy_id="EMA20_RECLAIM_5M_V1",
                )
                if alt_setup:
                    win_symbol = alt_sym
                    specs = self.inspector.get_symbol_specs(win_symbol)
                    strategy_setup = alt_setup
                    strat_reason = "SETUP_VALID"
                    raw_llm_setup["symbol"] = win_symbol
                    raw_llm_setup["entry_price"] = alt_setup.entry_price
                    raw_llm_setup["stop_loss_price"] = alt_setup.stop_loss
                    raw_llm_setup["take_profit_price"] = alt_setup.take_profit
                    raw_llm_setup["notes"] = f"{win_symbol} promoted from Top 3 finalists: verified 5m EMA20 Reclaim"
                    break

        # Sizing and guardrail validation
        if strategy_setup and strategy_setup.sizing and strategy_setup.sizing.is_feasible:
            # Deterministic quantitative sizing from strategy
            sizing = strategy_setup.sizing
            total_costs = (
                sizing.expected_entry_fee_usd
                + sizing.expected_exit_sl_fee_usd
                + sizing.expected_slippage_usd
                + sizing.expected_funding_usd
            )
            setup_output = Stage3SetupOutput(
                decision="EXECUTE",
                symbol=win_symbol,
                direction=strategy_setup.direction.value if hasattr(strategy_setup.direction, "value") else str(strategy_setup.direction),
                order_type="LIMIT",
                entry_price=strategy_setup.entry_price,
                stop_loss_price=strategy_setup.stop_loss,
                take_profit_price=strategy_setup.take_profit,
                qty=sizing.quantity,
                leverage=10,
                margin_mode="ISOLATED",
                max_risk_usdt=round(sizing.expected_conservative_loss_usd, 4),
                stop_distance_pct=round(strategy_setup.stop_distance_pct, 3),
                rr_ratio=round(sizing.gross_rr, 2),
                notional_usdt=round(sizing.notional_usd, 2),
                isolated_margin_usdt=round(sizing.isolated_margin_usd, 2),
                execution_venue=execution_venue.value,
                notes=raw_llm_setup.get("notes", f"{win_symbol} 5m EMA20 Reclaim"),
                reasoning=raw_llm_setup.get("reasoning", "Verified 5m EMA20 Reclaim with mathematical risk gate clearance"),
                strategy_id="EMA20_RECLAIM_5M_V1",
                strategy_eligible=True,
                strategy_rejection_reason="",
                expected_net_rr=round(sizing.expected_net_rr, 2),
                estimated_costs_usd=round(total_costs, 4),
                requires_confirmation=(execution_venue == ExecutionVenue.LIVE and auto_fire_limit_order and not operator_confirmed),
                status="PREVIEW" if not auto_fire_limit_order else ("READY" if (execution_venue == ExecutionVenue.LIVE and not operator_confirmed) else "SUBMITTING"),
            )
        else:
            # Fallback mathematical guardrail sizing
            setup_output = self.apply_mathematical_guardrails(
                raw_setup=raw_llm_setup,
                specs=specs,
                risk_preset=risk_preset,
                execution_venue=execution_venue,
                target_risk_cap=0.20 if execution_venue == ExecutionVenue.LIVE else target_risk_usdt,
            )
            setup_output.strategy_id = "EMA20_RECLAIM_5M_V1"
            setup_output.strategy_eligible = False
            setup_output.strategy_rejection_reason = strat_reason or "Candidate does not currently exhibit 5m EMA20 reclaim trigger"
            if execution_venue == ExecutionVenue.LIVE:
                setup_output.status = "REJECTED"
                setup_output.decision = "WAIT"

        # 4. Supervised Order Dispatch & Operator Confirmation Gate
        execution_receipt = None
        confirmation_payload = None

        if auto_fire_limit_order:
            if not is_live:
                # PAPER Execution
                order_payload = {
                    "symbol": setup_output.symbol,
                    "direction": setup_output.direction,
                    "order_type": "LIMIT",
                    "entry_price": setup_output.entry_price,
                    "stop_loss_price": setup_output.stop_loss_price,
                    "take_profit_price": setup_output.take_profit_price,
                    "qty": setup_output.qty,
                    "leverage": 10,
                    "margin_mode": "ISOLATED",
                    "max_risk_usdt": setup_output.max_risk_usdt,
                    "execution_venue": "PAPER",
                    "notes": setup_output.notes,
                }
                try:
                    execution_receipt = self.inspector.execute_ai_trade_setup(
                        payload=order_payload,
                        journal=journal,
                        live_trading_locked=False,
                        runners=runners,
                        live_adapter=live_adapter,
                    )
                    setup_output.status = "OPEN"
                except Exception as e:
                    logger.error(f"Error auto-dispatching Paper Order: {e}")
                    execution_receipt = {
                        "status": "ERROR",
                        "error": str(e),
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    setup_output.status = "ERROR"

            else:
                # LIVE MAINNET Execution (LIVE_MANUAL or LIVE_AUTO)
                # Gate 1: Check Strategy Eligibility
                if not setup_output.strategy_eligible:
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"Strategy Gate Failed: {setup_output.strategy_rejection_reason}",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                # Gate 2: Session Authorization or Manual Operator Confirmation
                if mode == ExecutionMode.LIVE_AUTO:
                    # LIVE_AUTO requires server-side session authorization
                    if not sess_mgr.is_authorized_and_active():
                        setup_output.status = "REJECTED"
                        execution_receipt = {
                            "status": "REJECTED",
                            "reason": f"LIVE_AUTO execution blocked: session is not active ({sess_mgr.state.value}). Start authorized session first.",
                            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        }
                        return setup_output, execution_receipt, None
                    # Authorized LIVE_AUTO automatically proceeds to execution!
                else:
                    # LIVE_MANUAL requires explicit per-order operator confirmation
                    if not operator_confirmed:
                        setup_output.status = "READY"
                        setup_output.requires_confirmation = True
                        confirmation_payload = {
                            "symbol": setup_output.symbol,
                            "direction": setup_output.direction,
                            "order_type": "LIMIT",
                            "entry_price": setup_output.entry_price,
                            "stop_loss_price": setup_output.stop_loss_price,
                            "take_profit_price": setup_output.take_profit_price,
                            "qty": setup_output.qty,
                            "notional_usdt": setup_output.notional_usdt,
                            "isolated_margin_usdt": setup_output.isolated_margin_usdt,
                            "max_risk_usdt": setup_output.max_risk_usdt,
                            "expected_net_rr": setup_output.expected_net_rr,
                            "estimated_costs_usd": setup_output.estimated_costs_usd,
                            "stop_distance_pct": setup_output.stop_distance_pct,
                            "strategy_id": "EMA20_RECLAIM_5M_V1",
                            "execution_mode": mode.value,
                            "warning": "LIVE MAINNET EXECUTION: Real USDT funds ($0.20 risk cap, 10x isolated margin) will be committed on Aster DEX.",
                        }
                        return setup_output, None, confirmation_payload

                # Operator Confirmed -> Proceed with Server-Side Invariant & Risk Limit Checks
                # Check 1: Coordinator Risk Limits (Daily entries, Daily loss, Peak drawdown kill switch)
                risk_ok, risk_msg = coord.check_risk_limits()
                if not risk_ok:
                    status_label = "KILL_SWITCH_HALTED" if "Kill switch" in risk_msg else "REJECTED"
                    setup_output.status = status_label
                    execution_receipt = {
                        "status": status_label,
                        "reason": risk_msg,
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                # Check 2: Global Position Lock
                can_open, lock_msg = coord.global_lock.can_open_position(setup_output.symbol, setup_output.strategy_id)
                if not can_open:
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"Global position lock rejected: {lock_msg}",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                # Check 3: Duplicate Signal Prevention (Idempotency)
                is_dup, dup_msg = coord.global_lock.check_duplicate_signal(setup_output.symbol, setup_output.strategy_id, setup_output.direction)
                if is_dup:
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"Duplicate order prevented: {dup_msg}",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                # Check 4: Strict Sizing Invariant Verification
                if setup_output.max_risk_usdt > 0.205:
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"Planned risk ${setup_output.max_risk_usdt:.3f} breaches $0.20 limit",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                if setup_output.isolated_margin_usdt > 7.05:
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"Margin ${setup_output.isolated_margin_usdt:.2f} breaches $7.00 limit",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                if setup_output.stop_distance_pct < 0.395 or setup_output.stop_distance_pct > 0.805:
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"Stop distance {setup_output.stop_distance_pct:.2f}% not in [0.40%, 0.80%] bounds",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                if setup_output.notional_usdt < 5.0:
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"Notional ${setup_output.notional_usdt:.2f} below Aster DEX $5.00 min notional",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                # Dispatch Real Limit Order on Aster DEX Mainnet
                now_utc = datetime.now(timezone.utc)
                now_ms = int(now_utc.timestamp() * 1000)
                temp_order_id = f"LIV_{setup_output.symbol}_{now_ms}"

                # Acquire global lock in SUBMITTING state
                coord.global_lock.acquire(
                    strategy_id=setup_output.strategy_id,
                    symbol=setup_output.symbol,
                    order_id=temp_order_id,
                    state="SUBMITTING",
                )

                if not live_adapter:
                    coord.global_lock.release(reason="No live exchange adapter initialized")
                    setup_output.status = "ERROR"
                    execution_receipt = {
                        "status": "ERROR",
                        "error": "Live exchange adapter is not initialized or Aster credentials missing",
                        "timestamp_utc": now_utc.isoformat(),
                    }
                    return setup_output, execution_receipt, None

                try:
                    # Enforce ISOLATED 10x leverage
                    try:
                        live_adapter.set_margin_type(setup_output.symbol, "ISOLATED")
                        live_adapter.set_leverage(setup_output.symbol, 10)
                    except Exception as cfg_err:
                        logger.warning(f"Aster DEX margin/leverage config note: {cfg_err}")

                    # 1. Place Limit Entry Order
                    order_side = "BUY" if setup_output.direction == "LONG" else "SELL"
                    close_side = "SELL" if setup_output.direction == "LONG" else "BUY"

                    live_order_resp = live_adapter.create_order(
                        symbol=setup_output.symbol,
                        side=order_side,
                        order_type="LIMIT",
                        quantity=setup_output.qty,
                        price=setup_output.entry_price,
                        time_in_force="GTC",
                    )
                    real_order_id = str(live_order_resp.get("orderId", temp_order_id))

                    # 2. Place Exchange-Side Reduce-Only Stop Loss Order
                    sl_resp = None
                    sl_order_id = None
                    try:
                        sl_resp = live_adapter.create_order(
                            symbol=setup_output.symbol,
                            side=close_side,
                            order_type="STOP_MARKET",
                            quantity=setup_output.qty,
                            stop_price=setup_output.stop_loss_price,
                            reduce_only=True,
                        )
                        sl_order_id = str(sl_resp.get("orderId")) if sl_resp else None
                    except Exception as sl_err:
                        logger.critical("EMERGENCY: Exchange-side SL failed for %s: %s", setup_output.symbol, sl_err)

                    # Emergency handling: If SL order failed, immediately cancel resting entry order to prevent naked risk
                    if not sl_order_id:
                        logger.critical("Cancelling entry order %s immediately due to SL placement failure!", real_order_id)
                        try:
                            if hasattr(live_adapter, "cancel_order"):
                                live_adapter.cancel_order(symbol=setup_output.symbol, order_id=real_order_id)
                        except Exception as cancel_err:
                            logger.error("Failed to cancel entry order after SL failure: %s", cancel_err)

                        coord.global_lock.release(reason="SL_PLACEMENT_FAILURE_EMERGENCY_CANCEL")
                        setup_output.status = "ERROR"
                        execution_receipt = {
                            "status": "ERROR",
                            "order_id": real_order_id,
                            "error": "Emergency handling executed: Protective Stop Loss failed to lodge on exchange. Resting limit entry order was cancelled immediately.",
                            "timestamp_utc": now_utc.isoformat(),
                        }
                        return setup_output, execution_receipt, None

                    # 3. Handle Exchange-Side Limit Take Profit (Section 3 Spec)
                    # Note on Aster DEX Perpetual Futures: Reduce-only LIMIT TP orders cannot rest
                    # without an active position. TP is only placed if entry is immediately filled,
                    # or deferred until entry fill is detected via reconciliation.
                    executed_qty = float(live_order_resp.get("executedQty", 0.0) or 0.0)
                    entry_status = str(live_order_resp.get("status", "NEW")).upper()
                    tp_resp = None
                    tp_order_id = None
                    tp_status = "NOT_PLACED"
                    tp_order_type = "LIMIT"
                    tp_quantity = 0.0
                    protection_status = "PARTIALLY_PROTECTED (SL ONLY - TP NOT PLACED)"

                    if executed_qty > 0 or entry_status == "FILLED":
                        fill_qty = executed_qty if executed_qty > 0 else setup_output.qty
                        try:
                            tp_resp = live_adapter.create_order(
                                symbol=setup_output.symbol,
                                side=close_side,
                                order_type="LIMIT",
                                quantity=fill_qty,
                                price=self.inspector.get_symbol_specs(setup_output.symbol).round_price(setup_output.take_profit_price, rounding="NEAREST"),
                                reduce_only=True,
                                time_in_force="GTC",
                            )
                            tp_order_id = str(tp_resp.get("orderId")) if tp_resp else None
                            if tp_order_id:
                                tp_status = "PLACED"
                                tp_quantity = fill_qty
                                protection_status = "PROTECTED"
                        except Exception as tp_err:
                            logger.warning(f"Immediate TP placement failed: {tp_err}")
                            tp_status = "REJECTED"
                            protection_status = "PARTIALLY_PROTECTED (SL ONLY - TP NOT PLACED)"
                    else:
                        logger.info(
                            "Entry limit order %s is resting (unfilled). Exchange-side LIMIT TP order deferred until entry fill.",
                            real_order_id,
                        )

                    # Update Global Lock to PROTECTED / PARTIALLY_PROTECTED state with exchange order id
                    coord.global_lock.update_state("PROTECTED" if tp_order_id else "PARTIALLY_PROTECTED", order_id=real_order_id)

                    # Record in Automated Trade Journal
                    trade_rec = AutomatedTradeRecord(
                        journal_id=f"JRN_{setup_output.symbol}_{now_ms}",
                        trade_id=f"TRD_{setup_output.symbol}_{now_ms}",
                        exchange="Aster DEX",
                        symbol=setup_output.symbol,
                        strategy_id="EMA20_RECLAIM_5M_V1",
                        config_version="LIVE_MICRO_V1",
                        signal_id=f"SIG_{setup_output.symbol}_{now_ms}",
                        order_id=real_order_id,
                        client_order_id=f"CLO_{setup_output.symbol}_{now_ms}",
                        mode="LIVE",
                        side=setup_output.direction,
                        status="OPEN" if (executed_qty > 0 or entry_status == "FILLED") else "PENDING",
                        signal_timestamp_utc=now_utc.isoformat(),
                        planned_entry_price=setup_output.entry_price,
                        planned_stop_price=setup_output.stop_loss_price,
                        planned_take_profit_price=setup_output.take_profit_price,
                        planned_stop_pct=setup_output.stop_distance_pct,
                        planned_risk_usd=setup_output.max_risk_usdt,
                        planned_quantity=setup_output.qty,
                        planned_notional_usd=setup_output.notional_usdt,
                        planned_margin_usd=setup_output.isolated_margin_usdt,
                        planned_rr_net_expected=setup_output.expected_net_rr,
                        leverage=10,
                        margin_mode="ISOLATED",
                        entry_order_type="LIMIT",
                        tp_order_id=tp_order_id,
                        tp_order_type=tp_order_type,
                        tp_status=tp_status,
                        tp_price=setup_output.take_profit_price,
                        tp_quantity=tp_quantity,
                        sl_order_id=sl_order_id,
                        sl_order_type="STOP_MARKET",
                        sl_status="PLACED",
                        sl_price=setup_output.stop_loss_price,
                        protection_status=protection_status,
                        llm_selected=1,
                        llm_reason=setup_output.notes or setup_output.reasoning,
                        trigger_reason="5m EMA20 Reclaim-Pullback",
                        risk_gate_result="PASSED",
                        cost_gate_result="PASSED",
                    )
                    if auto_journal:
                        auto_journal.record_trade_intent(trade_rec)
                    if sess_mgr:
                        sess_mgr.record_order_placed(setup_output.symbol, real_order_id)

                    setup_output.status = protection_status
                    execution_receipt = {
                        "status": protection_status,
                        "order_id": real_order_id,
                        "sl_order_id": sl_order_id,
                        "sl_order_type": "STOP_MARKET",
                        "sl_status": "PLACED",
                        "sl_price": setup_output.stop_loss_price,
                        "tp_order_id": tp_order_id,
                        "tp_order_type": tp_order_type,
                        "tp_status": tp_status,
                        "tp_price": setup_output.take_profit_price,
                        "tp_quantity": tp_quantity,
                        "protection_status": protection_status,
                        "symbol": setup_output.symbol,
                        "direction": setup_output.direction,
                        "order_type": "LIMIT",
                        "entry_price": setup_output.entry_price,
                        "quantity": setup_output.qty,
                        "fill_quantity": executed_qty,
                        "average_fill_price": None,
                        "notional_usdt": setup_output.notional_usdt,
                        "margin_usdt": setup_output.isolated_margin_usdt,
                        "max_risk_usdt": setup_output.max_risk_usdt,
                        "expected_net_rr": setup_output.expected_net_rr,
                        "execution_venue": "LIVE",
                        "strategy_id": "EMA20_RECLAIM_5M_V1",
                        "journal_trade_id": trade_rec.trade_id,
                        "tp_sl_protection_status": protection_status,
                        "reconciliation_status": "RECORDED_IN_JOURNAL",
                        "timestamp_utc": now_utc.isoformat(),
                    }

                except Exception as ex_err:
                    logger.error("Live order submission failed: %s", ex_err, exc_info=True)
                    coord.global_lock.release(reason=f"SUBMISSION_ERROR: {ex_err}")
                    setup_output.status = "ERROR"
                    execution_receipt = {
                        "status": "ERROR",
                        "error": str(ex_err),
                        "timestamp_utc": now_utc.isoformat(),
                    }

        return setup_output, execution_receipt, confirmation_payload

    def apply_mathematical_guardrails(
        self,
        raw_setup: Dict[str, Any],
        specs: SymbolSpecs,
        risk_preset: RiskPreset,
        execution_venue: ExecutionVenue,
        target_risk_cap: Optional[float] = None,
    ) -> Stage3SetupOutput:
        """Strict Python Mathematical Guardrail for Zero Hallucination Micro-Account Sizing."""
        if target_risk_cap is not None:
            target_risk_usdt = target_risk_cap
        elif execution_venue == ExecutionVenue.LIVE:
            target_risk_usdt = 0.20
        else:
            target_risk_usdt = 0.35 if risk_preset == RiskPreset.CONSERVATIVE_035 else 0.80

        win_symbol = raw_setup.get("symbol", getattr(specs, "symbol", "")).strip().upper()
        direction = raw_setup.get("direction", "LONG").upper()

        raw_entry = float(raw_setup.get("entry_price", 0.0))
        raw_sl = float(raw_setup.get("stop_loss_price", 0.0))
        raw_tp = float(raw_setup.get("take_profit_price", 0.0))

        # Dynamic precision & rounding
        prec = get_dynamic_precision(raw_entry, specs.tick_size)
        entry_price = round_price_dyn(raw_entry, prec, specs.tick_size)
        stop_loss_price = round_price_dyn(raw_sl, prec, specs.tick_size)
        take_profit_price = round_price_dyn(raw_tp, prec, specs.tick_size)

        # Enforce Minimum Stop Floor: >= 0.40% (LIVE) or >= 1.15% (Paper legacy default)
        min_stop_floor = 0.0040 if execution_venue == ExecutionVenue.LIVE else 0.0115
        stop_dist_pct = abs(entry_price - stop_loss_price) / (entry_price + 1e-9)
        if stop_dist_pct < min_stop_floor:
            stop_dist_pct = 0.0055 if execution_venue == ExecutionVenue.LIVE else 0.0130
            if direction == "LONG":
                stop_loss_price = round_price_dyn(entry_price * (1.0 - stop_dist_pct), prec, specs.tick_size)
            else:
                stop_loss_price = round_price_dyn(entry_price * (1.0 + stop_dist_pct), prec, specs.tick_size)

        # Enforce R:R Multiple
        sl_dist = abs(entry_price - stop_loss_price)
        tp_dist = abs(take_profit_price - entry_price)
        rr_ratio = round(tp_dist / (sl_dist + 1e-9), 2)
        if rr_ratio < 1.60:
            rr_ratio = 2.25 if execution_venue == ExecutionVenue.LIVE else 1.85
            if direction == "LONG":
                take_profit_price = round_price_dyn(entry_price + (sl_dist * rr_ratio), prec, specs.tick_size)
            else:
                take_profit_price = round_price_dyn(entry_price - (sl_dist * rr_ratio), prec, specs.tick_size)

        # Position Sizing Calculation
        sl_dist = abs(entry_price - stop_loss_price)
        raw_qty = target_risk_usdt / (sl_dist + 1e-9)
        step = specs.step_size or 0.001
        step_prec = max(0, int(round(-math.log10(step)))) if step < 1.0 else 0

        # Step size truncation
        qty = math.floor(raw_qty / step) * step
        qty = round(qty, step_prec)

        if qty < specs.min_qty:
            qty = specs.min_qty
        notional_usdt = round(qty * entry_price, 2)
        if notional_usdt < 5.0:
            qty = math.ceil(5.05 / (entry_price * step)) * step
            qty = round(qty, step_prec)
            notional_usdt = round(qty * entry_price, 2)

        isolated_margin_usdt = round(notional_usdt / 10.0, 2)
        if isolated_margin_usdt > 7.00:
            qty = math.floor(70.0 / (entry_price * step)) * step
            qty = round(qty, step_prec)
            notional_usdt = round(qty * entry_price, 2)
            isolated_margin_usdt = round(notional_usdt / 10.0, 2)

        actual_risk_usdt = round(qty * sl_dist, 2)
        est_net_rr = round(rr_ratio * 0.90, 2)

        return Stage3SetupOutput(
            decision="EXECUTE",
            symbol=win_symbol,
            direction=direction,
            order_type="LIMIT",
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            qty=qty,
            leverage=10,
            margin_mode="ISOLATED",
            max_risk_usdt=actual_risk_usdt,
            stop_distance_pct=round(stop_dist_pct * 100, 2),
            rr_ratio=rr_ratio,
            notional_usdt=notional_usdt,
            isolated_margin_usdt=isolated_margin_usdt,
            execution_venue=execution_venue.value if hasattr(execution_venue, "value") else str(execution_venue),
            notes=raw_setup.get("notes", f"{win_symbol} Groq Qwen automated setup"),
            reasoning=raw_setup.get("reasoning", "Optimal multi-timeframe SMC confluence with verified isolated sizing."),
            strategy_id="EMA20_RECLAIM_5M_V1",
            strategy_eligible=True,
            expected_net_rr=est_net_rr,
            estimated_costs_usd=round(notional_usdt * 0.0012, 4),
            status="PREVIEW",
        )

    def run_full_pipeline(
        self,
        risk_preset: RiskPreset = RiskPreset.GROWTH_080,
        execution_venue: Any = ExecutionVenue.PAPER,
        execution_mode: Optional[Any] = None,
        model_override: Optional[str] = None,
        auto_fire_limit_order: bool = False,
        operator_confirmed: bool = False,
        strategy_id: str = "EMA20_RECLAIM_5M_V1",
        journal: Optional[Any] = None,
        runners: Optional[Dict[str, Any]] = None,
        live_adapter: Optional[Any] = None,
        coordinator: Optional[LiveMicroCoordinator] = None,
        automated_journal: Optional[AutomatedTradeJournal] = None,
        session_manager: Optional[Any] = None,
        selected_setup: Optional[Dict[str, Any]] = None,
    ) -> GroqAutoTradePipelineResult:
        """Run the complete 10 -> 3 -> 1 Pipeline end-to-end with session-authorized LIVE auto-execution."""
        cycle = self._cycle_start()
        coord = coordinator or self.coordinator
        sess_mgr = session_manager or self.session_manager
        effective_venue = execution_mode or execution_venue

        # Fast Strategy Authorization Pre-Check
        if strategy_id == "SWEEP_1M_V1":
            raise PermissionError("CRITICAL SECURITY INVARIANT: SWEEP_1M_V1 is SHADOW-only and strictly prohibited from live execution.")
        if strategy_id != "EMA20_RECLAIM_5M_V1":
            raise ValueError(f"Strategy '{strategy_id}' is not authorized for live execution.")

        # Stage 1: Deterministic Screener
        stage1 = self.run_stage1_prescreen()

        # Stage 2: Groq LLM Call #1
        stage2 = self.run_stage2_rank_top3(stage1, model_override=model_override)

        # Brief 1s pacing for Groq Free Tier TPM
        time.sleep(1.0)

        # Stage 3 & 4: Groq LLM Call #2 + Strategy Evaluation (EMA20_RECLAIM_5M_V1) + Supervised Execution
        stage3_started = self._stage_start("stage3_total")
        stage3, receipt, confirmation = self.run_stage3_finalist_and_order(
            stage1=stage1,
            stage2=stage2,
            risk_preset=risk_preset,
            execution_venue=effective_venue,
            model_override=model_override,
            auto_fire_limit_order=auto_fire_limit_order,
            operator_confirmed=operator_confirmed,
            strategy_id=strategy_id,
            journal=journal,
            runners=runners,
            live_adapter=live_adapter,
            coordinator=coord,
            automated_journal=automated_journal or self.automated_journal,
            session_manager=sess_mgr,
            selected_setup=selected_setup,
        )
        self._stage_end("stage3_total", stage3_started)

        # Collect live risk gate diagnostics
        risk_ok, risk_reason = coord.check_risk_limits()
        can_lock, lock_reason = coord.global_lock.can_open_position(stage3.symbol, strategy_id)
        risk_gate_status = {
            "risk_limits_ok": risk_ok,
            "risk_limits_reason": risk_reason,
            "global_lock_available": can_lock,
            "global_lock_reason": lock_reason,
            "current_equity_usd": coord.current_equity,
            "daily_filled_entries": coord.daily_filled_entries,
            "max_filled_entries": coord.config.risk.max_filled_entries_per_day,
            "daily_realized_loss_usd": coord.daily_realized_loss,
            "daily_loss_limit_usd": coord.config.risk.daily_loss_limit_usd,
            "kill_switch_active": coord.kill_switch_triggered,
            "session_info": sess_mgr.get_session_info() if sess_mgr else {},
        }

        pipeline_status = stage3.status

        result = GroqAutoTradePipelineResult(
            status=pipeline_status,
            risk_preset=risk_preset.value if hasattr(risk_preset, "value") else str(risk_preset),
            execution_venue=execution_venue.value if hasattr(execution_venue, "value") else str(execution_venue),
            strategy_id=strategy_id,
            stage1=stage1,
            stage2=stage2,
            stage3=stage3,
            execution_receipt=receipt,
            confirmation_payload=confirmation,
            risk_gate_status=risk_gate_status,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
        self._finish_cycle(cycle, "SUCCESS")
        return result
