"""FastAPI Application and REST API Endpoints.

Provides endpoints for scanner status, watchlist controls, setup cards,
backtest comparisons, and signal journal. Serves the interactive dashboard.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hmac
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config.settings import ExecutionMode, get_default_config
from src.core.logger import logger
from src.core.types import CandidateStatus, Direction, EntryCategory, ExecutionStatus, MarketBias, RiskAssessment, SetupCandidate, StructureState
from src.data.database import DatabaseManager
from src.exchange.aster import AsterDexAdapter
from src.journal.repository import JournalRepository
from src.risk.manager import RiskManager
from src.scheduler.runner import ScannerRunner, rank_candidates
from src.backtest.engine import BacktestEngine
from src.ml.models import ModelRegistry
from src.execution.events import mask_webhook_url, validate_webhook_url


# App state containers
cfg = get_default_config()
db = DatabaseManager(cfg.db_path)
# Least-privilege initialization: public market-data adapter never loads private API credentials in PAPER/RESEARCH
exchange = AsterDexAdapter(
    base_url=cfg.exchange.base_url,
    api_key=None,
    api_secret=None,
    request_timeout=cfg.exchange.request_timeout,
    max_retries=cfg.exchange.max_retries,
    retry_delay_sec=cfg.exchange.retry_delay_sec,
    load_env_credentials=False,
)
journal = JournalRepository(db)
risk_mgr = RiskManager(
    initial_equity_usdt=cfg.risk.initial_equity_usdt,
    trade_risk_usdt=cfg.risk.risk_per_trade_usdt,
    daily_loss_limit_usdt=cfg.risk.daily_loss_limit_usdt,
    weekly_loss_limit_usdt=cfg.risk.weekly_loss_limit_usdt,
    max_open_positions=cfg.risk.max_open_positions,
    max_entries_per_week=cfg.risk.max_entries_per_week,
)
risk_mgr.sync_state_from_journal(journal)

specs_map: Dict[str, Any] = {}
runner: Optional[ScannerRunner] = None
enabled_symbols: set = set(cfg.enabled_symbols)

# Concurrency and background execution controls
scan_lock = asyncio.Lock()
stop_event = asyncio.Event()
bg_task: Optional[asyncio.Task] = None
BACKGROUND_SCAN_ENABLED: bool = True
CANDLE_GRACE_PERIOD_SEC: int = 60

LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
ALLOWED_MUTATING_ORIGINS = {
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    f"http://{cfg.host}:{cfg.port}",
}


def require_mutating_api_access(
    request: Request,
    x_scanner_token: Optional[str] = Header(default=None, alias="X-Scanner-Token"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    origin: Optional[str] = Header(default=None, alias="Origin"),
    referer: Optional[str] = Header(default=None, alias="Referer"),
) -> None:
    """Enforce local loopback access, Origin/Referer CSRF protection, and/or SCANNER_API_TOKEN for state-mutating POST endpoints."""
    # 1. Cross-Origin (CSRF) guard: if Origin or Referer is present, it must match an allowed local origin
    if origin and origin.strip().rstrip("/") not in ALLOWED_MUTATING_ORIGINS:
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: untrusted Origin '{origin}' rejected for mutating endpoint.",
        )
    if referer:
        ref_clean = referer.strip()
        if not any(ref_clean.startswith(ao) for ao in ALLOWED_MUTATING_ORIGINS):
            raise HTTPException(
                status_code=403,
                detail="Forbidden: untrusted Referer rejected for mutating endpoint.",
            )

    configured_token = (os.getenv("SCANNER_API_TOKEN") or cfg.scanner_api_token or "").strip()
    provided_token = (x_scanner_token or "").strip()
    if not provided_token and authorization and authorization.lower().startswith("bearer "):
        provided_token = authorization[7:].strip()

    if configured_token:
        if not provided_token or not hmac.compare_digest(provided_token, configured_token):
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: valid X-Scanner-Token or Bearer token required for mutating endpoints.",
            )
        return

    client_host = (request.client.host if request.client else "") or ""
    if client_host not in LOCAL_CLIENT_HOSTS:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: mutating endpoints are restricted to local loopback unless SCANNER_API_TOKEN is configured.",
        )


async def background_scan_worker():
    """
    Decoupled background scanning loop.
    Checks completed 1H candle boundaries with a configurable grace period.
    Runs off-thread via asyncio.to_thread to keep FastAPI completely responsive.
    Guarded by scan_lock against overlapping runs.
    """
    logger.info("Background scanner loop started.")
    try:
        await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        return

    # Initial scan on startup if runner has not completed a scan yet
    if BACKGROUND_SCAN_ENABLED and runner and not getattr(runner, "has_completed_scan", False) and not stop_event.is_set():
        active_symbols = [s for s in cfg.watchlist if s in enabled_symbols]
        if active_symbols and not scan_lock.locked():
            async with scan_lock:
                try:
                    logger.info("Running initial background scan on startup...")
                    await asyncio.to_thread(runner.run_scan, symbols=active_symbols, sync_live=True)
                    logger.info("Initial background scan completed successfully.")
                except Exception as e:
                    logger.warning(f"Initial background scan error: {e}")

    last_scanned_hour: Optional[int] = None

    while not stop_event.is_set() and BACKGROUND_SCAN_ENABLED:
        try:
            await asyncio.sleep(15.0)
            if stop_event.is_set():
                break

            now_utc = datetime.now(timezone.utc)
            current_hour = now_utc.hour
            current_minute = now_utc.minute
            current_second = now_utc.second

            # 1H candle closes at HH:00. After grace period (e.g. 60s),
            # if we have not scanned this hour yet, trigger background scan.
            is_new_candle_boundary = (
                (current_minute * 60 + current_second >= CANDLE_GRACE_PERIOD_SEC)
                and (current_hour != last_scanned_hour)
            )

            if is_new_candle_boundary and not scan_lock.locked():
                active_symbols = [s for s in cfg.watchlist if s in enabled_symbols]
                if active_symbols:
                    async with scan_lock:
                        logger.info(f"Triggering background scan for closed candle hour {current_hour}:00 UTC...")
                        try:
                            await asyncio.to_thread(runner.run_scan, symbols=active_symbols, sync_live=True)
                            last_scanned_hour = current_hour
                            logger.info(f"Background scan complete for hour {current_hour}:00 UTC.")
                        except Exception as e:
                            logger.error(f"Background scan cycle error: {e}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in background scan loop: {e}")
            await asyncio.sleep(10.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runner, specs_map, bg_task, stop_event
    logger.info("Initializing scanner API server...")
    try:
        specs_map = exchange.get_exchange_info()
    except Exception as e:
        logger.warning(f"Could not load live exchange info on startup: {e}")

    runner = ScannerRunner(
        exchange=exchange,
        db=db,
        journal=journal,
        risk_manager=risk_mgr,
        specs_map=specs_map,
        models_dir=cfg.data_dir / "models",
        target_isolated_margin=cfg.risk.target_isolated_margin_usdt,
        target_isolated_margin_max=cfg.risk.target_isolated_margin_max_usdt,
        hard_risk_limit_usdt=cfg.risk.hard_risk_limit_usdt,
        max_leverage=cfg.risk.max_leverage,
        ai_config=cfg.ai,
        mode=cfg.mode,
        paper_config=cfg.paper,
    )

    # Reconcile stale or expired database setups on startup
    try:
        reconciled_count = journal.reconcile_stale_setups(stale_threshold_hours=float(getattr(cfg, "stale_threshold_hours", 3.0)))
        if reconciled_count > 0:
            logger.info(f"Reconciled {reconciled_count} stale/expired setups in database on startup.")
    except Exception as e:
        logger.warning(f"Startup database setup reconciliation error: {e}")

    # Validate Discord monitoring configuration safely
    discord_url = cfg.paper.discord_webhook_url
    if not discord_url:
        logger.info("Discord notifications: DISABLED (no webhook configured)")
    elif not validate_webhook_url(discord_url):
        logger.warning("Invalid Discord webhook URL format; notifications disabled.")
    else:
        masked_url = mask_webhook_url(discord_url)
        logger.info(f"Discord notifications: ENABLED ({masked_url})")

    stop_event.clear()
    bg_task = asyncio.create_task(background_scan_worker())
    logger.info("Background scanner task started.")

    yield

    logger.info("Shutting down scanner API server...")
    stop_event.set()
    if bg_task and not bg_task.done():
        bg_task.cancel()
        try:
            await bg_task
        except asyncio.CancelledError:
            pass
    logger.info("Background scanner worker shutdown cleanly.")


app = FastAPI(
    title="SMC-SNR ML Perpetual Setup Scanner",
    version=cfg.version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        f"http://{cfg.host}:{cfg.port}",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Scanner-Token", "Authorization"],
)


@app.get("/", response_class=HTMLResponse)
def get_dashboard() -> HTMLResponse:
    """Serve the local single-page dashboard HTML."""
    html_path = Path(__file__).parent / "dashboard.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard HTML not found.</h1>")


@app.get("/api/status")
def get_system_status() -> Dict[str, Any]:
    """Return account health, risk budgets, execution mode, scanner state, and timestamps."""
    now_utc = datetime.now(timezone.utc)
    now_ms = int(now_utc.timestamp() * 1000)
    effective_risk = risk_mgr.get_effective_trade_risk(now_ms)
    can_open, block_reason = risk_mgr.can_open_new_position(now_ms)

    # Determine accurate scanner process and freshness state
    is_worker_alive = bool(bg_task is not None and not bg_task.done())
    is_stopped = not BACKGROUND_SCAN_ENABLED or stop_event.is_set()

    last_scan_age_sec: Optional[int] = None
    if runner and runner.last_scan_utc:
        try:
            ls_dt = datetime.fromisoformat(runner.last_scan_utc)
            last_scan_age_sec = max(0, int((now_utc - ls_dt).total_seconds()))
        except Exception:
            pass

    candle_age_sec = runner.candle_age_seconds if runner else None
    stale_threshold_sec = int(getattr(cfg, "stale_threshold_hours", 3.0) * 3600)

    is_stale = False
    if candle_age_sec is not None and candle_age_sec > stale_threshold_sec:
        is_stale = True
    elif last_scan_age_sec is not None and last_scan_age_sec > stale_threshold_sec:
        is_stale = True
    elif runner and runner.scanner_state == "STALE":
        is_stale = True

    if is_stopped:
        computed_state = "STOPPED"
    elif runner and runner.scanner_state == "ERROR":
        computed_state = "ERROR"
    elif is_stale:
        computed_state = "STALE"
    elif scan_lock.locked():
        computed_state = "RUNNING"
    else:
        computed_state = runner.scanner_state if (runner and runner.scanner_state in ("IDLE", "RUNNING", "ERROR")) else "IDLE"

    return {
        "app_name": cfg.app_name,
        "version": cfg.version,
        "mode": cfg.mode.value,
        "live_trading_locked": cfg.live_trading_locked,
        "capital": {
            "initial_equity_usdt": cfg.risk.initial_equity_usdt,
            "current_equity_usdt": round(risk_mgr.equity_usdt, 4),
            "margin_mode": cfg.risk.margin_mode,
            "max_leverage": cfg.risk.max_leverage,
        },
        "risk_limits": {
            "configured_trade_risk_usdt": cfg.risk.risk_per_trade_usdt,
            "effective_trade_risk_usdt": effective_risk,
            "daily_loss_limit_usdt": cfg.risk.daily_loss_limit_usdt,
            "daily_loss_used_usdt": round(risk_mgr.daily_loss_usdt, 4),
            "weekly_loss_limit_usdt": cfg.risk.weekly_loss_limit_usdt,
            "weekly_loss_used_usdt": round(risk_mgr.weekly_loss_usdt, 4),
            "open_positions": len(risk_mgr.open_trades),
            "max_open_positions": cfg.risk.max_open_positions,
            "weekly_entries": risk_mgr.weekly_entries_count,
            "max_weekly_entries": cfg.risk.max_entries_per_week,
            "can_open_position": can_open,
            "block_reason": block_reason,
            "target_isolated_margin_usdt": cfg.risk.target_isolated_margin_usdt,
            "target_isolated_margin_max_usdt": cfg.risk.target_isolated_margin_max_usdt,
            "hard_risk_limit_usdt": cfg.risk.hard_risk_limit_usdt,
        },
        "exchange": {
            "name": cfg.exchange.name,
            "base_url": cfg.exchange.base_url,
        },
        "scanner": {
            "state": computed_state,
            "process_status": "STOPPED" if is_stopped else ("RUNNING" if is_worker_alive else "IDLE"),
            "has_completed_scan": bool(getattr(runner, "has_completed_scan", False)) if runner else False,
            "scan_count": int(getattr(runner, "scan_count", 0)) if runner else 0,
            "last_scan_status": getattr(runner, "last_scan_status", "NEVER_RUN") if runner else "NEVER_RUN",
            "last_scan_utc": runner.last_scan_utc if runner else None,
            "last_scan_age_seconds": last_scan_age_sec,
            "last_successful_scan_utc": runner.last_successful_scan_utc if runner else None,
            "latest_candle_time_utc": runner.latest_candle_time_utc if runner else None,
            "candle_age_seconds": candle_age_sec,
            "symbols_attempted": list(getattr(runner, "last_symbols_attempted", [])) if runner else [],
            "last_error": runner.last_error if runner else None,
            "background_worker_active": is_worker_alive,
            "is_stale": is_stale,
        },
        "paper": {
            "enabled": cfg.paper.enabled,
            "open_positions": len(runner.paper_engine.open_positions) if (runner and runner.paper_engine) else 0,
            "pending_orders": len(runner.paper_engine.pending_orders) if (runner and runner.paper_engine) else 0,
            "slippage_rate": cfg.paper.slippage_rate,
            "maker_fee_rate": cfg.paper.maker_fee_rate,
            "taker_fee_rate": cfg.paper.taker_fee_rate,
            "discord_monitoring": bool(cfg.paper.discord_webhook_url),
        },
        "forward_validation": journal.compute_forward_validation_cohort(),
        "discord": {
            "enabled": bool(cfg.paper.discord_webhook_url and validate_webhook_url(cfg.paper.discord_webhook_url)),
            "webhook_configured": bool(cfg.paper.discord_webhook_url),
            "webhook_masked": mask_webhook_url(cfg.paper.discord_webhook_url) if cfg.paper.discord_webhook_url else None,
        },
        "last_scan_utc": runner.last_scan_utc if runner else None,
    }


@app.post("/api/discord/test", dependencies=[Depends(require_mutating_api_access)])
async def test_discord_webhook() -> Dict[str, Any]:
    """Send an operational diagnostic verification ping to the configured Discord webhook."""
    if not runner or not runner.event_dispatcher:
        raise HTTPException(status_code=503, detail="Runner or event dispatcher not initialized")
    if not cfg.paper.discord_webhook_url:
        return {
            "status": "DISABLED",
            "message": "Discord notifications are disabled; no webhook configured.",
            "success": False,
        }
    success = runner.event_dispatcher.send_diagnostic_ping()
    masked = mask_webhook_url(cfg.paper.discord_webhook_url)
    if success:
        return {
            "status": "SUCCESS",
            "message": f"Diagnostic ping successfully delivered to Discord ({masked}).",
            "success": True,
            "webhook_masked": masked,
        }
    else:
        return {
            "status": "FAILED",
            "message": f"Diagnostic ping delivery failed for {masked}. Check server logs for details.",
            "success": False,
            "webhook_masked": masked,
        }


@app.post("/api/scanner/start", dependencies=[Depends(require_mutating_api_access)])
async def start_scanner() -> Dict[str, Any]:
    """Start or resume background scanner loop."""
    global BACKGROUND_SCAN_ENABLED, stop_event, bg_task
    BACKGROUND_SCAN_ENABLED = True
    stop_event.clear()
    if bg_task is None or bg_task.done():
        bg_task = asyncio.create_task(background_scan_worker())
    if runner:
        runner.scanner_state = "IDLE"
    logger.info("Background scanner resumed via API.")
    return {"status": "SUCCESS", "message": "Background scanner resumed.", "scanner_state": "IDLE"}


@app.post("/api/scanner/stop", dependencies=[Depends(require_mutating_api_access)])
async def stop_scanner() -> Dict[str, Any]:
    """Stop or pause background scanner loop."""
    global BACKGROUND_SCAN_ENABLED, stop_event
    BACKGROUND_SCAN_ENABLED = False
    stop_event.set()
    if runner:
        runner.scanner_state = "STOPPED"
    logger.info("Background scanner stopped via API.")
    return {"status": "SUCCESS", "message": "Background scanner paused.", "scanner_state": "STOPPED"}




@app.get("/api/ai/status")
def get_ai_status() -> Dict[str, Any]:
    """Return status of Qwen2.5-7B AI analysis integration."""
    return {
        "enabled": cfg.ai.enabled,
        "mode": cfg.ai.mode.value,
        "model_id": cfg.ai.model_id,
        "api_url_configured": bool(cfg.ai.api_url),
        "timeout_sec": cfg.ai.read_timeout_sec,
        "max_tokens": cfg.ai.max_tokens,
    }


@app.get("/api/symbols")
def get_symbols() -> List[Dict[str, Any]]:
    """Return watchlist symbols with enabled toggle status and contract specifications."""
    results = []
    for s in cfg.watchlist:
        specs = specs_map.get(s)
        results.append({
            "symbol": s,
            "enabled": s in enabled_symbols,
            "min_notional": specs.min_notional if specs else 5.0,
            "step_size": specs.step_size if specs else 1.0,
            "tick_size": specs.tick_size if specs else 0.01,
            "price_precision": specs.price_precision if specs else 2,
        })
    return results


class ToggleSymbolRequest(BaseModel):
    enabled: bool


@app.post("/api/symbols/{symbol}/toggle", dependencies=[Depends(require_mutating_api_access)])
def toggle_symbol(symbol: str, req: ToggleSymbolRequest) -> Dict[str, Any]:
    """Enable or disable scanning for a specific symbol."""
    sym = symbol.upper()
    if sym not in cfg.watchlist:
        raise HTTPException(status_code=404, detail="Symbol not found in watchlist.")

    if req.enabled:
        enabled_symbols.add(sym)
    else:
        enabled_symbols.discard(sym)

    return {"symbol": sym, "enabled": sym in enabled_symbols}


@app.get("/api/setups")
def get_setups(
    status: Optional[str] = Query(None, description="Filter status: ALL, READY, WATCHLIST, INFEASIBLE, INVALIDATED, STALE, ERROR"),
    sort_by: Optional[str] = Query("confidence_desc", description="Sort option: confidence_desc, confidence_asc, latest"),
    symbol: Optional[str] = Query(None, description="Filter by symbol search substring"),
    category: Optional[str] = Query(None, description="Filter entry category: ALL, Breakout, Pullback, Reversal, Range/Mean Reversion, Unclassified"),
    include_historical: bool = Query(False, description="Explicitly include historical journal rows when current scan has 0 candidates"),
) -> Dict[str, Any]:
    """Return latest setups discovered by the scanner with filtering and deterministic confidence ranking."""
    raw_candidates: List[SetupCandidate] = []
    has_completed_scan = bool(getattr(runner, "has_completed_scan", False)) if runner else False
    has_in_memory_results = bool(runner and runner.last_results)

    if runner and (has_completed_scan or has_in_memory_results) and not include_historical:
        # Use authoritative current scan results (even when runner.last_results == [] on a completed empty scan!)
        raw_candidates = list(runner.last_results)
    else:
        # Fallback to journal ONLY if runner has never completed a scan in this session (or include_historical=True)
        db_setups = journal.get_latest_setups(limit=50)
        for s in db_setups:
            det = s.get("details") or {}
            ra_dict = det.get("risk_assessment")
            risk_assessment: Optional[RiskAssessment] = None
            if isinstance(ra_dict, dict) and ra_dict:
                try:
                    entry_p = float(det.get("entry_price", s["entry_price"]))
                    sl_p = float(det.get("stop_loss", s["stop_loss"]))
                    sym = det.get("symbol", s["symbol"])
                    stop_dist_pct = abs(entry_p - sl_p) / entry_p if entry_p > 0 else 0.0
                    req_margin = float(ra_dict.get("required_margin_usdt", ra_dict.get("actual_margin_usdt", 0.0)))
                    notional = float(ra_dict.get("position_notional_usdt", 0.0))
                    pos_lev = float(ra_dict.get("position_leverage", ra_dict.get("leverage", 10.0)))
                    eq_exp = float(ra_dict.get("equity_exposure_ratio", ra_dict.get("effective_leverage", 0.0)))

                    risk_assessment = RiskAssessment(
                        symbol=sym,
                        entry_price=entry_p,
                        stop_loss=sl_p,
                        stop_distance_pct=stop_dist_pct,
                        risk_budget_usdt=float(ra_dict.get("risk_budget_usdt", 0.0)),
                        position_notional_usdt=notional,
                        quantity=float(ra_dict.get("quantity", 0.0)),
                        required_margin_usdt=req_margin,
                        effective_leverage=eq_exp,
                        estimated_fee_usdt=float(ra_dict.get("estimated_fee_usdt", 0.0)),
                        estimated_slippage_usdt=float(ra_dict.get("estimated_slippage_usdt", 0.0)),
                        is_feasible=bool(ra_dict.get("is_feasible", False)),
                        infeasible_reason=ra_dict.get("infeasible_reason"),
                        target_isolated_margin_usdt=float(ra_dict.get("target_isolated_margin_usdt", 6.0)),
                        estimated_loss_usdt=float(ra_dict.get("estimated_loss_usdt", 0.0)),
                        estimated_loss_pct=float(ra_dict.get("estimated_loss_pct", 0.0)),
                        position_leverage=pos_lev,
                        equity_exposure_ratio=eq_exp,
                    )
                except Exception as e:
                    logger.debug(f"Could not reconstruct risk_assessment for setup: {e}")
                    risk_assessment = None

            try:
                cand = SetupCandidate(
                    candidate_id=det.get("candidate_id", s["candidate_id"]),
                    symbol=det.get("symbol", s["symbol"]),
                    timestamp_ms=det.get("timestamp_ms", s["timestamp_ms"]),
                    direction=Direction(det.get("direction", s["direction"])),
                    bias_4h=MarketBias(det.get("bias_4h", s["bias_4h"])),
                    structure_1h=StructureState(det.get("structure_1h", s["structure_1h"])),
                    entry_price=float(det.get("entry_price", s["entry_price"])),
                    stop_loss=float(det.get("stop_loss", s["stop_loss"])),
                    take_profit_1=float(det.get("take_profit_1", s["take_profit_1"])),
                    take_profit_2=float(det["take_profit_2"]) if det.get("take_profit_2") is not None else None,
                    gross_reward_to_risk=float(det.get("gross_reward_to_risk", s.get("gross_rr", 0.0))),
                    net_reward_to_risk=float(det.get("net_reward_to_risk", s.get("net_rr", 0.0))),
                    invalidation_price=float(det.get("invalidation_price", s.get("invalidation_price", 0.0))),
                    invalidation_reason=det.get("invalidation_reason", ""),
                    smc_evidence=det.get("smc_evidence", []),
                    snr_evidence=det.get("snr_evidence", []),
                    supporting_reasons=det.get("supporting_reasons", []),
                    opposing_reasons=det.get("opposing_reasons", []),
                    ml_probability=float(det["ml_probability"]) if det.get("ml_probability") is not None else None,
                    calibration_status=det.get("calibration_status", s.get("calibration_status", "INSUFFICIENT_DATA")),
                    eval_sample_count=int(det.get("eval_sample_count", s.get("eval_sample_count", 0))),
                    status=CandidateStatus(det.get("status", s.get("status", "WATCHLIST"))),
                    entry_category=EntryCategory(det.get("entry_category", s.get("entry_category", "Unclassified"))),
                    rejection_reasons=det.get("rejection_reasons", []),
                    expires_at_ms=int(det.get("expires_at_ms", 0)),
                    risk_assessment=risk_assessment,
                )
                raw_candidates.append(cand)
            except Exception as e:
                logger.warning(f"Error parsing journal setup: {e}")

    # Latest-per-symbol aggregation: retain only newest candidate per symbol by candle timestamp_ms
    latest_by_symbol: Dict[str, SetupCandidate] = {}
    for c in raw_candidates:
        sym = c.symbol.upper()
        if sym not in latest_by_symbol or c.timestamp_ms > latest_by_symbol[sym].timestamp_ms:
            latest_by_symbol[sym] = c

    deduped_candidates = list(latest_by_symbol.values())

    # Dynamic Staleness & Execution Status Evaluation
    now_utc = datetime.now(timezone.utc)
    now_ms = int(now_utc.timestamp() * 1000)
    stale_threshold_hours = float(getattr(cfg, "stale_threshold_hours", 3.0))

    for c in deduped_candidates:
        # 1. Staleness and TTL expiration
        candle_age_hours = (now_ms - c.timestamp_ms) / (3600.0 * 1000.0)
        is_expired = bool(c.expires_at_ms and now_ms > c.expires_at_ms)
        is_candle_stale = candle_age_hours > stale_threshold_hours

        if is_expired or is_candle_stale:
            c.status = CandidateStatus.STALE
            if is_expired and not any("expired" in r.lower() for r in c.rejection_reasons):
                exp_utc = (
                    datetime.fromtimestamp(c.expires_at_ms / 1000.0, tz=timezone.utc).isoformat()
                    if c.expires_at_ms
                    else "N/A"
                )
                c.rejection_reasons.append(f"Setup expired at {exp_utc} (12h TTL exceeded).")
            if is_candle_stale and not any("candle data age" in r.lower() for r in c.rejection_reasons):
                c.rejection_reasons.append(
                    f"Candle data age ({candle_age_hours:.1f}h) exceeds {stale_threshold_hours:.1f}h staleness threshold."
                )

        # 1b. Scanner Stopped Invariant: If scanner is stopped, no signal can be presented as READY
        is_stopped = not BACKGROUND_SCAN_ENABLED or stop_event.is_set() or (runner and runner.scanner_state == "STOPPED")
        if is_stopped and c.status in (CandidateStatus.READY, CandidateStatus.VALID):
            c.status = CandidateStatus.STALE
            if not any("scanner is stopped" in r.lower() for r in c.rejection_reasons):
                c.rejection_reasons.append("Scanner is STOPPED / background worker inactive. Signal not active.")

        # 2. Execution Status Reconciliation
        if runner:
            runner.sync_candidate_execution_status(c)
        else:
            c.execution_status = ExecutionStatus.NOT_SUBMITTED

        # 3. AsterDEX Reference Price & Provenance Information Enrichment
        try:
            latest_c = db.get_latest_candle(c.symbol, "1h")
            if latest_c:
                c.reference_price = latest_c.close
                c.mark_price = latest_c.close
                c.reference_price_time_utc = datetime.fromtimestamp(
                    latest_c.timestamp_ms / 1000.0, tz=timezone.utc
                ).isoformat()
                c.mark_price_time_utc = c.reference_price_time_utc
                c.reference_price_type = "1H_CLOSED_CANDLE"
                c.reference_data_age_hours = round(
                    max(0.0, (now_ms - latest_c.timestamp_ms) / (3600.0 * 1000.0)), 2
                )
                c.is_reference_data_stale = c.reference_data_age_hours > stale_threshold_hours
            else:
                c.reference_price = None
                c.mark_price = None
                c.reference_price_time_utc = None
                c.mark_price_time_utc = None
                c.reference_price_type = "NO_DATA"
                c.reference_data_age_hours = None
                c.is_reference_data_stale = True
        except Exception as e:
            logger.debug(f"Could not fetch latest candle for {c.symbol}: {e}")

        # Unrealized PnL and ROE: Only populated when an actual open position exists
        c.unrealized_pnl_usdt = None
        c.roe_pct = None
        if runner and runner.paper_engine and c.symbol in runner.paper_engine.open_positions:
            pos = runner.paper_engine.open_positions[c.symbol]
            effective_price = c.reference_price if c.reference_price is not None else c.mark_price
            if pos.is_open and effective_price is not None:
                if pos.direction == Direction.LONG:
                    raw_pnl = (effective_price - pos.entry_price) * pos.quantity
                else:
                    raw_pnl = (pos.entry_price - effective_price) * pos.quantity
                net_pnl = raw_pnl - pos.entry_fee - pos.funding_paid
                roe = (net_pnl / pos.margin_usdt * 100.0) if pos.margin_usdt > 0 else 0.0
                c.unrealized_pnl_usdt = round(net_pnl, 4)
                c.roe_pct = round(roe, 2)

    counts_by_status = {
        "ALL": len(deduped_candidates),
        "READY": sum(1 for c in deduped_candidates if c.status in (CandidateStatus.READY, CandidateStatus.VALID)),
        "WATCHLIST": sum(1 for c in deduped_candidates if c.status == CandidateStatus.WATCHLIST),
        "INFEASIBLE": sum(1 for c in deduped_candidates if c.status == CandidateStatus.INFEASIBLE),
        "INVALIDATED": sum(1 for c in deduped_candidates if c.status == CandidateStatus.INVALIDATED),
        "STALE": sum(1 for c in deduped_candidates if c.status == CandidateStatus.STALE),
        "ERROR": sum(1 for c in deduped_candidates if c.status == CandidateStatus.ERROR),
    }

    counts_by_category = {
        "ALL": len(deduped_candidates),
        "Breakout": sum(1 for c in deduped_candidates if c.entry_category == EntryCategory.BREAKOUT),
        "Pullback": sum(1 for c in deduped_candidates if c.entry_category == EntryCategory.PULLBACK),
        "Reversal": sum(1 for c in deduped_candidates if c.entry_category == EntryCategory.REVERSAL),
        "Range/Mean Reversion": sum(1 for c in deduped_candidates if c.entry_category == EntryCategory.RANGE_MEAN_REVERSION),
        "Unclassified": sum(1 for c in deduped_candidates if c.entry_category == EntryCategory.UNCLASSIFIED),
    }

    filtered = list(deduped_candidates)
    if status and status.upper() != "ALL":
        st_upper = status.upper()
        if st_upper in ("READY", "VALID"):
            filtered = [c for c in filtered if c.status in (CandidateStatus.READY, CandidateStatus.VALID)]
        else:
            filtered = [c for c in filtered if c.status.value == st_upper]

    if category and category.upper() != "ALL":
        cat_clean = category.strip().upper()
        filtered = [
            c for c in filtered
            if c.entry_category.value.upper() == cat_clean
            or (cat_clean == "RANGE" and c.entry_category == EntryCategory.RANGE_MEAN_REVERSION)
            or (cat_clean == "UNCLASSIFIED" and c.entry_category == EntryCategory.UNCLASSIFIED)
        ]

    if symbol and symbol.strip():
        sym_clean = symbol.strip().upper()
        filtered = [c for c in filtered if sym_clean in c.symbol.upper()]

    sorted_candidates = rank_candidates(filtered, sort_by=sort_by or "confidence_desc")
    setups_data = [c.to_dict() for c in sorted_candidates]
    ready_count = counts_by_status["READY"]

    is_stopped = not BACKGROUND_SCAN_ENABLED or stop_event.is_set()
    computed_scanner_state = "STOPPED" if is_stopped else (runner.scanner_state if runner else "IDLE")
    if counts_by_status["STALE"] == len(deduped_candidates) and len(deduped_candidates) > 0:
        computed_scanner_state = "STALE"

    return {
        "status": "VALID" if ready_count > 0 else "NO_TRADE",
        "count": len(setups_data),
        "valid_count": ready_count,
        "ready_count": ready_count,
        "counts_by_status": counts_by_status,
        "counts_by_category": counts_by_category,
        "scanner_state": computed_scanner_state,
        "has_completed_scan": has_completed_scan,
        "scan_count": int(getattr(runner, "scan_count", 0)) if runner else 0,
        "last_scan_status": getattr(runner, "last_scan_status", "NEVER_RUN") if runner else "NEVER_RUN",
        "last_scan_utc": runner.last_scan_utc if runner else None,
        "last_successful_scan_utc": runner.last_successful_scan_utc if runner else None,
        "symbols_attempted": list(getattr(runner, "last_symbols_attempted", [])) if runner else [],
        "last_error": runner.last_error if runner else None,
        "setups": setups_data,
    }



@app.post("/api/scan", dependencies=[Depends(require_mutating_api_access)])
async def trigger_scan() -> Dict[str, Any]:
    """Manually trigger an immediate scan cycle across enabled symbols with concurrency locking."""
    if runner is None:
        raise HTTPException(status_code=500, detail="Scanner runner not initialized.")

    if scan_lock.locked():
        return {
            "status": "ALREADY_RUNNING",
            "message": "A scan is already in progress. Please wait for it to complete.",
            "count": len(runner.last_results) if runner else 0,
            "valid_count": sum(1 for c in runner.last_results if c.status in (CandidateStatus.READY, CandidateStatus.VALID)) if runner else 0,
            "ready_count": sum(1 for c in runner.last_results if c.status in (CandidateStatus.READY, CandidateStatus.VALID)) if runner else 0,
            "setups": [c.to_dict() for c in runner.last_results] if runner else [],
        }

    active_symbols = [s for s in cfg.watchlist if s in enabled_symbols]
    if not active_symbols:
        return {"status": "NO_TRADE", "message": "No symbols enabled for scan.", "setups": [], "count": 0, "valid_count": 0}

    async with scan_lock:
        results = await asyncio.to_thread(runner.run_scan, symbols=active_symbols, sync_live=True)
        setups_data = [c.to_dict() for c in results]
        ready_count = sum(1 for s in setups_data if s["status"] in (CandidateStatus.READY.value, CandidateStatus.VALID.value))

        return {
            "status": "VALID" if ready_count > 0 else "NO_TRADE",
            "count": len(setups_data),
            "valid_count": ready_count,
            "ready_count": ready_count,
            "setups": setups_data,
        }



@app.get("/api/journal")
def get_journal(limit: int = 50) -> Dict[str, Any]:
    """Retrieve historical setup candidates and trade executions with performance summary."""
    setups = journal.get_latest_setups(limit=limit)
    trades = journal.get_trades(limit=limit)
    metrics = journal.compute_trade_metrics(trades)
    return {
        "setups": setups,
        "trades": trades,
        "metrics": metrics,
        "forward_validation": journal.compute_forward_validation_cohort(),
    }


@app.get("/api/trades")
def get_trades(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    direction: Optional[str] = Query(None, description="Filter direction: LONG, SHORT, ALL"),
    category: Optional[str] = Query(None, description="Filter entry category"),
    outcome: Optional[str] = Query(None, description="Filter outcome: WIN, LOSS, BREAKEVEN, OPEN, ALL"),
    period: Optional[str] = Query(None, description="Period filter: 24h, 7d, 30d, all"),
    open_only: bool = Query(False, description="Filter open positions only"),
    limit: int = Query(100, description="Max trades to return"),
) -> Dict[str, Any]:
    """Retrieve executed trade journal records and paper orders with robust filtering and performance metrics."""
    start_time_ms = None
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if period:
        p_lower = period.lower().strip()
        if p_lower == "24h":
            start_time_ms = now_ms - (24 * 3600 * 1000)
        elif p_lower == "7d":
            start_time_ms = now_ms - (7 * 24 * 3600 * 1000)
        elif p_lower == "30d":
            start_time_ms = now_ms - (30 * 24 * 3600 * 1000)

    trades = journal.get_trades(
        symbol=symbol,
        direction=direction,
        entry_category=category,
        outcome=outcome,
        open_only=open_only,
        start_time_ms=start_time_ms,
        limit=limit,
    )
    all_recorded_trades = journal.get_trades(limit=1000)
    overall_metrics = journal.compute_trade_metrics(all_recorded_trades)
    filtered_metrics = journal.compute_trade_metrics(trades)

    orders = journal.get_paper_orders(symbol=symbol)
    lifecycle_events = journal.get_lifecycle_events(symbol=symbol, limit=limit)
    forward_cohort = journal.compute_forward_validation_cohort()
    return {
        "count": len(trades),
        "trades": trades,
        "metrics": overall_metrics,
        "filtered_metrics": filtered_metrics,
        "paper_orders_count": len(orders),
        "paper_orders": orders[:limit],
        "lifecycle_events_count": len(lifecycle_events),
        "lifecycle_events": lifecycle_events,
        "forward_validation": forward_cohort,
    }


@app.get("/api/backtest")
def run_backtest_report(symbols: Optional[str] = None) -> Dict[str, Any]:
    """Run event-driven backtest comparing Deterministic SMC/SnR vs ML-Filtered across watchlist with chronological OOS decomposition."""
    if symbols:
        active_symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        active_symbols = [s for s in cfg.watchlist if s in enabled_symbols]

    if not active_symbols:
        active_symbols = list(cfg.watchlist)

    # 1. Deterministic baseline (No ML filter) across all active symbols
    engine_deterministic = BacktestEngine(
        db=db,
        symbol_specs_map=specs_map,
        initial_equity=cfg.risk.initial_equity_usdt,
        trade_risk=cfg.risk.risk_per_trade_usdt,
        daily_loss_limit=cfg.risk.daily_loss_limit_usdt,
        weekly_loss_limit=cfg.risk.weekly_loss_limit_usdt,
    )
    report_det = engine_deterministic.run_backtest(symbols=active_symbols)

    # 2. ML-filtered backtest (if model available)
    reg = ModelRegistry(cfg.data_dir / "models")
    model_bundle = reg.load_model()
    ml_model = model_bundle if model_bundle else None

    engine_ml = BacktestEngine(
        db=db,
        symbol_specs_map=specs_map,
        initial_equity=cfg.risk.initial_equity_usdt,
        trade_risk=cfg.risk.risk_per_trade_usdt,
        daily_loss_limit=cfg.risk.daily_loss_limit_usdt,
        weekly_loss_limit=cfg.risk.weekly_loss_limit_usdt,
    )
    report_ml = engine_ml.run_backtest(
        symbols=active_symbols,
        ml_model=ml_model,
        min_ml_confidence=cfg.ml.min_confidence_threshold,
    )

    # 3. Compute chronological Train / Validation / Out-of-Sample Test split metrics with 48h purge
    train_end_ms = 1787011200000    # 2026-08-18 00:00 UTC (48h before 2026-08-20)
    val_start_ms = 1787184000000    # 2026-08-20 00:00 UTC
    val_end_ms = 1788220800000      # 2026-09-01 00:00 UTC (48h before 2026-09-03)
    test_start_ms = 1788393600000   # 2026-09-03 00:00 UTC

    def _split_summary(trades_list):
        if not trades_list:
            return {"trades": 0, "win_rate_pct": 0.0, "net_pnl": 0.0, "expectancy_per_trade": 0.0}
        n = len(trades_list)
        wins = sum(1 for t in trades_list if t.pnl_net > 0)
        net = sum(t.pnl_net for t in trades_list)
        return {
            "trades": n,
            "win_rate_pct": round((wins / n) * 100.0, 2),
            "net_pnl": round(net, 4),
            "expectancy_per_trade": round(net / n, 4),
        }

    det_closed = list(engine_deterministic.risk_manager.closed_trades)
    det_train = [t for t in det_closed if t.entry_timestamp_ms < train_end_ms]
    det_val = [t for t in det_closed if val_start_ms <= t.entry_timestamp_ms < val_end_ms]
    det_oos = [t for t in det_closed if t.entry_timestamp_ms >= test_start_ms]
    oos_split = _split_summary(det_oos)

    if oos_split["trades"] < 30:
        oos_status = (
            f"INSUFFICIENT_OOS_SAMPLE ({oos_split['trades']}/30 portfolio OOS trades; "
            f"OOS Net PnL ${oos_split['net_pnl']:+.2f}, Expectancy ${oos_split['expectancy_per_trade']:+.4f}/trade)"
        )
    elif oos_split["expectancy_per_trade"] <= 0:
        oos_status = f"NEGATIVE_OOS_EXPECTANCY (${oos_split['expectancy_per_trade']:+.4f}/trade)"
    else:
        oos_status = f"VALIDATED_OOS_POSITIVE (${oos_split['expectancy_per_trade']:+.4f}/trade)"

    return {
        "symbols_evaluated": active_symbols,
        "cost_assumptions": {
            "maker_fee_rate": engine_deterministic.maker_fee,
            "taker_fee_rate": engine_deterministic.taker_fee,
            "slippage_rate": engine_deterministic.slippage,
            "target_isolated_margin_usdt": engine_deterministic.target_isolated_margin,
            "hard_sl_loss_limit_usdt": engine_deterministic.hard_risk_limit_usdt,
        },
        "chronological_splits": {
            "in_sample_train": _split_summary(det_train),
            "validation": _split_summary(det_val),
            "out_of_sample_test": oos_split,
            "purge_embargo_hours": 48,
            "historical_validation_status": oos_status,
        },
        "deterministic": {
            "initial_equity": report_det.initial_equity,
            "final_equity": report_det.final_equity,
            "total_net_pnl": report_det.total_net_pnl,
            "return_on_capital_pct": report_det.return_on_capital_pct,
            "total_trades": report_det.total_trades,
            "win_rate_pct": report_det.win_rate_pct,
            "profit_factor": report_det.profit_factor,
            "expectancy_per_trade": report_det.expectancy_per_trade,
            "max_drawdown_usdt": report_det.max_drawdown_usdt,
            "max_drawdown_pct": report_det.max_drawdown_pct,
            "total_fees_paid": report_det.total_fees_paid,
            "total_funding_paid": report_det.total_funding_paid,
            "per_symbol_summary": report_det.per_symbol_summary,
            "equity_curve": report_det.equity_curve,
        },
        "ml_filtered": {
            "initial_equity": report_ml.initial_equity,
            "final_equity": report_ml.final_equity,
            "total_net_pnl": report_ml.total_net_pnl,
            "return_on_capital_pct": report_ml.return_on_capital_pct,
            "total_trades": report_ml.total_trades,
            "win_rate_pct": report_ml.win_rate_pct,
            "profit_factor": report_ml.profit_factor,
            "expectancy_per_trade": report_ml.expectancy_per_trade,
            "max_drawdown_usdt": report_ml.max_drawdown_usdt,
            "max_drawdown_pct": report_ml.max_drawdown_pct,
            "total_fees_paid": report_ml.total_fees_paid,
            "total_funding_paid": report_ml.total_funding_paid,
            "per_symbol_summary": report_ml.per_symbol_summary,
            "equity_curve": report_ml.equity_curve,
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api.app:app", host=cfg.host, port=cfg.port, reload=False)

