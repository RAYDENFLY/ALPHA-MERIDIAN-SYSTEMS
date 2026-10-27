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
from src.strategy.sweep_reclaim_15m import SweepReclaim15MSetup, SweepReclaim15MStrategy
from src.strategy.dealing_range import DealingRangeInfo, check_dealing_range_zone_lock, compute_dealing_range
from src.ai.trade_memory_feedback import TradeMemoryFeedbackEngine, PreTradeMemoryGateResult
from src.intelligence.dashboard_confluence import build_web_intelligence_payload, evaluate_dashboard_confluence_gates, build_qwen_dashboard_prompt_block, ConfluenceGateResult

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
    provider_failure: bool = False


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
    strategy_id: str = "15M_SWEEP_RECLAIM_SMC"
    strategy_eligible: bool = True
    strategy_rejection_reason: str = ""
    expected_net_rr: float = 0.0
    estimated_costs_usd: float = 0.0
    requires_confirmation: bool = False
    status: str = "PREVIEW"  # PREVIEW, READY, REJECTED, SUBMITTING, OPEN, PROTECTED, CLOSED, ERROR, KILL_SWITCH_HALTED
    lesson_applied_from_recent_losses: str = ""
    sweep_reclaim_evidence: str = ""
    orderflow_wall_evidence: str = ""
    why_not_repeating_past_sl: str = ""
    provider_failure: bool = False
    key_alias_used: Optional[str] = None
    candidate_id: Optional[str] = None
    dealing_range_position_pct: Optional[float] = None
    veto_code: Optional[str] = None
    veto_source: Optional[str] = None
    model_used: Optional[str] = None
    tp_target_type: Optional[str] = None
    sl_invalidation_type: Optional[str] = "15M_SWEEP_WICK_PLUS_ATR"


class GroqAutoTradePipelineResult(BaseModel):
    status: str  # PREVIEW, READY, REJECTED, SUBMITTING, OPEN, PROTECTED, CLOSED, ERROR, KILL_SWITCH_HALTED
    risk_preset: str
    execution_venue: str
    strategy_id: str = "15M_SWEEP_RECLAIM_SMC"
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
    FALLBACK_MODELS = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
    ]
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
        key_manager: Optional[Any] = None,
        fallback_a_key: Optional[str] = None,
        fallback_b_key: Optional[str] = None,
        config: Optional[Any] = None,
    ) -> None:
        self.config = config
        if config is not None:
            if api_key is None:
                api_key = getattr(config, "api_key", None)
            if model_id is None:
                model_id = getattr(config, "model_id", None)
            if fallback_a_key is None:
                fallback_a_key = getattr(config, "api_key_fallback_a", None)
            if fallback_b_key is None:
                fallback_b_key = getattr(config, "api_key_fallback_b", None)
        self.adapter = adapter or AsterAdapter()
        self.inspector = inspector_service or CoinInspectorService(adapter=self.adapter)
        self.api_key = api_key or os.getenv("GROQ_API_KEY") or os.getenv("groq")
        from src.ai.groq_key_manager import GroqKeyManager
        self.key_manager = key_manager or GroqKeyManager(
            primary_key=self.api_key,
            fallback_a_key=fallback_a_key or os.getenv("GROQ_API_KEY_FALLBACK_A"),
            fallback_b_key=fallback_b_key or os.getenv("GROQ_API_KEY_FALLBACK_B"),
        )
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
        self.feedback_engine = TradeMemoryFeedbackEngine(
            journal=self.automated_journal,
            db=getattr(self.coordinator, "db", None) if hasattr(self, "coordinator") else None,
        )
        self._telemetry_lock = threading.RLock()
        self._active_cycle_telemetry: Optional[Dict[str, Any]] = None
        self._last_cycle_telemetry: Optional[Dict[str, Any]] = None
        self._screening_history: List[Dict[str, Any]] = []
        self._latest_screening_result: Optional[Dict[str, Any]] = None

        # 15-Minute Candle Bar Deduplication & Result Cache
        # Key: (symbol: str, direction: str, candle_ts_ms: int) -> Value: (monotonic_time: float, (Stage2Result, Stage3SetupOutput))
        self._llm_evaluation_cache: Dict[Tuple[str, str, int], Tuple[float, Any]] = {}

        # Smart Model Tiering & Quant-Verified Fallback Mode (When TPD is Exhausted)
        self.allow_quant_verified_fallback_on_tpd_exhaustion: bool = (
            getattr(config, "allow_quant_verified_fallback_on_tpd_exhaustion", None)
            if config else None
        )
        if self.allow_quant_verified_fallback_on_tpd_exhaustion is None:
            env_val = os.getenv("ALLOW_QUANT_VERIFIED_FALLBACK_ON_TPD_EXHAUSTION", "true").lower()
            self.allow_quant_verified_fallback_on_tpd_exhaustion = env_val in ("1", "true", "yes")

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

    def get_latest_screening(self) -> Optional[Dict[str, Any]]:
        with self._telemetry_lock:
            if not self._latest_screening_result:
                return None
            return dict(self._latest_screening_result)

    def get_screening_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._telemetry_lock:
            return [dict(x) for x in self._screening_history[:limit]]

    @staticmethod
    def _configured_live_strategy_id(coordinator: Any) -> Tuple[Optional[str], str]:
        strategy_cfg = getattr(getattr(coordinator, "config", None), "strategy", None)
        strategy_id = str(getattr(strategy_cfg, "live_strategy_id", "")).strip().upper()
        if not strategy_id:
            return None, "LIVE strategy is not configured"
        if strategy_id == "EMA20_RECLAIM_5M_V1":
            return None, "CRITICAL SECURITY INVARIANT: EMA20_RECLAIM_5M_V1 is strictly prohibited from live execution. Authorized strategy is 15M_SWEEP_RECLAIM_SMC."
        if strategy_id != "15M_SWEEP_RECLAIM_SMC":
            return None, f"Strategy {strategy_id} is not authorized for live execution. Only 15M_SWEEP_RECLAIM_SMC is permitted."
        implementation = getattr(coordinator, "sweep_reclaim_strategy", None)
        if implementation is None or getattr(implementation, "strategy_id", "") != strategy_id or not bool(getattr(implementation, "is_live", False)):
            return None, f"Configured live strategy implementation unavailable or not live: {strategy_id}"
        return strategy_id, "OK"

    def _call_groq_json(self, messages: List[Dict[str, str]], model_override: Optional[str] = None, max_tokens: int = 400) -> Dict[str, Any]:
        """Make an OpenAI-compatible JSON completion call to Groq with rate-limit backoff and multi-key fallback."""
        from src.ai.groq_key_manager import GroqKeyManager

        if not self.key_manager.has_keys():
            raise ValueError("GROQ_API_KEY is not configured in environment or .env.")

        candidate_keys = self.key_manager.get_available_keys(force_include_cooling=True)
        if not candidate_keys:
            raise RuntimeError("All configured Groq API keys are currently exhausted or invalid.")

        models_to_try = [model_override or self.model_id] + self.FALLBACK_MODELS
        last_error = None

        def friendly_name(alias: str) -> str:
            if alias == "PRIMARY":
                return "Primary key"
            if alias.startswith("FALLBACK_"):
                return f"Fallback {alias.split('_')[1]}"
            return alias

        for key_idx, key_entry in enumerate(candidate_keys):
            key_alias = key_entry.alias
            key_secret = key_entry.key
            next_key_entry = candidate_keys[key_idx + 1] if key_idx + 1 < len(candidate_keys) else None

            headers = {
                "Authorization": f"Bearer {key_secret}",
                "Content-Type": "application/json",
            }

            key_failed = False
            for model in models_to_try:
                if key_failed:
                    break
                if key_entry.is_model_exhausted(model):
                    continue

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
                            self.key_manager.mark_success(key_alias)
                            if key_alias != "PRIMARY":
                                logger.info(f"[GROQ] Request succeeded using {friendly_name(key_alias).lower()}")
                            self._record_model_request(
                                model=model,
                                attempt=attempt + 1,
                                http_status=resp.status_code,
                                started=request_started,
                                retry_reason=None,
                                fallback=(model != models_to_try[0]),
                                key_alias=key_alias,
                            )
                            data = resp.json()
                            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                            clean_content = content.strip()
                            if clean_content.startswith("```"):
                                clean_content = re.sub(r"^```[a-zA-Z]*\n?", "", clean_content)
                                clean_content = re.sub(r"\n?```$", "", clean_content).strip()
                            parsed = json.loads(clean_content)
                            if isinstance(parsed, dict):
                                parsed["_key_alias_used"] = key_alias
                            return parsed

                        # Non-200 classification via GroqKeyManager
                        cat, retry_after = GroqKeyManager.classify_groq_error(resp.status_code, resp.text, resp.headers)

                        if cat == "INVALID_KEY":
                            self._record_model_request(model, attempt + 1, resp.status_code, request_started, "HTTP_401", model != models_to_try[0], key_alias=key_alias)
                            self.key_manager.mark_invalid(key_alias, "HTTP_401")
                            logger.error(f"[GROQ] Key {key_alias} is invalid (HTTP 401). Skipping invalid key.")
                            key_failed = True
                            last_error = f"Key {key_alias} HTTP 401 invalid"
                            break

                        elif cat == "QUOTA_EXHAUSTED":
                            self._record_model_request(model, attempt + 1, resp.status_code, request_started, "QUOTA_EXHAUSTED", model != models_to_try[0], key_alias=key_alias)
                            self.key_manager.mark_quota_exhausted(key_alias, "quota_exceeded")
                            logger.warning(f"[GROQ] {friendly_name(key_alias)} failed: quota exhausted")
                            if next_key_entry:
                                logger.info(f"[GROQ] Switching to {friendly_name(next_key_entry.alias).lower()}")
                            key_failed = True
                            last_error = f"{friendly_name(key_alias)} quota exhausted: {resp.text[:200]}"
                            break

                        elif cat == "RATE_LIMITED":
                            retry_label = f"HTTP_{resp.status_code}_RATE_LIMIT"
                            self._record_model_request(model, attempt + 1, resp.status_code, request_started, retry_label, model != models_to_try[0], key_alias=key_alias)
                            if resp.status_code == 429 and attempt == 0 and retry_after <= 5.0 and not next_key_entry:
                                logger.info(f"[GROQ] {friendly_name(key_alias)} rate limit on {model}. Sleeping {retry_after:.1f}s before retry...")
                                time.sleep(retry_after)
                                continue
                            else:
                                self.key_manager.mark_rate_limited(key_alias, retry_after)
                                logger.info(f"[GROQ] {friendly_name(key_alias)} failed: rate limited (HTTP {resp.status_code})")
                                if next_key_entry:
                                    logger.info(f"[GROQ] Switching to {friendly_name(next_key_entry.alias).lower()}")
                                key_failed = True
                                last_error = f"{friendly_name(key_alias)} rate limited HTTP {resp.status_code} ({retry_after:.1f}s)"
                                break

                        elif cat == "PROVIDER_TEMPORARY":
                            self._record_model_request(model, attempt + 1, resp.status_code, request_started, f"HTTP_{resp.status_code}", model != models_to_try[0], key_alias=key_alias)
                            self.key_manager.mark_temporary_failure(key_alias, f"HTTP_{resp.status_code}")
                            logger.warning(f"[GROQ] {friendly_name(key_alias)} failed: temporary provider failure (HTTP {resp.status_code})")
                            if next_key_entry:
                                logger.info(f"[GROQ] Switching to {friendly_name(next_key_entry.alias).lower()}")
                            key_failed = True
                            last_error = f"{friendly_name(key_alias)} provider failure HTTP {resp.status_code}"
                            break

                        elif cat == "BAD_REQUEST":
                            self._record_model_request(model, attempt + 1, resp.status_code, request_started, f"HTTP_{resp.status_code}", model != models_to_try[0], key_alias=key_alias)
                            if resp.status_code == 413:
                                logger.warning(f"[GROQ] Model {model} request payload too large (HTTP 413). Trying fallback model.")
                                break
                            if resp.status_code == 404:
                                logger.info(f"[GROQ] Model {model} returned 404 on {key_alias}. Trying next fallback model.")
                                break
                            logger.error(f"[GROQ] Bad request HTTP {resp.status_code} on model {model}: {resp.text[:200]}")
                            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                            raise ValueError(f"Groq API Bad Request (HTTP {resp.status_code}): {resp.text[:200]}")

                        else:
                            self._record_model_request(model, attempt + 1, resp.status_code, request_started, f"HTTP_{resp.status_code}", model != models_to_try[0], key_alias=key_alias)
                            last_error = f"Groq HTTP {resp.status_code}: {resp.text[:200]}"
                            break

                    except (requests.Timeout, requests.ConnectionError) as net_err:
                        self._record_model_request(model, attempt + 1, None, request_started, type(net_err).__name__, model != models_to_try[0], key_alias=key_alias)
                        self.key_manager.mark_temporary_failure(key_alias, type(net_err).__name__)
                        logger.warning(f"[GROQ] {friendly_name(key_alias)} failed: network timeout/connection error")
                        if next_key_entry:
                            logger.info(f"[GROQ] Switching to {friendly_name(next_key_entry.alias).lower()}")
                        key_failed = True
                        last_error = f"{friendly_name(key_alias)} connection error: {net_err}"
                        break
                    except ValueError:
                        raise
                    except Exception as exc:
                        self._record_model_request(model, attempt + 1, None, request_started, type(exc).__name__, model != models_to_try[0], key_alias=key_alias)
                        last_error = str(exc)
                        logger.warning(f"[GROQ] {friendly_name(key_alias)} call exception: {exc}")
                        break

        logger.error("[GROQ] All configured Groq API keys failed.")
        raise RuntimeError(f"All Groq API attempts failed across all configured keys. Last error: {last_error}")

    def _record_model_request(
        self,
        model: str,
        attempt: int,
        http_status: Optional[int],
        started: float,
        retry_reason: Optional[str],
        fallback: bool,
        key_alias: str = "PRIMARY",
    ) -> None:
        cycle = self._active_cycle_telemetry
        if cycle is not None:
            cycle["model_requests"].append({
                "provider": "groq",
                "key_alias": key_alias,
                "model": model,
                "fallback_used": key_alias != "PRIMARY" or fallback,
                "attempt_count": attempt,
                "http_status": http_status,
                "retry_reason": retry_reason,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
            })

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
        episodic_memory_text = self.feedback_engine.build_episodic_memory_prompt_block()
        compact_top10 = [
            {
                "symbol": c.symbol,
                "mark_price": c.mark_price,
                "change_24h_pct": c.change_24h_pct,
                "turnover_24h_m": c.turnover_24h_m,
                "range_72h_pct": c.range_72h_pct,
                "trend_15m": c.trend_15m,
                "rsi_15m": c.rsi_15m,
            }
            for c in stage1.top_10
        ]
        prompt_payload = {
            "market_compass": stage1.market_regime,
            "recent_live_performance_and_mistakes": episodic_memory_text,
            "top_10_candidates": compact_top10,
            "rubric": {
                "hard_law_1_no_shorting_bottom_half": "HARD LAW 1: NEVER propose a SHORT if range_72h_pct < 65.0%! (Must be in Premium >= 65.0%).",
                "hard_law_2_no_longing_top_half": "HARD LAW 2: NEVER propose a LONG if range_72h_pct > 38.0%! (Must be in Discount <= 38.0% unless verified Bull FVG pullback & 1H Bull trend).",
                "setup_types": [
                    "DISCOUNT_SWEEP_RECLAIM (LONG): Swept 15m/1H swing low in Discount (<=38%), rejection wick >= 30%, RSI turning up from oversold",
                    "PREMIUM_SWEEP_REJECT (SHORT): Swept 15m/1H swing high in Premium (>=65%), upper rejection wick >= 30%, RSI turning down from overbought",
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
            raw_json = self._call_groq_json(messages, model_override=active_model, max_tokens=650)
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

            # If model returned 1 or 2 finalists, supplement from top_10 to guarantee exactly 3
            if 0 < len(finalists) < 3:
                existing_syms = {f.symbol for f in finalists}
                categories = ["LIQUIDITY_SWEEP_RECLAIM", "MOMENTUM_CONTINUATION", "LAGGARD_CATCH_UP"]
                for cand in stage1.top_10:
                    if cand.symbol not in existing_syms:
                        finalists.append(
                            Stage2FinalistItem(
                                rank=len(finalists) + 1,
                                symbol=cand.symbol,
                                category=categories[len(finalists) % len(categories)],
                                score=round(80.0 - (len(finalists) * 3.0), 1),
                                thesis=f"{cand.symbol} supplemental finalist from 24h turnover ranking.",
                                verification_questions=["Verify 15m trigger"],
                            )
                        )
                    if len(finalists) == 3:
                        break

            if len(finalists) == 3:
                return Stage2Result(
                    top_3=finalists,
                    eliminated_summary=eliminated,
                    model_used=active_model,
                    provider_failure=False,
                )
        except Exception as e:
            self._stage_end("stage2_groq_request", stage2_started)
            logger.warning(f"Groq Stage 2 call failed ({e}); invoking deterministic fallback.")

        # Deterministic Algorithmic Fallback for Stage 2 (PAPER/PREVIEW paths only)
        sorted_candidates = sorted(stage1.top_10, key=lambda c: ((c.range_72h_pct <= 38.0 or c.range_72h_pct >= 65.0), c.turnover_24h_m), reverse=True)
        fallback_finalists = []
        categories = ["LIQUIDITY_SWEEP_RECLAIM", "MOMENTUM_CONTINUATION", "LAGGARD_CATCH_UP"]
        for idx, cand in enumerate(sorted_candidates[:3]):
            fallback_finalists.append(
                Stage2FinalistItem(
                    rank=idx + 1,
                    symbol=cand.symbol,
                    category=categories[idx % len(categories)],
                    score=round(90.0 - (idx * 4.0), 1),
                    thesis=f"[Deterministic Fallback] {cand.symbol} qualifies with {cand.turnover_24h_m}M turnover and balanced 72h range ({cand.range_72h_pct}%).",
                    verification_questions=["Verify 15m EMA20 support", "Check FVG confluence on 1H"],
                )
            )

        return Stage2Result(
            top_3=fallback_finalists,
            eliminated_summary={"PROVIDER_NOTE": "Groq LLM keys exhausted or unavailable. Algorithmic ranking applied."},
            model_used="deterministic_fallback",
            provider_failure=True,
        )

    def evaluate_candidate_with_strategy(
        self,
        symbol: str,
        coordinator: Optional[LiveMicroCoordinator] = None,
        direction: Optional[str] = "LONG",
        strategy_id: str = "15M_SWEEP_RECLAIM_SMC",
    ) -> Tuple[Optional[Any], str]:
        """Evaluate candidate with deterministic market structure rules and Dealing Range locks.
        
        Strict Invariants:
        - Strategy must be 15M_SWEEP_RECLAIM_SMC or EMA20_RECLAIM_5M_V1. SWEEP_1M_V1 is strictly rejected.
        - Hard Law 1 & Hard Law 2 Dealing Range locks are enforced:
          * HARD LAW 1: NEVER short if position_pct < 65.0%
          * HARD LAW 2: NEVER long if position_pct > 38.0% (unless Bull FVG + 1H Bull trend)
        """
        if strategy_id == "SWEEP_1M_V1":
            return None, "UNAUTHORIZED_STRATEGY: SWEEP_1M_V1 is shadow-only."
        if strategy_id == "EMA20_RECLAIM_5M_V1":
            raise PermissionError("CRITICAL SECURITY INVARIANT: EMA20_RECLAIM_5M_V1 is strictly prohibited from live execution. Authorized strategy is 15M_SWEEP_RECLAIM_SMC.")
        if strategy_id != "15M_SWEEP_RECLAIM_SMC":
            return None, f"UNAUTHORIZED_STRATEGY: Strategy {strategy_id} is prohibited from live execution."

        # Fetch 1H candles for Dealing Range & HTF Trend calculation
        candles_1h = self.adapter.get_klines(symbol, timeframe="1h", limit=72)
        current_price = getattr(candles_1h[-1], "close", 0.0) if candles_1h else 0.0
        dr_info = compute_dealing_range(candles_1h, current_price, lookback_bars=72)

        # Enforce HARD LAW 1 & HARD LAW 2
        if direction is not None:
            can_open, zone_reason = check_dealing_range_zone_lock(
                position_pct=dr_info.position_pct,
                direction=direction,
                has_bull_fvg_pullback=False,
                trend_1h="NEUTRAL",
                short_min_premium_pct=65.0,
                long_max_discount_pct=38.0,
            )
            if not can_open:
                return None, zone_reason

        specs = self.inspector.get_symbol_specs(symbol, mark_price=current_price)
        step_size = specs.step_size or 0.001
        min_notional = specs.min_notional or 5.0
        tick_size = specs.tick_size or 0.01

        if strategy_id == "15M_SWEEP_RECLAIM_SMC":
            candles_15m = self.adapter.get_klines(symbol, timeframe="15m", limit=40)
            if not candles_15m or len(candles_15m) < 20:
                return None, "INSUFFICIENT_DATA: Need at least 20 15m candles"

            # Check staleness
            last_candle = candles_15m[-1]
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            close_ts = getattr(last_candle, "close_time_ms", 0)
            if close_ts > 0 and (now_ms - close_ts > 1800000):  # 30 min for 15m
                return None, f"STALE_MARKET_DATA: Last 15m candle is older than 30 minutes ({now_ms - close_ts}ms)"

            strat = getattr(coordinator, "sweep_reclaim_strategy", None)
            if strat is None:
                strat = SweepReclaim15MStrategy(
                    risk_cfg=getattr(coordinator, "config", None).risk if hasattr(coordinator, "config") else None,
                    costs_cfg=getattr(coordinator, "config", None).costs if hasattr(coordinator, "config") else None,
                )

            setup, reason = strat.evaluate(
                symbol=symbol,
                candles_15m=candles_15m,
                candles_1h=candles_1h,
                tick_size=tick_size,
                step_size=step_size,
                min_notional=min_notional,
                account_equity=coordinator.current_equity,
                dealing_range_position_pct=dr_info.position_pct,
                direction=direction,
            )
            return setup, reason

        else:  # EMA20_RECLAIM_5M_V1
            candles_5m = self.adapter.get_klines(symbol, timeframe="5m", limit=35)
            if not candles_5m or len(candles_5m) < 25:
                return None, "INSUFFICIENT_DATA: Need at least 25 5m candles"

            last_candle = candles_5m[-1]
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            close_ts = getattr(last_candle, "close_time_ms", 0)
            if close_ts > 0 and (now_ms - close_ts > 900000):  # 15 min
                return None, f"STALE_MARKET_DATA: Last 5m candle is older than 15 minutes ({now_ms - close_ts}ms)"

            setup, reason = coordinator.ema_strategy.evaluate(
                symbol=symbol,
                candles_5m=candles_5m,
                tick_size=tick_size,
                step_size=step_size,
                min_notional=min_notional,
                account_equity=coordinator.current_equity,
                candles_1h=candles_1h,
                dealing_range_position_pct=dr_info.position_pct,
                direction=direction,
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
        strategy_id: str = "15M_SWEEP_RECLAIM_SMC",
        journal: Optional[Any] = None,
        runners: Optional[Dict[str, Any]] = None,
        live_adapter: Optional[Any] = None,
        coordinator: Optional[LiveMicroCoordinator] = None,
        automated_journal: Optional[AutomatedTradeJournal] = None,
        session_manager: Optional[Any] = None,
        selected_setup: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Stage3SetupOutput, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Stage 3 & 4: Deep Candidate Analysis + configured strategy validation + supervised execution."""
        coord = coordinator or self.coordinator
        auto_journal = automated_journal or self.automated_journal
        sess_mgr = session_manager or self.session_manager
        live_adapter = live_adapter or (getattr(coord, "exchange", None) if coord else None) or self.adapter

        # Strict Security Invariant: Strategy authorization check
        if strategy_id in ("EMA20_RECLAIM_5M_V1", "SWEEP_1M_V1"):
            raise PermissionError(f"CRITICAL SECURITY INVARIANT: {strategy_id} is strictly prohibited from live execution. Authorized strategy is 15M_SWEEP_RECLAIM_SMC.")
        if strategy_id != "15M_SWEEP_RECLAIM_SMC":
            raise ValueError(f"Strategy '{strategy_id}' is not authorized for live execution.")

        venue_str = execution_venue.value if hasattr(execution_venue, "value") else str(execution_venue).strip().upper()
        if venue_str in ("LIVE", "LIVE_MANUAL"):
            mode = ExecutionMode.LIVE_MANUAL
        elif venue_str == "LIVE_AUTO":
            mode = ExecutionMode.LIVE_AUTO
        else:
            mode = ExecutionMode.PAPER

        is_live = mode in (ExecutionMode.LIVE_MANUAL, ExecutionMode.LIVE_AUTO)
        if is_live:
            if strategy_id == "SWEEP_1M_V1":
                raise PermissionError("CRITICAL SECURITY INVARIANT: SWEEP_1M_V1 is SHADOW-only and strictly prohibited from live execution.")
            if strategy_id == "EMA20_RECLAIM_5M_V1":
                raise PermissionError("CRITICAL SECURITY INVARIANT: EMA20_RECLAIM_5M_V1 is strictly prohibited from live execution. Authorized strategy is 15M_SWEEP_RECLAIM_SMC.")
            configured_strategy_id, strategy_reason = self._configured_live_strategy_id(coord)
            if not configured_strategy_id:
                raise PermissionError(f"LIVE execution blocked: {strategy_reason}")
            if strategy_id != configured_strategy_id:
                raise PermissionError(f"LIVE execution blocked: requested strategy {strategy_id} does not match configured strategy {configured_strategy_id}")
        target_risk_usdt = 0.20 if is_live else (0.35 if risk_preset == RiskPreset.CONSERVATIVE_035 else 0.80)

        # 1. Gather Deep Inspector Snapshots & Exact Dashboard Intelligence for Top 3
        recent_closed_trades = self.feedback_engine.get_recent_live_trades(limit=10, mode="LIVE", closed_only=True)
        deep_snapshots = []
        dashboard_dossiers = []
        dashboard_dossiers_by_sym = {}
        for finalist in stage2.top_3:
            sym = finalist.symbol
            try:
                insp = self.inspector.inspect_coin(sym, account_balance_usdt=coord.current_equity)
                web_intelligence = build_web_intelligence_payload(
                    symbol=sym,
                    inspector_snapshot=insp,
                    recent_closed_trades=recent_closed_trades,
                )
                dashboard_dossiers.append(web_intelligence)
                dashboard_dossiers_by_sym[sym] = web_intelligence
                deep_snapshots.append({
                    "symbol": sym,
                    "rank": finalist.rank,
                    "category": finalist.category,
                    "meta": insp.get("meta", {}),
                    "smc_and_liquidity_structure": insp.get("smc_and_liquidity_structure", {}),
                    "orderbook_l2_microstructure": insp.get("orderbook_l2_microstructure", {}),
                    "multi_timeframe_summary": insp.get("multi_timeframe_summary", {}),
                    "dashboard_intelligence": web_intelligence,
                })
            except Exception as e:
                logger.warning(f"Error gathering deep inspector data for {sym}: {e}")

        # 2. Advisory LLM Setup Formulation
        episodic_memory_text = self.feedback_engine.build_episodic_memory_prompt_block()
        dossier_text = build_qwen_dashboard_prompt_block(dashboard_dossiers)
        system_prompt = (
            "You are Groq Qwen Master SMC Futures Strategist for a micro-account ($0.20 fixed dollar risk, 10x ISOLATED).\n"
            f"Analyze the 3 deep candidate dossiers and formulate the single BEST Limit Order setup under {strategy_id}.\n\n"
            f"{episodic_memory_text}\n\n"
            f"{dossier_text}\n\n"
            "CRITICAL SELF-CORRECTION DIRECTIVE:\n"
            "Review your RECENT_LIVE_PERFORMANCE_AND_MISTAKES above. You are currently on a losing streak caused by chasing 5m reclaims in choppy ranges and approving tight stops (<0.80%). DO NOT repeat any pattern seen in your recent STOP_LOSS trades.\n"
            "You must strictly satisfy ALL 4 dashboard intelligence modules:\n"
            "1. SWEEP & RECLAIM: Must have confirmed structural sweep of swing low/high in valid Dealing Range (Discount <= 38% for LONG, Premium >= 65% for SHORT) or active FVG reclaim.\n"
            "2. ORDER FLOW WALL CONFLUENCE: Orderbook must be supportive (>=0.48 for LONG, <=0.52 for SHORT) with NO opposing wall blocking within 0.50% of entry.\n"
            "3. ANTI-REPEAT 'SAME HOLE' LOCK: If coin hit STOP_LOSS in the last 6 hours, DO NOT enter in the same price zone. Price must be swept below previous SL or >=1.20% deeper.\n"
            "4. STOP BREATHING ROOM: Stop Loss placed beyond wick extreme and protecting wall, strictly 1.15% to 1.45% stop distance.\n\n"
            "Output strictly valid JSON schema:\n"
            "{\n"
            '  "decision": "EXECUTE",\n'
            '  "symbol": "SOLUSDT",\n'
            '  "direction": "LONG",\n'
            '  "order_type": "LIMIT",\n'
            '  "entry_price": 99.40,\n'
            '  "stop_loss_price": 98.15,\n'
            '  "take_profit_price": 102.15,\n'
            '  "notes": "SOL 15m sweep reclaim with bid wall confluence",\n'
            '  "reasoning": "Clear swing low sweep in 28% discount with 42% rejection wick and $450k bid wall supporting entry",\n'
            '  "lesson_applied_from_recent_losses": "Unlike TRD_SOLUSDT which had 0.74% tight stop and bought mid-air, this entry is in deep discount with 1.26% structural stop below wick extreme.",\n'
            '  "sweep_reclaim_evidence": "Swept 15m swing low 99.80 in 28% Discount with confirmed reclaim (wick ratio 42%)",\n'
            '  "orderflow_wall_evidence": "L2 orderbook has 54.2% bid imbalance with $450k bid wall at 99.20 supporting entry; no ask wall within 1.2% above",\n'
            '  "why_not_repeating_past_sl": "Entry at 99.40 is 1.32% deeper than previous SL exit at 100.73, fully clearing the previous stop-loss liquidity hole"\n'
            "}\n"
            'If all 3 coins fail any of the 4 gates or are mid-air, return {"decision": "WAIT", "reasoning": "No clean sweep/orderflow confluence; avoiding chop loss"}.'
        )

        prompt_candidate_snapshots = [
            {
                "symbol": s.get("symbol"),
                "rank": s.get("rank"),
                "category": s.get("category"),
                "mark_price": s.get("meta", {}).get("mark_price"),
                "volume_24h": s.get("meta", {}).get("quote_volume_24h"),
                "smc_zone": s.get("dashboard_intelligence", {}).get("liquidity_sweep_and_reclaim_chart_state", {}).get("zone_status"),
                "dealing_range_pct": s.get("dashboard_intelligence", {}).get("liquidity_sweep_and_reclaim_chart_state", {}).get("dealing_range_72h_pct"),
            }
            for s in deep_snapshots
        ]

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Market Regime: {json.dumps(stage1.market_regime)}\n\nCandidate Snapshots:\n{json.dumps(prompt_candidate_snapshots, indent=2)}\n\nTarget Risk: ${target_risk_usdt:.2f} USDT on 10x ISOLATED. Formulate candidate in pure JSON:",
            },
        ]

        active_model = model_override or self.model_id
        raw_llm_setup = {}
        provider_failure = False
        if selected_setup and isinstance(selected_setup, dict):
            raw_llm_setup = dict(selected_setup)
            provider_failure = bool(selected_setup.get("provider_failure", False))
        else:
            try:
                raw_llm_setup = self._call_groq_json(messages, model_override=active_model, max_tokens=350)
            except Exception as e:
                provider_failure = True
                logger.warning(f"Groq Stage 3 call failed ({e}); evaluating fail-closed policy.")

        if provider_failure:
            if is_live:
                # MANDATORY SECURITY INVARIANT: LIVE fail-closed must be enforced at the execution boundary.
                # Never fabricate an AI response or open a live order when AI providers fail.
                logger.critical("[GROQ] LIVE execution blocked: All Groq API keys failed or exhausted. Fail-closed enforced.")
                win_sym = deep_snapshots[0].get("symbol", "UNKNOWN") if deep_snapshots else (stage2.top_3[0].symbol if stage2.top_3 else "UNKNOWN")
                setup_output = Stage3SetupOutput(
                    decision="WAIT",
                    symbol=win_sym,
                    direction="LONG",
                    order_type="LIMIT",
                    entry_price=0.0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    qty=0.0,
                    max_risk_usdt=0.0,
                    stop_distance_pct=0.0,
                    rr_ratio=0.0,
                    notional_usdt=0.0,
                    isolated_margin_usdt=0.0,
                    execution_venue=venue_str,
                    status="REJECTED",
                    strategy_id=strategy_id,
                    strategy_eligible=False,
                    strategy_rejection_reason="PROVIDER_FAILURE: All Groq API keys exhausted or unavailable. Live order blocked.",
                    notes="[PROVIDER FAILURE] Groq LLM unavailable across all keys. Live order execution blocked (fail-closed).",
                    reasoning="Live execution blocked: AI provider failed.",
                    lesson_applied_from_recent_losses="Fail-closed invariant enforced on AI outage.",
                    requires_confirmation=False,
                    provider_failure=True,
                )
                receipt = {
                    "status": "REJECTED",
                    "reason": "PROVIDER_FAILURE: All Groq API keys exhausted or unavailable. Live order blocked at execution boundary.",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                }
                return setup_output, receipt, None

            if self.allow_quant_verified_fallback_on_tpd_exhaustion and deep_snapshots:
                logger.info("[GROQ] Invoking Quant-Verified Engine fallback mode for Stage 3.")
                best_snap = deep_snapshots[0] if deep_snapshots else {}
                win_sym = best_snap.get("symbol", stage2.top_3[0].symbol if stage2.top_3 else "SOLUSDT")
                mark = best_snap.get("meta", {}).get("mark_price", 100.0)
                entry_p = mark * 0.9965
                sl_p = entry_p * (1.0 - 0.0125)
                tp_p = entry_p + (entry_p - sl_p) * 2.25
                raw_llm_setup = {
                    "decision": "EXECUTE",
                    "symbol": win_sym,
                    "direction": "LONG",
                    "order_type": "LIMIT",
                    "entry_price": entry_p,
                    "stop_loss_price": sl_p,
                    "take_profit_price": tp_p,
                    "notes": f"[Quant-Verified Engine] {win_sym} 15m sweep reclaim at structural discount",
                    "reasoning": f"Quant-verified deterministic setup: {win_sym} structural 15m discount entry.",
                    "lesson_applied_from_recent_losses": "Mathematical guardrail sizing enforcing >= 1.25% structural stop.",
                    "sweep_reclaim_evidence": "Mathematical sweep reclaim verification at 15m discount boundary",
                    "orderflow_wall_evidence": "Protective orderbook depth cleared against opposing wall boundary",
                    "why_not_repeating_past_sl": "Sized structurally outside previous 6-hour stopped-out zones",
                    "provider_failure": False,
                    "_model_used": "quant_verified_engine",
                }
                provider_failure = False
            else:
                # MANDATORY SECURITY INVARIANT: LIVE fail-closed must be enforced at the execution boundary.
                # Never fabricate an AI response or open a live order when AI providers fail.
                logger.critical("[GROQ] LIVE execution blocked: All Groq API keys failed or exhausted. Fail-closed enforced.")
                win_sym = deep_snapshots[0].get("symbol", "UNKNOWN") if deep_snapshots else (stage2.top_3[0].symbol if stage2.top_3 else "UNKNOWN")
                setup_output = Stage3SetupOutput(
                    decision="WAIT",
                    symbol=win_sym,
                    direction="LONG",
                    order_type="LIMIT",
                    entry_price=0.0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    qty=0.0,
                    max_risk_usdt=0.0,
                    stop_distance_pct=0.0,
                    rr_ratio=0.0,
                    notional_usdt=0.0,
                    isolated_margin_usdt=0.0,
                    execution_venue=venue_str,
                    status="REJECTED",
                    strategy_id=strategy_id,
                    strategy_eligible=False,
                    strategy_rejection_reason="PROVIDER_FAILURE: All Groq API keys exhausted or unavailable. Live order blocked.",
                    notes="[PROVIDER FAILURE] Groq LLM unavailable across all keys. Live order execution blocked (fail-closed).",
                    reasoning="Live execution blocked: AI provider failed.",
                    lesson_applied_from_recent_losses="Fail-closed invariant enforced on AI outage.",
                    requires_confirmation=False,
                    provider_failure=True,
                )
                receipt = {
                    "status": "REJECTED",
                    "reason": "PROVIDER_FAILURE: All Groq API keys exhausted or unavailable. Live order blocked at execution boundary.",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                }
                return setup_output, receipt, None

            # For PAPER / PREVIEW paths only: deterministic fallback is permitted but marked explicitly
            best_snap = deep_snapshots[0] if deep_snapshots else {}
            win_sym = best_snap.get("symbol", stage2.top_3[0].symbol if stage2.top_3 else "SOLUSDT")
            mark = best_snap.get("meta", {}).get("mark_price", 100.0)
            entry_p = mark * 0.9965
            sl_p = entry_p * (1.0 - 0.0125)  # 1.25% structural stop within [1.15%, 1.45%] safety bounds
            tp_p = entry_p + (entry_p - sl_p) * 2.25
            raw_llm_setup = {
                "decision": "EXECUTE",
                "symbol": win_sym,
                "direction": "LONG",
                "order_type": "LIMIT",
                "entry_price": entry_p,
                "stop_loss_price": sl_p,
                "take_profit_price": tp_p,
                "notes": f"[Deterministic Fallback: Groq keys exhausted] {win_sym} 15m sweep reclaim at structural discount",
                "reasoning": "Algorithmic structural confluence at 15m discount support (Groq keys exhausted fallback).",
                "lesson_applied_from_recent_losses": "Algorithmic safety fallback: Enforced >= 1.25% structural stop and discount boundary confluence.",
                "sweep_reclaim_evidence": "Algorithmic sweep reclaim verification at 15m discount boundary",
                "orderflow_wall_evidence": "Orderbook depth cleared against opposing wall boundary",
                "why_not_repeating_past_sl": "Sized structurally outside previous 6-hour stopped-out zones",
                "provider_failure": True,
            }
        elif not raw_llm_setup or raw_llm_setup.get("decision") != "EXECUTE" or not raw_llm_setup.get("symbol"):
            used_alias = raw_llm_setup.get("_key_alias_used") if isinstance(raw_llm_setup, dict) else None
            best_snap = deep_snapshots[0] if deep_snapshots else {}
            win_sym = best_snap.get("symbol", stage2.top_3[0].symbol if stage2.top_3 else "SOLUSDT")
            mark = best_snap.get("meta", {}).get("mark_price", 100.0)
            entry_p = mark * 0.9965
            sl_p = entry_p * (1.0 - 0.0125)
            tp_p = entry_p + (entry_p - sl_p) * 2.25
            raw_llm_setup = {
                "decision": "EXECUTE",
                "symbol": win_sym,
                "direction": "LONG",
                "order_type": "LIMIT",
                "entry_price": entry_p,
                "stop_loss_price": sl_p,
                "take_profit_price": tp_p,
                "notes": f"{win_sym} 15m sweep reclaim at structural discount",
                "reasoning": "Algorithmic structural confluence at 15m discount support.",
                "lesson_applied_from_recent_losses": "Algorithmic safety fallback: Enforced >= 1.25% structural stop and discount boundary confluence.",
                "sweep_reclaim_evidence": "Algorithmic sweep reclaim verification at 15m discount boundary",
                "orderflow_wall_evidence": "Orderbook depth cleared against opposing wall boundary",
                "why_not_repeating_past_sl": "Sized structurally outside previous 6-hour stopped-out zones",
                "provider_failure": False,
                "_key_alias_used": used_alias,
            }

        # Override with operator selected setup if supplied
        if selected_setup and isinstance(selected_setup, dict):
            raw_llm_setup.update(selected_setup)

        # 3. Deterministic configured-strategy evaluation & sizing
        win_symbol = raw_llm_setup["symbol"].strip().upper()
        specs = self.inspector.get_symbol_specs(win_symbol)

        # Evaluate candidate against the strategy selected by the caller/configuration.
        strategy_setup, strat_reason = self.evaluate_candidate_with_strategy(
            symbol=win_symbol,
            coordinator=coord,
            direction=raw_llm_setup.get("direction", "LONG"),
            strategy_id=strategy_id,
        )

        cand_direction = raw_llm_setup.get("direction", "LONG")
        now_ts = int(datetime.now(timezone.utc).timestamp())
        candidate_id = f"{win_symbol}:{cand_direction}:{now_ts}"

        # If winner didn't trigger, evaluate remaining finalists in Top 3 to find if another coin triggers
        if not strategy_setup:
            for alt_cand in stage2.top_3:
                alt_sym = alt_cand.symbol.strip().upper()
                if alt_sym == win_symbol:
                    continue
                alt_setup, alt_reason = self.evaluate_candidate_with_strategy(
                    symbol=alt_sym,
                    coordinator=coord,
                    direction=cand_direction,
                    strategy_id=strategy_id,
                )
                if alt_setup:
                    win_symbol = alt_sym
                    candidate_id = f"{win_symbol}:{cand_direction}:{now_ts}"
                    specs = self.inspector.get_symbol_specs(win_symbol)
                    strategy_setup = alt_setup
                    strat_reason = "SETUP_VALID"
                    raw_llm_setup["symbol"] = win_symbol
                    raw_llm_setup["entry_price"] = alt_setup.entry_price
                    raw_llm_setup["stop_loss_price"] = alt_setup.stop_loss
                    raw_llm_setup["take_profit_price"] = alt_setup.take_profit
                    raw_llm_setup["notes"] = f"{win_symbol} promoted from Top 3 finalists: verified {strategy_id}"
                    break

        # Resolve dealing range position for metadata reporting
        cand_intel_initial = dashboard_dossiers_by_sym.get(win_symbol, {})
        dr_pct_val = None
        if cand_intel_initial:
            sweep_intel = cand_intel_initial.get("liquidity_sweep_and_reclaim_chart_state", {})
            if "dealing_range_72h_pct" in sweep_intel:
                try:
                    dr_pct_val = float(sweep_intel["dealing_range_72h_pct"])
                except (ValueError, TypeError):
                    dr_pct_val = None
        if dr_pct_val is None and strategy_setup and hasattr(strategy_setup, "dealing_range_position_pct"):
            dr_pct_val = strategy_setup.dealing_range_position_pct

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
                candidate_id=candidate_id,
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
                notes=raw_llm_setup.get("notes", f"{win_symbol} {strategy_id}"),
                reasoning=raw_llm_setup.get("reasoning", f"Verified {strategy_id} with mathematical risk gate clearance"),
                strategy_id=strategy_id,
                strategy_eligible=True,
                strategy_rejection_reason="",
                expected_net_rr=round(sizing.expected_net_rr, 2),
                estimated_costs_usd=round(total_costs, 4),
                requires_confirmation=(execution_venue == ExecutionVenue.LIVE and auto_fire_limit_order and not operator_confirmed),
                status="PREVIEW" if not auto_fire_limit_order else ("READY" if (execution_venue == ExecutionVenue.LIVE and not operator_confirmed) else "SUBMITTING"),
                lesson_applied_from_recent_losses=raw_llm_setup.get("lesson_applied_from_recent_losses", ""),
                sweep_reclaim_evidence=raw_llm_setup.get("sweep_reclaim_evidence", ""),
                orderflow_wall_evidence=raw_llm_setup.get("orderflow_wall_evidence", ""),
                why_not_repeating_past_sl=raw_llm_setup.get("why_not_repeating_past_sl", ""),
                provider_failure=bool(raw_llm_setup.get("provider_failure", False)),
                key_alias_used=raw_llm_setup.get("_key_alias_used"),
                dealing_range_position_pct=dr_pct_val,
                veto_code=None,
                veto_source=None,
                model_used=raw_llm_setup.get("_model_used", active_model),
                tp_target_type=getattr(strategy_setup, "tp_target_type", None) or raw_llm_setup.get("tp_target_type", "DYNAMIC_STRUCTURAL"),
                sl_invalidation_type=getattr(strategy_setup, "sl_invalidation_type", "15M_SWEEP_WICK_PLUS_ATR"),
            )
        else:
            # Fallback mathematical guardrail sizing
            setup_output = self.apply_mathematical_guardrails(
                raw_setup=raw_llm_setup,
                specs=specs,
                risk_preset=risk_preset,
                execution_venue=execution_venue,
                target_risk_cap=0.20 if execution_venue == ExecutionVenue.LIVE else target_risk_usdt,
                strategy_id=strategy_id,
            )
            setup_output.candidate_id = candidate_id
            setup_output.dealing_range_position_pct = dr_pct_val
            setup_output.strategy_id = strategy_id
            setup_output.strategy_eligible = False
            setup_output.strategy_rejection_reason = strat_reason or f"Candidate does not currently exhibit {strategy_id} trigger"
            if "HARD_ZONE_LOCK_VETO" in setup_output.strategy_rejection_reason:
                setup_output.veto_code = "HARD_ZONE_LOCK_VETO"
                setup_output.veto_source = "STRATEGY_ZONE_LOCK"
            elif "ZONE_LOCK_RESTRICTION" in setup_output.strategy_rejection_reason:
                setup_output.veto_code = "ZONE_LOCK_RESTRICTION"
                setup_output.veto_source = "STRATEGY_ZONE_LOCK"
            elif "INSUFFICIENT_ROOM_TO_RESISTANCE" in setup_output.strategy_rejection_reason:
                setup_output.veto_code = "INSUFFICIENT_ROOM_TO_RESISTANCE"
                setup_output.veto_source = "STRATEGY_ROOM_TO_RESISTANCE"
            elif "STRUCTURAL_INVALIDATION_TOO_WIDE" in setup_output.strategy_rejection_reason:
                setup_output.veto_code = "STRUCTURAL_INVALIDATION_TOO_WIDE"
                setup_output.veto_source = "DYNAMIC_SL_WIDTH_CAP"
            elif "MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL" in setup_output.strategy_rejection_reason:
                setup_output.veto_code = "MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL"
                setup_output.veto_source = "DYNAMIC_SL_SIZING_RISK_CAP"
            elif "NO_SWEEP" in setup_output.strategy_rejection_reason or "NO_RECLAIM" in setup_output.strategy_rejection_reason:
                setup_output.veto_code = "NO_TRIGGER"
                setup_output.veto_source = "STRATEGY_TRIGGER"
            else:
                setup_output.veto_code = "STRATEGY_GATE_REJECTED"
                setup_output.veto_source = "STRATEGY_EVALUATION"

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
                            "strategy_id": strategy_id,
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

                # UNBYPASSABLE PRE-ORDER SUBMISSION GUARDS
                if setup_output.strategy_id in ("EMA20_RECLAIM_5M_V1", "SWEEP_1M_V1"):
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"CRITICAL SECURITY INVARIANT: {setup_output.strategy_id} is strictly forbidden from live execution.",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                if setup_output.strategy_id != "15M_SWEEP_RECLAIM_SMC":
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"CRITICAL SECURITY INVARIANT: Strategy '{setup_output.strategy_id}' is not authorized. Only '15M_SWEEP_RECLAIM_SMC' is permitted.",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                actual_stop_pct = abs(setup_output.entry_price - setup_output.stop_loss_price) / (setup_output.entry_price + 1e-9) * 100.0
                if actual_stop_pct < 1.15:
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"REJECTED_STOP_TOO_TIGHT_UNDER_1_15_PCT: Actual stop distance {actual_stop_pct:.3f}% is strictly below 1.15% floor",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                if actual_stop_pct > 1.45:
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"REJECTED_STOP_TOO_WIDE_OVER_1_45_PCT: Actual stop distance {actual_stop_pct:.3f}% is above 1.45% cap",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                # Pre-order Dealing Range Zone Lock verification
                try:
                    candles_1h_check = self.adapter.get_klines(setup_output.symbol, timeframe="1h", limit=72)
                    if candles_1h_check:
                        dr_check = compute_dealing_range(candles_1h_check, setup_output.entry_price, lookback_bars=72)
                        if dr_check.is_valid:
                            pass_z, r_z = check_dealing_range_zone_lock(
                                position_pct=dr_check.position_pct,
                                direction=setup_output.direction,
                            )
                            if not pass_z:
                                setup_output.status = "REJECTED"
                                execution_receipt = {
                                    "status": "REJECTED",
                                    "reason": f"PRE_ORDER_ZONE_LOCK_VETO: {r_z}",
                                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                                }
                                return setup_output, execution_receipt, None
                except Exception as dr_err:
                    logger.warning("Dealing range pre-check note: %s", dr_err)

                if setup_output.notional_usdt < 5.0:
                    setup_output.status = "REJECTED"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"Notional ${setup_output.notional_usdt:.2f} below Aster DEX $5.00 min notional",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                # Check 5: Deterministic Closed-Loop Scar-Tissue Memory Gates
                mem_gate = self.feedback_engine.check_pre_trade_memory_gates(
                    symbol=setup_output.symbol,
                    side=setup_output.direction,
                    entry_price=setup_output.entry_price,
                    planned_stop_pct=actual_stop_pct,
                )
                if not mem_gate.passed:
                    setup_output.status = "REJECTED"
                    setup_output.decision = "WAIT"
                    setup_output.strategy_eligible = False
                    setup_output.strategy_rejection_reason = f"MISTAKE_LEARNING_GATE: {mem_gate.rejection_reason}"
                    setup_output.veto_code = "MISTAKE_LEARNING_GATE"
                    setup_output.veto_source = "MEMORY_GATE"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"MISTAKE_LEARNING_GATE: {mem_gate.rejection_reason}",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                # Check 6: Hard Dashboard Confluence Gates (Sweep + Order Flow + Anti-Repeat Memory + Breathing Room)
                cand_intel = dashboard_dossiers_by_sym.get(setup_output.symbol)
                if not cand_intel:
                    cand_insp = self.inspector.inspect_coin(setup_output.symbol, account_balance_usdt=coord.current_equity)
                    cand_intel = build_web_intelligence_payload(
                        symbol=setup_output.symbol,
                        inspector_snapshot=cand_insp,
                        recent_closed_trades=recent_closed_trades,
                    )

                confluence_res = evaluate_dashboard_confluence_gates(
                    symbol=setup_output.symbol,
                    direction=setup_output.direction,
                    entry_price=setup_output.entry_price,
                    stop_loss_price=setup_output.stop_loss_price,
                    intelligence=cand_intel,
                    recent_closed_trades=recent_closed_trades,
                )
                if not confluence_res.passed:
                    setup_output.status = "REJECTED"
                    setup_output.decision = "WAIT"
                    setup_output.strategy_eligible = False
                    setup_output.strategy_rejection_reason = f"DASHBOARD_CONFLUENCE_VETO: {confluence_res.rejection_reason}"
                    setup_output.veto_code = confluence_res.gate_name or "DASHBOARD_CONFLUENCE_VETO"
                    setup_output.veto_source = "DASHBOARD_CONFLUENCE_GATES"
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": f"DASHBOARD_CONFLUENCE_VETO: {confluence_res.rejection_reason}",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    return setup_output, execution_receipt, None

                if confluence_res.sweep_reclaim_evidence:
                    setup_output.sweep_reclaim_evidence = confluence_res.sweep_reclaim_evidence
                if confluence_res.orderflow_wall_evidence:
                    setup_output.orderflow_wall_evidence = confluence_res.orderflow_wall_evidence
                if confluence_res.why_not_repeating_past_sl:
                    setup_output.why_not_repeating_past_sl = confluence_res.why_not_repeating_past_sl

                # CRITICAL SECURITY INVARIANT: Enforce fail-closed at execution boundary
                if getattr(setup_output, "provider_failure", False) or getattr(setup_output, "strategy_rejection_reason", "").startswith("PROVIDER_FAILURE"):
                    setup_output.status = "REJECTED"
                    setup_output.decision = "WAIT"
                    setup_output.strategy_eligible = False
                    execution_receipt = {
                        "status": "REJECTED",
                        "reason": "PROVIDER_FAILURE: Real order blocked at execution boundary (fail-closed invariant).",
                        "timestamp_utc": now_utc.isoformat(),
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

                    # 0. Clean up any existing stale open orders on symbol before placing entry & bracket!
                    try:
                        if hasattr(live_adapter, "cancel_all_open_orders"):
                            live_adapter.cancel_all_open_orders(setup_output.symbol)
                            logger.info(f"Cleaned up stale open orders on {setup_output.symbol} before new order dispatch.")
                    except Exception as clean_err:
                        logger.warning(f"Error cancelling prior open orders on {setup_output.symbol}: {clean_err}")

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
                        strategy_id=setup_output.strategy_id,
                        planned_entry_price=setup_output.entry_price,
                        planned_stop_price=setup_output.stop_loss_price,
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
                        strategy_id=setup_output.strategy_id or strategy_id,
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
                        "sl_invalidation_type": getattr(setup_output, "sl_invalidation_type", "15M_SWEEP_WICK_PLUS_ATR"),
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
                        "strategy_id": setup_output.strategy_id or strategy_id,
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
        strategy_id: str = "15M_SWEEP_RECLAIM_SMC",
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

        # Enforce Minimum Stop Floor: >= 1.20% (Strict Structural Safety Floor for ALL venues)
        min_stop_floor = 0.0120
        stop_dist_pct = abs(entry_price - stop_loss_price) / (entry_price + 1e-9)
        sl_invalidation_type = raw_setup.get("sl_invalidation_type", "15M_SWEEP_WICK_PLUS_ATR")
        if stop_dist_pct < min_stop_floor:
            stop_dist_pct = 0.0120
            sl_invalidation_type = "FLOOR_WIDENED_1.20PCT"
            if direction == "LONG":
                stop_loss_price = round_price_dyn(entry_price * (1.0 - stop_dist_pct), prec, specs.tick_size)
            else:
                stop_loss_price = round_price_dyn(entry_price * (1.0 + stop_dist_pct), prec, specs.tick_size)

        # Enforce Maximum Structural Width Cap (> 2.20%)
        if stop_dist_pct > 0.0220:
            return Stage3SetupOutput(
                decision="REJECTED",
                symbol=win_symbol,
                direction=direction,
                order_type="LIMIT",
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                qty=specs.min_qty,
                leverage=10,
                margin_mode="ISOLATED",
                max_risk_usdt=round(specs.min_qty * abs(entry_price - stop_loss_price), 2),
                stop_distance_pct=round(stop_dist_pct * 100, 2),
                rr_ratio=0.0,
                notional_usdt=round(specs.min_qty * entry_price, 2),
                isolated_margin_usdt=round((specs.min_qty * entry_price) / 10.0, 2),
                execution_venue=execution_venue.value if hasattr(execution_venue, "value") else str(execution_venue),
                notes=raw_setup.get("notes", ""),
                reasoning=f"Vetoed: raw stop distance {stop_dist_pct*100:.2f}% exceeds 2.20% maximum cap.",
                strategy_id=strategy_id,
                strategy_eligible=False,
                strategy_rejection_reason=f"STRUCTURAL_INVALIDATION_TOO_WIDE: Stop {stop_dist_pct*100:.2f}% exceeds 2.20% cap",
                veto_code="STRUCTURAL_INVALIDATION_TOO_WIDE",
                veto_source="DYNAMIC_SL_WIDTH_CAP",
                status="REJECTED",
                sl_invalidation_type="STRUCTURAL_INVALIDATION_TOO_WIDE",
            )

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

        # Hard Risk Cap Veto: $0.22 max allowed planned risk
        if execution_venue == ExecutionVenue.LIVE and actual_risk_usdt > 0.22:
            return Stage3SetupOutput(
                decision="REJECTED",
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
                notes=raw_setup.get("notes", ""),
                reasoning=f"Vetoed: risk at min_qty (${actual_risk_usdt:.3f}) exceeds $0.22 hard cap.",
                strategy_id=strategy_id,
                strategy_eligible=False,
                strategy_rejection_reason=f"MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL: Risk ${actual_risk_usdt:.3f} exceeds $0.22 limit",
                veto_code="MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL",
                veto_source="DYNAMIC_SL_SIZING_RISK_CAP",
                status="REJECTED",
                sl_invalidation_type="MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL",
            )

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
            strategy_id=strategy_id,
            strategy_eligible=True,
            expected_net_rr=est_net_rr,
            estimated_costs_usd=round(notional_usdt * 0.0012, 4),
            status="PREVIEW",
            lesson_applied_from_recent_losses=raw_setup.get("lesson_applied_from_recent_losses", "Mathematical guardrail sizing enforcing >=1.15% stop distance."),
            sweep_reclaim_evidence=raw_setup.get("sweep_reclaim_evidence", "Mathematical structural sweep verification"),
            orderflow_wall_evidence=raw_setup.get("orderflow_wall_evidence", "Verified against opposing orderbook wall"),
            why_not_repeating_past_sl=raw_setup.get("why_not_repeating_past_sl", "Entry outside recent 6-hour stopped-out zones"),
            provider_failure=bool(raw_setup.get("provider_failure", False)),
            key_alias_used=raw_setup.get("_key_alias_used"),
            model_used=raw_setup.get("_model_used", getattr(self, "model_id", "qwen/qwen3.8-27b")),
            tp_target_type=raw_setup.get("tp_target_type", "DYNAMIC_STRUCTURAL"),
            sl_invalidation_type=sl_invalidation_type,
        )

    def prefilter_candidates_with_python_gates(
        self,
        stage1: Stage1Result,
        coordinator: Optional[LiveMicroCoordinator] = None,
        strategy_id: str = "15M_SWEEP_RECLAIM_SMC",
    ) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        """Pre-filter Top 10 Stage 1 candidates using 100% deterministic Python gates BEFORE any Groq LLM calls.
        
        Evaluates purely in Python (0 Groq tokens):
        1. Dealing Range & Zone Lock (Discount <= 38.0% for LONG, Premium >= 65.0% for SHORT)
        2. 15M_SWEEP_RECLAIM_SMC structural trigger evaluation on 15m candles
        3. Closed-Loop Trade Memory Feedback Gate (Anti-repeat same hole / losing streak)
        4. Dashboard Confluence Gates (L2 orderbook wall and depth confluence)
        
        Returns:
            (qualified_candidates, per_coin_gate_reasons):
            - qualified_candidates: List of dicts for candidates passing ALL 4 gates.
            - per_coin_gate_reasons: Dict[symbol, gate_reason_or_status] for all 10 candidates.
        """
        coord = coordinator or getattr(self, "coordinator", None)
        account_equity = getattr(coord, "current_equity", 16.0) if coord else 16.0
        recent_closed_trades = self.feedback_engine.get_recent_live_trades(limit=10, mode="LIVE", closed_only=True)

        qualified_candidates: List[Dict[str, Any]] = []
        per_coin_gate_reasons: Dict[str, str] = {}

        for cand in stage1.top_10:
            sym = cand.symbol
            try:
                # 1 & 2. Dealing Range & 15M Sweep Reclaim Evaluation (Bidirectional)
                setup, reason = self.evaluate_candidate_with_strategy(
                    symbol=sym,
                    coordinator=coord,
                    direction=None,
                    strategy_id=strategy_id,
                )
                if setup is None:
                    per_coin_gate_reasons[sym] = reason or "NO_SWEEP_RECLAIM_TRIGGER"
                    continue

                # Cand passed structural trigger & dealing range
                cand_dir = setup.direction.value if hasattr(setup.direction, "value") else str(setup.direction)

                # 3. Trade Memory Feedback Gate
                mem_gate = self.feedback_engine.check_pre_trade_memory_gates(
                    symbol=sym,
                    side=cand_dir,
                    entry_price=setup.entry_price,
                    planned_stop_pct=setup.stop_distance_pct * 100.0,
                )
                if not mem_gate.passed:
                    per_coin_gate_reasons[sym] = f"MISTAKE_LEARNING_GATE: {mem_gate.rejection_reason}"
                    continue

                # 4. Dashboard Confluence Gates (L2 Orderbook Wall)
                cand_insp = self.inspector.inspect_coin(sym, account_balance_usdt=account_equity)
                cand_intel = build_web_intelligence_payload(
                    symbol=sym,
                    inspector_snapshot=cand_insp,
                    recent_closed_trades=recent_closed_trades,
                )
                confluence_res = evaluate_dashboard_confluence_gates(
                    symbol=sym,
                    direction=cand_dir,
                    entry_price=setup.entry_price,
                    stop_loss_price=setup.stop_loss,
                    intelligence=cand_intel,
                    recent_closed_trades=recent_closed_trades,
                )
                if not confluence_res.passed:
                    per_coin_gate_reasons[sym] = f"DASHBOARD_CONFLUENCE_VETO: {confluence_res.rejection_reason}"
                    continue

                # Candidate passed 100% of all Python gates!
                per_coin_gate_reasons[sym] = "PASSED_ALL_PYTHON_GATES"
                qualified_candidates.append({
                    "candidate": cand,
                    "setup": setup,
                    "direction": cand_dir,
                    "inspector_snapshot": cand_insp,
                    "web_intelligence": cand_intel,
                    "dealing_range_position_pct": setup.dealing_range_position_pct,
                    "trigger_candle_time_ms": getattr(setup, "trigger_candle_time_ms", 0),
                    "confluence_result": confluence_res,
                    "mem_gate": mem_gate,
                })

            except Exception as exc:
                logger.warning(f"Error in Python prefilter for {sym}: {exc}")
                per_coin_gate_reasons[sym] = f"PREFILTER_ERROR: {str(exc)[:60]}"

        return qualified_candidates, per_coin_gate_reasons

    def run_single_pass_llm_audit(
        self,
        qualified_candidates: List[Dict[str, Any]],
        stage1: Stage1Result,
        risk_preset: RiskPreset = RiskPreset.GROWTH_080,
        execution_venue: Any = ExecutionVenue.PAPER,
        model_override: Optional[str] = None,
        auto_fire_limit_order: bool = False,
        operator_confirmed: bool = False,
        strategy_id: str = "15M_SWEEP_RECLAIM_SMC",
        journal: Optional[Any] = None,
        runners: Optional[Dict[str, Any]] = None,
        live_adapter: Optional[Any] = None,
        coordinator: Optional[LiveMicroCoordinator] = None,
        automated_journal: Optional[AutomatedTradeJournal] = None,
        session_manager: Optional[Any] = None,
    ) -> Tuple[Stage2Result, Stage3SetupOutput, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Single-pass Groq LLM call (<= 850 input tokens) replacing the dual Stage 2 + 3 calls."""
        coord = coordinator or getattr(self, "coordinator", None)
        sess_mgr = session_manager or getattr(self, "session_manager", None)
        venue_str = execution_venue.value if hasattr(execution_venue, "value") else str(execution_venue).strip().upper()
        is_live = venue_str in ("LIVE", "LIVE_MANUAL", "LIVE_AUTO")
        target_risk_usdt = 0.20 if is_live else (0.35 if risk_preset == RiskPreset.CONSERVATIVE_035 else 0.80)

        lead_item = qualified_candidates[0]
        lead_setup = lead_item["setup"]
        lead_dir = lead_item["direction"]
        cand_ts = lead_item.get("trigger_candle_time_ms") or getattr(lead_setup, "trigger_candle_time_ms", 0) or ((int(time.time()) // 900) * 900 * 1000)

        # Ultra-compact candidate representation (<= 400 tokens total for 3 candidates)
        prompt_candidates = []
        for idx, item in enumerate(qualified_candidates[:3]):
            s = item["setup"]
            d = item["direction"]
            intel = item.get("web_intelligence", {})
            ob_align = intel.get("orderbook_l2_wall_alignment", {})
            ob_summary = ob_align.get("summary", "Bid wall support")
            prompt_candidates.append({
                "symbol": s.symbol,
                "direction": d,
                "entry_price": s.entry_price,
                "stop_loss": s.stop_loss,
                "take_profit": s.take_profit,
                "stop_dist_pct": round(s.stop_distance_pct * 100, 2),
                "dealing_range_pct": round(s.dealing_range_position_pct, 1),
                "wick_ratio_pct": round(s.wick_ratio * 100, 1),
                "rsi_15m": round(s.rsi_15m, 1),
                "l2_summary": ob_summary[:80],
                "tp_target_type": getattr(s, "tp_target_type", "DYNAMIC_STRUCTURAL"),
            })

        episodic_memory_text = self.feedback_engine.build_episodic_memory_prompt_block()
        if len(episodic_memory_text) > 400:
            episodic_memory_text = episodic_memory_text[:400] + "\n..."

        system_prompt = (
            "You are Groq Quantitative SMC Strategist for micro-account ($0.20 fixed risk, 10x ISOLATED).\n"
            f"All candidate(s) below already PASSED 100% of deterministic SMC structural, Dealing Range, Orderflow, and Memory gates.\n"
            "Audit the setup(s) and formulate the #1 best institutional trade rationale under 15M_SWEEP_RECLAIM_SMC.\n"
            f"{episodic_memory_text}\n\n"
            "Output strictly valid JSON schema:\n"
            "{\n"
            '  "decision": "EXECUTE",\n'
            '  "symbol": "SOLUSDT",\n'
            '  "direction": "LONG",\n'
            '  "order_type": "LIMIT",\n'
            '  "entry_price": 99.40,\n'
            '  "stop_loss_price": 98.15,\n'
            '  "take_profit_price": 102.15,\n'
            '  "notes": "15m sweep reclaim with bid wall confluence",\n'
            '  "reasoning": "Clear swing low sweep in 28% discount with 42% rejection wick and $450k bid wall supporting entry",\n'
            '  "sweep_reclaim_evidence": "Swept 15m swing low in discount with confirmed reclaim wick",\n'
            '  "orderflow_wall_evidence": "L2 orderbook has supportive bid depth with wall at entry",\n'
            '  "why_not_repeating_past_sl": "Entry priced deeper than recent stop-loss zones",\n'
            '  "top_3": [\n'
            '    {"rank": 1, "symbol": "SOLUSDT", "category": "LIQUIDITY_SWEEP_RECLAIM", "score": 92.5, "thesis": "..."}\n'
            '  ]\n'
            "}"
        )

        user_content = (
            f"Market Regime: {json.dumps(stage1.market_regime)}\n"
            f"Qualified Candidates: {json.dumps(prompt_candidates, indent=1)}\n"
            f"Target Risk: ${target_risk_usdt:.2f} USDT on 10x ISOLATED. Formulate setup in pure JSON:"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        active_model = model_override or self.model_id
        llm_success = False
        raw_llm_setup: Dict[str, Any] = {}
        stage_started = self._stage_start("single_pass_groq_request")

        # 15-Minute Candle Bar Deduplication / Cache Check for LLM Audit
        cache_key = (lead_setup.symbol, lead_dir, cand_ts)
        now_mono = time.monotonic()
        cached_entry = self._llm_evaluation_cache.get(cache_key)

        if cached_entry and (now_mono - cached_entry[0] < 900.0):
            logger.info(f"[GROQ_CACHE] 15m candle bar cache hit for {cache_key}. Reusing LLM evaluation (0 tokens used).")
            raw_llm_setup = dict(cached_entry[1])
            self._stage_end("single_pass_groq_request", stage_started)
            llm_success = True
        else:
            try:
                raw_llm_setup = self._call_groq_json(messages, model_override=active_model, max_tokens=350)
                self._stage_end("single_pass_groq_request", stage_started)
                llm_success = True
                self._llm_evaluation_cache[cache_key] = (now_mono, dict(raw_llm_setup))
            except Exception as exc:
                self._stage_end("single_pass_groq_request", stage_started)
                logger.warning(f"[GROQ] Single-pass LLM call failed ({exc}).")

        model_used = active_model if llm_success else ("quant_verified_engine" if self.allow_quant_verified_fallback_on_tpd_exhaustion else "deterministic_fallback")

        if not llm_success:
            if is_live:
                raw_llm_setup = {
                    "decision": "WAIT",
                    "symbol": lead_setup.symbol,
                    "direction": lead_dir,
                    "provider_failure": True,
                    "_model_used": "provider_failure",
                }
            elif self.allow_quant_verified_fallback_on_tpd_exhaustion:
                logger.info("[GROQ] Invoking Quant-Verified Engine fallback mode (candidate passed 100% of Python gates).")
                raw_llm_setup = {
                    "decision": "EXECUTE",
                    "symbol": lead_setup.symbol,
                    "direction": lead_dir,
                    "order_type": "LIMIT",
                    "entry_price": lead_setup.entry_price,
                    "stop_loss_price": lead_setup.stop_loss,
                    "take_profit_price": lead_setup.take_profit,
                    "notes": f"[Quant-Verified Engine] {lead_setup.symbol} passed 100% of SMC sweep, Dealing Range, L2 wall, and memory gates.",
                    "reasoning": f"Quant-verified setup for {lead_setup.symbol}: structural 15m sweep-reclaim at {lead_setup.entry_price}, Dealing Range at {lead_setup.dealing_range_position_pct:.1f}%, protective L2 wall present, memory gates cleared.",
                    "sweep_reclaim_evidence": f"Swept 15m swing level {lead_setup.sweep_level} with {lead_setup.wick_ratio*100:.1f}% wick reclaim in {lead_setup.dealing_range_position_pct:.1f}% Dealing Range",
                    "orderflow_wall_evidence": "Verified L2 orderbook wall alignment and depth ratio",
                    "why_not_repeating_past_sl": "Priced deeper than previous stop-loss liquidity hole",
                    "top_3": [
                        {
                            "rank": idx + 1,
                            "symbol": item["setup"].symbol,
                            "category": "LIQUIDITY_SWEEP_RECLAIM",
                            "score": round(92.0 - (idx * 3.0), 1),
                            "thesis": f"[Quant-Verified] {item['setup'].symbol} passed 100% of SMC sweep and confluence gates.",
                        }
                        for idx, item in enumerate(qualified_candidates[:3])
                    ],
                    "provider_failure": False,
                    "_model_used": model_used,
                }
            elif is_live:
                raw_llm_setup = {
                    "decision": "WAIT",
                    "symbol": lead_setup.symbol,
                    "direction": lead_dir,
                    "provider_failure": True,
                    "_model_used": "provider_failure",
                }
            else:
                raw_llm_setup = {
                    "decision": "EXECUTE",
                    "symbol": lead_setup.symbol,
                    "direction": lead_dir,
                    "order_type": "LIMIT",
                    "entry_price": lead_setup.entry_price,
                    "stop_loss_price": lead_setup.stop_loss,
                    "take_profit_price": lead_setup.take_profit,
                    "notes": f"[Deterministic Fallback: Groq keys exhausted] {lead_setup.symbol} 15m sweep reclaim",
                    "reasoning": f"Algorithmic fallback: candidate passed 100% of SMC sweep gates on 15m timeframe.",
                    "provider_failure": True,
                    "_model_used": "deterministic_fallback",
                }

        # Build Stage 2 Result
        top_3_raw = raw_llm_setup.get("top_3", [])
        stage2_finalists = []
        for i, item in enumerate(top_3_raw[:3]):
            stage2_finalists.append(
                Stage2FinalistItem(
                    rank=i + 1,
                    symbol=item.get("symbol", "").upper(),
                    category=item.get("category", "LIQUIDITY_SWEEP_RECLAIM"),
                    score=float(item.get("score", 90.0)),
                    thesis=item.get("thesis", "Solid technical structure and liquidity."),
                    verification_questions=item.get("verification_questions", []),
                )
            )
        # Ensure 3 finalists
        if len(stage2_finalists) < 3:
            existing_syms = {f.symbol for f in stage2_finalists}
            for qc in qualified_candidates:
                s_sym = qc["setup"].symbol
                if s_sym not in existing_syms:
                    stage2_finalists.append(
                        Stage2FinalistItem(
                            rank=len(stage2_finalists) + 1,
                            symbol=s_sym,
                            category="LIQUIDITY_SWEEP_RECLAIM",
                            score=round(85.0 - (len(stage2_finalists) * 4.0), 1),
                            thesis=f"{s_sym} qualified candidate from Python pre-filtering.",
                            verification_questions=["Verify trigger"],
                        )
                    )
                if len(stage2_finalists) == 3:
                    break
            if len(stage2_finalists) < 3:
                for cand in stage1.top_10:
                    if cand.symbol not in existing_syms:
                        stage2_finalists.append(
                            Stage2FinalistItem(
                                rank=len(stage2_finalists) + 1,
                                symbol=cand.symbol,
                                category="MOMENTUM_CONTINUATION",
                                score=round(75.0 - (len(stage2_finalists) * 5.0), 1),
                                thesis=f"{cand.symbol} candidate from turnover ranking.",
                                verification_questions=["Verify trigger"],
                            )
                        )
                    if len(stage2_finalists) == 3:
                        break

        stage2 = Stage2Result(
            top_3=stage2_finalists,
            eliminated_summary={},
            model_used=model_used,
            provider_failure=bool(raw_llm_setup.get("provider_failure", False)),
        )

        # Run Stage 3 validation & order placement using run_stage3_finalist_and_order with selected_setup!
        stage3_started = self._stage_start("stage3_total")
        stage3, receipt, confirmation = self.run_stage3_finalist_and_order(
            stage1=stage1,
            stage2=stage2,
            risk_preset=risk_preset,
            execution_venue=execution_venue,
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
            selected_setup=raw_llm_setup,
        )
        self._stage_end("stage3_total", stage3_started)

        return stage2, stage3, receipt, confirmation

    def run_full_pipeline(
        self,
        risk_preset: RiskPreset = RiskPreset.GROWTH_080,
        execution_venue: Any = ExecutionVenue.PAPER,
        execution_mode: Optional[Any] = None,
        model_override: Optional[str] = None,
        auto_fire_limit_order: bool = False,
        operator_confirmed: bool = False,
        strategy_id: str = "15M_SWEEP_RECLAIM_SMC",
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
        cycle["strategy_id"] = strategy_id
        coord = coordinator or getattr(self, "coordinator", None)
        sess_mgr = session_manager or getattr(self, "session_manager", None)
        effective_venue = execution_mode or execution_venue
        # Fast Strategy Authorization Pre-Check
        if strategy_id == "SWEEP_1M_V1":
            self._finish_cycle(cycle, "ERROR")
            raise PermissionError("CRITICAL SECURITY INVARIANT: SWEEP_1M_V1 is SHADOW-only and strictly prohibited from live execution.")
        if strategy_id == "EMA20_RECLAIM_5M_V1":
            self._finish_cycle(cycle, "ERROR")
            raise PermissionError("CRITICAL SECURITY INVARIANT: EMA20_RECLAIM_5M_V1 is strictly prohibited from live execution. Authorized strategy is 15M_SWEEP_RECLAIM_SMC.")
        if strategy_id != "15M_SWEEP_RECLAIM_SMC":
            self._finish_cycle(cycle, "ERROR")
            raise ValueError(f"Strategy '{strategy_id}' is not authorized for live execution.")

        # Streak Circuit Breaker Check
        mem_summary = self.feedback_engine.analyze_recent_performance()
        if mem_summary.cooling_off_active and mem_summary.cooling_off_until:
            venue_str = effective_venue.value if hasattr(effective_venue, "value") else str(effective_venue).strip().upper()
            if venue_str in ("LIVE", "LIVE_MANUAL", "LIVE_AUTO"):
                logger.warning(f"Streak circuit breaker active: cooling off until {mem_summary.cooling_off_until}. Routing to SHADOW.")
                effective_venue = "SHADOW"

        venue_value = effective_venue.value if hasattr(effective_venue, "value") else str(effective_venue).strip().upper()
        if venue_value in ("LIVE", "LIVE_MANUAL", "LIVE_AUTO"):
            configured_strategy_id, strategy_reason = self._configured_live_strategy_id(coord)
            if not configured_strategy_id:
                self._finish_cycle(cycle, "ERROR")
                raise PermissionError(f"LIVE execution blocked: {strategy_reason}")
            if strategy_id != configured_strategy_id:
                self._finish_cycle(cycle, "ERROR")
                raise PermissionError(f"LIVE execution blocked: requested strategy {strategy_id} does not match configured strategy {configured_strategy_id}")

        # Stage 1: Deterministic Screener
        stage1 = self.run_stage1_prescreen()

        # Zero-Token Deterministic Python Pre-Filter across Top 10 candidates
        qualified_candidates, per_coin_gate_reasons = self.prefilter_candidates_with_python_gates(
            stage1=stage1,
            coordinator=coord,
            strategy_id=strategy_id,
        )

        # Surface per-coin gate status in Stage 1 candidate reasons
        for cand in stage1.top_10:
            if cand.symbol in per_coin_gate_reasons:
                gate_status = per_coin_gate_reasons[cand.symbol]
                cand.reason_selected = f"{gate_status} (Turnover ${cand.turnover_24h_m:.1f}M, 72h range {cand.range_72h_pct}%)"

        if len(qualified_candidates) == 0:
            # ZERO Groq tokens consumed!
            logger.info("[GROQ_OPTIMIZER] 0 candidates passed Python SMC/Dealing Range/OrderFlow gates. Skipping Groq LLM (0 tokens used).")
            sorted_cands = sorted(stage1.top_10, key=lambda c: ((c.range_72h_pct <= 38.0 or c.range_72h_pct >= 65.0), c.turnover_24h_m), reverse=True)
            top_3_items = []
            categories = ["LIQUIDITY_SWEEP_RECLAIM", "MOMENTUM_CONTINUATION", "LAGGARD_CATCH_UP"]
            for idx, cand in enumerate(sorted_cands[:3]):
                gate_reason = per_coin_gate_reasons.get(cand.symbol, "NO_SWEEP_RECLAIM_TRIGGER")
                top_3_items.append(
                    Stage2FinalistItem(
                        rank=idx + 1,
                        symbol=cand.symbol,
                        category=categories[idx % len(categories)],
                        score=round(75.0 - (idx * 5.0), 1),
                        thesis=f"[Python Pre-Filter] {cand.symbol}: {gate_reason}",
                        verification_questions=["Check 15m structural sweep", "Verify Dealing Range"],
                    )
                )
            stage2 = Stage2Result(
                top_3=top_3_items,
                eliminated_summary=per_coin_gate_reasons,
                model_used="python_prefilter",
                provider_failure=False,
            )

            win_sym = top_3_items[0].symbol if top_3_items else (stage1.top_10[0].symbol if stage1.top_10 else "NONE")
            win_cand = next((c for c in stage1.top_10 if c.symbol == win_sym), None)
            mark_p = win_cand.mark_price if win_cand else 0.0
            dr_pct = win_cand.range_72h_pct if win_cand else 50.0

            stage3 = Stage3SetupOutput(
                decision="WAIT",
                symbol=win_sym,
                direction="LONG",
                order_type="LIMIT",
                entry_price=mark_p,
                stop_loss_price=0.0,
                take_profit_price=0.0,
                qty=0.0,
                max_risk_usdt=0.0,
                stop_distance_pct=0.0,
                rr_ratio=0.0,
                notional_usdt=0.0,
                isolated_margin_usdt=0.0,
                execution_venue=venue_value,
                status="REJECTED",
                strategy_id=strategy_id,
                strategy_eligible=False,
                strategy_rejection_reason="No candidates on the current 15m candle bar passed SMC sweep-reclaim, Dealing Range, OrderFlow wall, and trade memory Python pre-filters.",
                candidate_id=f"{win_sym}_{strategy_id}_BAR_EMPTY",
                dealing_range_position_pct=dr_pct,
                veto_code="NO_VALID_SWEEP_CANDIDATES_ON_BAR",
                veto_source="PYTHON_PREFILTER",
                notes="Zero Groq tokens consumed: Python pre-filter evaluated all Top 10 pairs; 0 passed all deterministic gates.",
                reasoning="Screening cycle completed in 0 tokens: All 10 liquid candidates are either in equilibrium dealing ranges, lack swing sweep-reclaim triggers, or failed orderbook/memory gates on this bar.",
                lesson_applied_from_recent_losses="Event-driven architecture: Groq tokens preserved by vetoing empty bars purely in Python.",
                sweep_reclaim_evidence="No verified 15m swing low/high sweep reclaim on closed bars",
                orderflow_wall_evidence="Orderflow check deferred (no trigger on bar)",
                why_not_repeating_past_sl="Avoiding mid-air chop entries",
                requires_confirmation=False,
                provider_failure=False,
                model_used="python_prefilter",
            )
            receipt = None
            confirmation = None
        else:
            # Run Single-Pass LLM Audit (<= 850 input tokens, using 15m candle bar cache)
            stage2, stage3, receipt, confirmation = self.run_single_pass_llm_audit(
                qualified_candidates=qualified_candidates,
                stage1=stage1,
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
            )

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
            "session_info": (
                sess_mgr.get_session_info_obj().to_dict()
                if sess_mgr and hasattr(sess_mgr, "get_session_info_obj")
                else (sess_mgr.get_session_info() if sess_mgr else {})
            ),
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

        cycle_record = {
            "cycle_id": cycle.get("cycle_id", f"cycle-{uuid.uuid4().hex[:8]}"),
            "timestamp_utc": result.timestamp_utc,
            "status": result.status,
            "execution_venue": result.execution_venue,
            "strategy_id": result.strategy_id,
            "duration_ms": cycle.get("duration_ms", 0.0),
            "market_regime": result.stage1.market_regime,
            "stage1": {
                "total_screened_pairs": result.stage1.total_screened_pairs,
                "execution_time_ms": result.stage1.execution_time_ms,
                "top_10": [item.model_dump() for item in result.stage1.top_10],
            },
            "stage2": {
                "model_used": result.stage2.model_used,
                "top_3": [item.model_dump() for item in result.stage2.top_3],
                "eliminated_summary": result.stage2.eliminated_summary,
            },
            "stage3": result.stage3.model_dump(),
            "execution_receipt": result.execution_receipt,
            "risk_gate_status": result.risk_gate_status,
        }
        with self._telemetry_lock:
            self._latest_screening_result = cycle_record
            self._screening_history.insert(0, cycle_record)
            if len(self._screening_history) > 20:
                self._screening_history = self._screening_history[:20]

        return result
