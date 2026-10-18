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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.settings import ExecutionMode, ModelBConfig, get_default_config
from src.core.logger import logger
from src.core.types import Candle, CandidateStatus, Direction, EntryCategory, ExecutionStatus, MarketBias, RiskAssessment, SetupCandidate, StructureState
from src.data.database import DatabaseManager
from src.exchange.aster import AsterDexAdapter
from src.exchange.aster_v3 import AsterDexV3Adapter
from src.exchange.account_service import AsterAccountService, AccountConnectionStatus
from src.journal.repository import JournalRepository
from src.risk.manager import RiskManager
from src.risk.model_b_risk import ModelBRiskManager
from src.scheduler.model_b_runner import ModelBRunner
from src.scheduler.runner import ScannerRunner, rank_candidates
from src.backtest.engine import BacktestEngine
from src.ml.models import ModelRegistry
from src.execution.events import mask_webhook_url, validate_webhook_url
from src.alerts.models import (
    AlertAcknowledgeRequest,
    AlertCreateRequest,
    AlertDirection,
    AlertEvent,
    AlertSource,
    AlertStatus,
    AlertType,
    AlertUpdateRequest,
    PriceAlert,
    SymbolCatalogItem,
)
from src.alerts.storage import AlertStorage
from src.alerts.market_data import AlertMarketDataService
from src.alerts.discord import AlertDiscordNotifier
from src.alerts.engine import PriceAlertEngine
from src.intelligence.position_analyzer import (
    PositionAnalyzer,
    SinglePositionAnalysis,
    PortfolioAnalysis,
)
from src.intelligence.liquidation_heatmap import (
    LiquidationHeatmapEngine,
    LiquidationHeatmap,
)
from src.intelligence.whale_detector import (
    WhaleDetector,
    WhaleActivitySummary,
)
from src.intelligence.market_context import (
    MarketContextService,
    UnifiedMarketContext,
)
from src.intelligence.liquidity_chart_service import LiquidityChartService



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

# Least-privilege read-only authenticated account service for verified balance tracking
account_service: Optional[AsterAccountService] = None
try:
    _v3_user = os.getenv("ASTER_USER_ADDRESS")
    _v3_signer = os.getenv("ASTER_SIGNER_ADDRESS")
    _v3_key = os.getenv("ASTER_SIGNER_PRIVATE_KEY")
    _v3_chain = int(os.getenv("ASTER_CHAIN_ID", 1666))
    if _v3_user and _v3_signer and _v3_key:
        _v3_acc_adapter = AsterDexV3Adapter(
            base_url=cfg.exchange.base_url,
            user_address=_v3_user,
            signer_address=_v3_signer,
            signer_private_key=_v3_key,
            chain_id=_v3_chain,
            is_testnet=False,
            request_timeout=cfg.exchange.request_timeout,
            live_trading_locked=True,
        )
        account_service = AsterAccountService(
            adapter=_v3_acc_adapter,
            cache_ttl_sec=15.0,
            stale_threshold_sec=120.0,
        )
    else:
        account_service = AsterAccountService(adapter=None)
except Exception as _acc_err:
    logger.warning(f"Failed to initialize AsterAccountService: {_acc_err}")
    account_service = AsterAccountService(adapter=None)
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

# Universal AsterDEX Perpetual Price Alerts
alert_storage = AlertStorage(db)
alert_market_data = AlertMarketDataService(
    base_url=cfg.exchange.base_url,
    poll_interval_sec=4.0,
    stale_threshold_sec=60.0,
    catalog_refresh_interval_sec=1800.0,
)
alert_discord = AlertDiscordNotifier(
    webhook_url=getattr(cfg, "discord_price_alert_webhook_url", None),
    storage=alert_storage,
    request_timeout=8.0,
    max_retries=3,
)
alert_engine = PriceAlertEngine(
    storage=alert_storage,
    market_data=alert_market_data,
    discord_notifier=alert_discord,
    cooldown_sec=60.0,
)

# Model B Aggressive Multi-Exchange Liquidity Research Engine
model_b_config: ModelBConfig = getattr(cfg, "model_b", None) or ModelBConfig()
model_b_risk_mgr = ModelBRiskManager(config=model_b_config)
model_b_runner = ModelBRunner(
    db=db,
    journal=journal,
    exchange=exchange,
    config=model_b_config,
    paper_config=cfg.paper,
    risk_manager=model_b_risk_mgr,
)

# AsterDEX Position Intelligence & Market Liquidity Context Service
market_context_service = MarketContextService()
liquidity_chart_service = LiquidityChartService(adapter=market_context_service.adapter)

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
        account_service=account_service,
        tier_config=cfg.risk.equity_tier,
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

    # Start universal perpetual price alerts market data service
    try:
        await alert_market_data.start()
        logger.info("Universal price alerts market data service started.")
        if alert_discord.enabled:
            logger.info(f"Price Alerts Discord notifications: ENABLED ({alert_discord.mask_url()})")
        else:
            logger.info("Price Alerts Discord notifications: DISABLED (no webhook configured)")
    except Exception as e:
        logger.warning(f"Failed to start price alerts market data service: {e}")

    # Start Model B background research loop if enabled
    if getattr(model_b_config, "enabled", False):
        try:
            model_b_runner.start_background_loop(interval_seconds=60.0)
            logger.info("Model B background research loop started.")
        except Exception as e:
            logger.warning(f"Failed to start Model B runner: {e}")

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

    # Stop Model B runner
    try:
        model_b_runner.stop()
        logger.info("Model B runner stopped.")
    except Exception as e:
        logger.warning(f"Error stopping Model B runner: {e}")

    # Stop price alerts market data service
    try:
        await alert_market_data.stop()
    except Exception as e:
        logger.warning(f"Error stopping price alerts market data service: {e}")


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

# Mount static assets directory
STATIC_DIR = Path(__file__).parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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

    # Fetch live account snapshot from read-only account service
    account_snapshot = account_service.get_snapshot() if account_service else None
    account_data = account_snapshot.to_dict() if account_snapshot else {
        "status": AccountConnectionStatus.ACCOUNT_DATA_UNAVAILABLE.value,
        "is_connected": False,
        "is_stale": True,
        "total_equity_usdt": None,
        "wallet_balance_usdt": None,
        "available_balance_usdt": None,
        "used_margin_usdt": None,
        "unrealized_pnl_usdt": None,
        "open_positions_count": 0,
        "last_sync_utc": None,
        "last_sync_age_seconds": None,
        "source": "ASTERDEX_V3_REST",
        "error_message": "Account service not initialized.",
        "account_alias": None,
        "positions_summary": [],
    }

    # Resolve active equity tier
    active_equity_for_tier = (
        account_snapshot.total_equity_usdt
        if (account_snapshot and account_snapshot.status == AccountConnectionStatus.CONNECTED and account_snapshot.total_equity_usdt is not None and account_snapshot.total_equity_usdt > 0)
        else risk_mgr.equity_usdt
    )
    from src.risk.tier import resolve_equity_tier
    tier_res = resolve_equity_tier(active_equity_for_tier, cfg.risk.equity_tier)

    return {
        "app_name": cfg.app_name,
        "version": cfg.version,
        "mode": cfg.mode.value,
        "live_trading_locked": cfg.live_trading_locked,
        "account": account_data,
        "capital": {
            "initial_equity_usdt": cfg.risk.initial_equity_usdt,
            "current_equity_usdt": round(risk_mgr.equity_usdt, 4),
            "verified_live_equity_usdt": account_snapshot.total_equity_usdt if (account_snapshot and account_snapshot.status == AccountConnectionStatus.CONNECTED) else None,
            "margin_mode": cfg.risk.margin_mode,
            "max_leverage": cfg.risk.max_leverage,
            "active_tier": {
                "tier_id": tier_res.tier_id,
                "tier_name": tier_res.tier_name,
                "target_margin_min": tier_res.target_margin_min,
                "target_margin_max": tier_res.target_margin_max,
                "is_trading_allowed": tier_res.is_trading_allowed,
                "rejection_reason": tier_res.rejection_reason,
            },
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


@app.get("/api/account")
def get_account_status() -> Dict[str, Any]:
    """Return verified live Aster DEX account snapshot with status and metrics."""
    if not account_service:
        return {
            "status": AccountConnectionStatus.ACCOUNT_DATA_UNAVAILABLE.value,
            "is_connected": False,
            "error_message": "Account service not initialized.",
        }
    return account_service.get_snapshot().to_dict()


@app.get("/api/account/positions")
def get_account_positions() -> Dict[str, Any]:
    """Return verified live Aster DEX open positions with detailed risk metrics and order brackets."""
    if not account_service:
        return {
            "status": AccountConnectionStatus.ACCOUNT_DATA_UNAVAILABLE.value,
            "is_connected": False,
            "open_positions_count": 0,
            "positions": [],
            "error_message": "Account service not initialized.",
            "last_sync_utc": None,
        }
    snapshot_dict = account_service.get_snapshot().to_dict()
    return {
        "status": snapshot_dict.get("status"),
        "is_connected": snapshot_dict.get("is_connected", False),
        "is_stale": snapshot_dict.get("is_stale", False),
        "open_positions_count": snapshot_dict.get("open_positions_count", 0),
        "positions": snapshot_dict.get("positions_summary", []),
        "last_sync_utc": snapshot_dict.get("last_sync_utc"),
        "last_sync_age_seconds": snapshot_dict.get("last_sync_age_seconds"),
        "source": snapshot_dict.get("source"),
        "error_message": snapshot_dict.get("error_message"),
    }


@app.post("/api/account/sync")
def sync_account_status() -> Dict[str, Any]:
    """Force refresh of live Aster DEX account snapshot."""
    if not account_service:
        return {
            "status": AccountConnectionStatus.ACCOUNT_DATA_UNAVAILABLE.value,
            "is_connected": False,
            "error_message": "Account service not initialized.",
        }
    return account_service.sync().to_dict()


# =========================================================================
# AsterDEX Position Intelligence & Market Liquidity Endpoints (Read-Only)
# =========================================================================

class PositionAnalysisPayload(BaseModel):
    symbol: Optional[str] = None
    position: Optional[Dict[str, Any]] = None


@app.post("/api/intelligence/analyze_position")
async def analyze_position_endpoint(payload: PositionAnalysisPayload) -> Dict[str, Any]:
    """Perform multi-timeframe structural audit, thesis validation, fee/slippage risk, and conditional recommendation."""
    target_pos = payload.position
    sym = (payload.symbol or "").strip().upper()

    snapshot = account_service.get_snapshot() if account_service else None
    equity = snapshot.total_equity_usdt if snapshot else None

    # If position not provided in payload, look up from live account positions
    if not target_pos:
        if not sym:
            raise HTTPException(status_code=400, detail="Either 'symbol' or 'position' must be provided.")
        if snapshot and snapshot.positions:
            for p in snapshot.positions:
                if str(p.get("symbol", "")).upper() == sym:
                    target_pos = p
                    break
        if not target_pos and snapshot:
            for p in snapshot.to_dict().get("positions_summary", []):
                if str(p.get("symbol", "")).upper() == sym:
                    target_pos = p
                    break

    if not target_pos:
        # Create an observation position for the requested symbol
        prem = market_context_service.adapter.get_premium_index(sym)
        mark_px = float(prem.get("mark_price", 0.0)) if prem else 0.0
        target_pos = {
            "symbol": sym,
            "side": "LONG",
            "position_amt": 0.0,
            "entry_price": mark_px,
            "mark_price": mark_px,
            "margin_mode": "ISOLATED",
            "allocated_margin_usdt": 0.0,
        }

    pos_sym = str(target_pos.get("symbol", sym)).upper()
    candles_4h = market_context_service.adapter.get_klines(pos_sym, "4h", limit=40)
    candles_1h = market_context_service.adapter.get_klines(pos_sym, "1h", limit=50)
    candles_15m = market_context_service.adapter.get_klines(pos_sym, "15m", limit=50)

    analysis = PositionAnalyzer.analyze_position(
        position=target_pos,
        candles_4h=candles_4h,
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        account_equity=equity,
    )
    return analysis.to_dict()


@app.get("/api/intelligence/analyze_portfolio")
@app.post("/api/intelligence/analyze_portfolio")
async def analyze_portfolio_endpoint() -> Dict[str, Any]:
    """Perform portfolio-level risk aggregation, net directional exposure, and position thesis synthesis."""
    snapshot = account_service.get_snapshot() if account_service else None
    positions = snapshot.positions if snapshot else []
    equity = snapshot.total_equity_usdt if snapshot else None
    avail = snapshot.available_balance_usdt if snapshot else None

    def _candle_prov(s: str, tf: str) -> List[Candle]:
        return market_context_service.adapter.get_klines(s, tf, limit=40)

    port_analysis = PositionAnalyzer.analyze_portfolio(
        positions=positions,
        account_equity=equity,
        available_balance=avail,
        candle_provider=_candle_prov,
    )
    return port_analysis.to_dict()


@app.get("/api/intelligence/liquidation_heatmap")
async def liquidation_heatmap_endpoint(symbol: str = Query(..., description="Perpetual symbol e.g. BTCUSDT")) -> Dict[str, Any]:
    """Generate Long vs Short estimated liquidation concentration bands."""
    sym = symbol.strip().upper().replace(".P", "")
    prem = market_context_service.adapter.get_premium_index(sym)
    mark_px = float(prem.get("mark_price", 0.0)) if prem else 0.0
    funding = float(prem.get("last_funding_rate", 0.0001)) if prem else 0.0001
    oi_data = market_context_service.adapter.get_open_interest(sym)
    oi_contracts = float(oi_data.get("open_interest", 0.0)) if oi_data else None
    candles_1h = market_context_service.adapter.get_klines(sym, "1h", limit=40)

    heatmap = LiquidationHeatmapEngine.generate_heatmap(
        symbol=sym,
        mark_price=mark_px,
        open_interest_contracts=oi_contracts,
        funding_rate=funding,
        candles_1h=candles_1h,
    )
    return heatmap.to_dict()


@app.get("/api/intelligence/whale_activity")
async def whale_activity_endpoint(symbol: str = Query(..., description="Perpetual symbol e.g. BTCUSDT")) -> Dict[str, Any]:
    """Inspect order book and recent trades for institutional resting depth, aggressive executions, and spoofing."""
    sym = symbol.strip().upper().replace(".P", "")
    book = market_context_service.adapter.get_order_book(sym, limit=50)
    trades = market_context_service.adapter.get_recent_trades(sym, limit=100)
    oi_data = market_context_service.adapter.get_open_interest(sym)
    oi_contracts = float(oi_data.get("open_interest", 0.0)) if oi_data else None
    prem = market_context_service.adapter.get_premium_index(sym)
    mark_px = float(prem.get("mark_price", 0.0)) if prem else (book.mid_price if book else 0.0)

    summary = market_context_service.whale_detector.analyze_market_data(
        symbol=sym,
        book=book,
        trades=trades,
        current_oi_contracts=oi_contracts,
        mark_price=mark_px,
    )
    return summary.to_dict()


@app.get("/api/intelligence/market_context")
async def market_context_endpoint(
    symbol: str = Query(..., description="Perpetual symbol e.g. BTCUSDT"),
    force_refresh: bool = Query(False, description="Bypass cache and force fresh query"),
) -> Dict[str, Any]:
    """Retrieve unified market context synthesizing MTF structure, depth imbalance, CVD, liquidations, and whale activity."""
    sym = symbol.strip().upper().replace(".P", "")
    ctx = market_context_service.get_market_context(sym, force_refresh=force_refresh)
    return ctx.to_dict()


# =========================================================================
# Feature E: Interactive Liquidity Sweep & Reclaim Chart Endpoints (Read-Only)
# =========================================================================

@app.get("/api/chart/klines")
async def chart_klines_endpoint(
    symbol: str = Query(..., description="Perpetual symbol e.g. BTCUSDT"),
    timeframe: str = Query("1h", description="Candle timeframe e.g. 15m, 1h, 4h"),
    limit: int = Query(200, ge=10, le=500, description="Number of candles"),
    start_time_ms: Optional[int] = Query(None, description="Start time in ms"),
    end_time_ms: Optional[int] = Query(None, description="End time in ms"),
) -> List[Dict[str, Any]]:
    """Retrieve OHLCV candles formatted for TradingView Lightweight Charts."""
    return liquidity_chart_service.get_chart_klines(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
    )


@app.get("/api/chart/liquidity_layers")
async def chart_liquidity_layers_endpoint(
    symbol: str = Query(..., description="Perpetual symbol e.g. BTCUSDT"),
    timeframe: str = Query("1h", description="Candle timeframe e.g. 15m, 1h, 4h"),
    limit: int = Query(150, ge=20, le=400, description="Number of candles"),
    as_of_index: Optional[int] = Query(None, description="Historical replay bar index limit"),
    as_of_timestamp_ms: Optional[int] = Query(None, description="Historical replay timestamp cutoff"),
) -> Dict[str, Any]:
    """Retrieve complete liquidity layers: structural levels, sweep/reclaim events, order-book depth, and liquidation estimates."""
    return liquidity_chart_service.get_liquidity_layers(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        as_of_index=as_of_index,
        as_of_timestamp_ms=as_of_timestamp_ms,
    )


@app.get("/api/chart/symbols")
async def chart_symbols_endpoint() -> Dict[str, Any]:
    """Retrieve eligible perpetual symbols available for charting."""
    catalog = liquidity_chart_service.adapter.discover_instruments()
    symbols = [inst.symbol for inst in catalog if inst.is_active] if catalog else []
    if not symbols:
        symbols = list(cfg.watchlist)
    priority = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AGTUSDT", "BNBUSDT", "DOGEUSDT"]
    ordered_symbols = [s for s in priority if s in symbols] + [s for s in symbols if s not in priority]
    return {
        "symbols": ordered_symbols,
        "default_symbol": ordered_symbols[0] if ordered_symbols else "BTCUSDT",
        "default_timeframe": "1h",
        "supported_timeframes": ["15m", "1h", "4h"],
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
def get_journal(
    limit: int = 50,
    model_id: Optional[str] = Query(None, description="Filter by model_id: MODEL_A, MODEL_B, or ALL"),
) -> Dict[str, Any]:
    """Retrieve historical setup candidates and trade executions with performance summary."""
    m_id = None if (not model_id or model_id.strip().upper() == "ALL") else model_id.strip().upper()
    setups = journal.get_latest_setups(limit=limit, model_id=m_id)
    trades = journal.get_trades(limit=limit, model_id=m_id)
    metrics = journal.compute_trade_metrics(trades)
    return {
        "model_id": m_id or "ALL",
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
    model_id: Optional[str] = Query(None, description="Filter by model_id: MODEL_A, MODEL_B, or ALL"),
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

    target_model_id = None if (not model_id or model_id.strip().upper() == "ALL") else model_id.strip().upper()

    trades = journal.get_trades(
        symbol=symbol,
        direction=direction,
        entry_category=category,
        outcome=outcome,
        open_only=open_only,
        start_time_ms=start_time_ms,
        limit=limit,
        model_id=target_model_id,
    )
    all_recorded_trades = journal.get_trades(limit=1000, model_id=target_model_id)
    overall_metrics = journal.compute_trade_metrics(all_recorded_trades)
    filtered_metrics = journal.compute_trade_metrics(trades)

    # Compute authoritative per-model metrics
    model_a_trades = journal.get_trades(limit=1000, model_id="MODEL_A")
    model_b_trades = journal.get_trades(limit=1000, model_id="MODEL_B")
    per_model_metrics = {
        "MODEL_A": journal.compute_trade_metrics(model_a_trades),
        "MODEL_B": journal.compute_trade_metrics(model_b_trades),
    }

    orders = journal.get_paper_orders(symbol=symbol, model_id=target_model_id)
    lifecycle_events = journal.get_lifecycle_events(symbol=symbol, limit=limit, model_id=target_model_id)
    forward_cohort = journal.compute_forward_validation_cohort()
    return {
        "model_id": target_model_id or "ALL",
        "count": len(trades),
        "trades": trades,
        "metrics": overall_metrics,
        "filtered_metrics": filtered_metrics,
        "per_model_metrics": per_model_metrics,
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


# =========================================================================
# Universal AsterDEX Perpetual Price Alerts REST Endpoints
# =========================================================================

@app.get("/api/alerts/symbols")
def get_alert_symbols(q: Optional[str] = Query(default=None, description="Symbol or asset search filter")):
    """
    Return all discovered eligible USDT perpetual symbols (~578 contracts).
    Provides symbol, base asset, precisions, tick size, current mark/last prices, and freshness.
    """
    symbols = alert_market_data.get_all_symbols()
    if q:
        query = q.strip().upper()
        symbols = [s for s in symbols if query in s.symbol or query in s.base_asset.upper()]
    return [s.model_dump() for s in symbols]


@app.get("/api/alerts")
def list_alerts(
    symbol: Optional[str] = Query(default=None, description="Filter by symbol"),
    is_active: Optional[bool] = Query(default=None, description="Filter by active state"),
):
    """List all configured price alerts with live enriched pricing and distances."""
    alerts = alert_engine.list_alerts(symbol=symbol, is_active=is_active)
    return [a.model_dump() for a in alerts]


@app.post("/api/alerts", dependencies=[Depends(require_mutating_api_access)])
def create_alert(req: AlertCreateRequest):
    """
    Create a new persistent price alert.
    Validates symbol against AsterDEX universe and target price positivity.
    """
    symbol_clean = req.symbol.upper().strip()
    catalog_item = alert_market_data.get_symbol_catalog_item(symbol_clean)
    if not catalog_item and len(alert_market_data._catalog) > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Symbol '{symbol_clean}' is not an active AsterDEX USDT perpetual contract.",
        )
    if req.target_price <= 0:
        raise HTTPException(status_code=400, detail="Target price must be greater than zero.")

    alert = alert_engine.create_alert(req)
    return alert.model_dump()


@app.get("/api/alerts/status")
def get_alert_status():
    """Return health telemetry for market data service and alert engine."""
    all_alerts = alert_engine.list_alerts()
    active_count = sum(1 for a in all_alerts if a.is_active)
    triggered_count = sum(1 for a in all_alerts if a.status == AlertStatus.TRIGGERED)
    stale_count = sum(1 for a in all_alerts if a.status == AlertStatus.STALE)
    events = alert_engine.get_recent_events(limit=100)
    unack_count = sum(1 for e in events if not e.acknowledged)

    return {
        "market_data": alert_market_data.get_status(),
        "discord": alert_discord.get_status(),
        "total_alerts": len(all_alerts),
        "active_alerts": active_count,
        "triggered_alerts": triggered_count,
        "stale_alerts": stale_count,
        "unacknowledged_events": unack_count,
    }


@app.post("/api/alerts/discord/test", dependencies=[Depends(require_mutating_api_access)])
def test_price_alert_discord():
    """Test Discord price alert webhook delivery with mock payload without leaking credentials."""
    if not alert_discord.enabled:
        raise HTTPException(
            status_code=400,
            detail="Discord price alert webhook is disabled or not configured.",
        )
    test_event = AlertEvent(
        alert_id="test_alert_id",
        symbol="BTCUSDT",
        target_price=68500.0,
        trigger_price=68520.0,
        direction=AlertDirection.PRICE_ABOVE,
        price_source=AlertSource.MARK_PRICE,
        triggered_at=datetime.now(timezone.utc).isoformat(),
        note="Manual test notification from dashboard",
        acknowledged=False,
    )
    success = alert_discord.send_notification(test_event)
    return {
        "success": success,
        "webhook_masked": alert_discord.mask_url(),
        "delivered_at": datetime.now(timezone.utc).isoformat() if success else None,
    }


@app.get("/api/alerts/history")
def get_alert_history(
    limit: int = Query(default=50, ge=1, le=500),
    unack_only: bool = Query(default=False),
):
    """Retrieve historical triggered alert events."""
    events = alert_storage.list_events(limit=limit, unack_only=unack_only)
    return [e.model_dump() for e in events]


@app.post("/api/alerts/history/ack", dependencies=[Depends(require_mutating_api_access)])
def acknowledge_alert_events(req: AlertAcknowledgeRequest):
    """Mark alert events as acknowledged."""
    count = alert_engine.acknowledge_events(req.event_ids)
    return {"acknowledged_count": count}


@app.delete("/api/alerts/history", dependencies=[Depends(require_mutating_api_access)])
def clear_alert_history():
    """Clear all historical triggered alert events."""
    count = alert_engine.clear_events()
    return {"cleared_count": count}


@app.get("/api/alerts/{alert_id}")
def get_alert(alert_id: str):
    """Retrieve a single price alert by ID."""
    alert = alert_engine.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return alert.model_dump()


@app.put("/api/alerts/{alert_id}", dependencies=[Depends(require_mutating_api_access)])
def update_alert(alert_id: str, req: AlertUpdateRequest):
    """Update an existing price alert's parameters."""
    if req.target_price is not None and req.target_price <= 0:
        raise HTTPException(status_code=400, detail="Target price must be greater than zero.")
    alert = alert_engine.update_alert(alert_id, req)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return alert.model_dump()


@app.patch("/api/alerts/{alert_id}/toggle", dependencies=[Depends(require_mutating_api_access)])
def toggle_alert(alert_id: str):
    """Toggle an alert between active and disabled."""
    alert = alert_engine.toggle_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return alert.model_dump()


@app.delete("/api/alerts/{alert_id}", dependencies=[Depends(require_mutating_api_access)])
def delete_alert(alert_id: str):
    """Delete a price alert and its associated events."""
    deleted = alert_engine.delete_alert(alert_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return {"deleted": True, "alert_id": alert_id}


# =====================================================================
# Model B — Aggressive Liquidity Research API Endpoints
# =====================================================================

@app.get("/api/model_b/status")
def get_model_b_status():
    """Retrieve runtime status, portfolio capital, and risk meters for Model B."""
    return model_b_runner.get_status()


@app.post("/api/model_b/scan")
async def trigger_model_b_scan():
    """Trigger an immediate 1H scan cycle across universal perpetual markets for Model B."""
    candidates = await asyncio.to_thread(model_b_runner.run_scan_cycle)
    ready_count = sum(1 for c in candidates if c.status == CandidateStatus.READY)
    return {
        "status": model_b_runner.last_scan_status,
        "scan_count": model_b_runner.scan_count,
        "total_evaluated": len(candidates),
        "ready_count": ready_count,
        "candidates": [c.to_dict() for c in candidates[:20]],
    }


@app.get("/api/model_b/setups")
def get_model_b_setups(limit: int = Query(50, ge=1, le=200)):
    """Retrieve latest Model B setup candidates from database enriched with execution state, structured reasons, and metrics."""
    setups = journal.get_latest_setups(limit=limit, model_id="MODEL_B")
    all_orders = journal.get_paper_orders(model_id="MODEL_B")
    all_trades = journal.get_trades(open_only=False, limit=1000, model_id="MODEL_B")
    
    enriched_setups = []
    for s in setups:
        item = dict(s)
        cid = item.get("candidate_id")
        sym = item.get("symbol")
        details = item.get("details") or {}
        ai = details.get("ai_analysis") or {}
        risk = details.get("risk_assessment") or {}

        # Enrich metrics and risk sizing for frontend display
        item["metadata"] = ai
        item["risk_assessment"] = risk
        item["spread_bps"] = ai.get("spread_bps")
        item["imbalance_25bps"] = ai.get("imbalance_25bps")
        item["cvd_1h"] = ai.get("cvd_1h")
        item["quantity"] = risk.get("quantity")
        item["risk_budget_usdt"] = risk.get("risk_budget_usdt", 1.0)
        
        # Check active open positions
        open_pos = model_b_runner.paper_engine.open_positions.get(sym) if model_b_runner else None
        
        # Check active pending limit orders
        pending_order = None
        if model_b_runner:
            pending_order = next((o for o in model_b_runner.paper_engine.pending_orders.values() if o.candidate_id == cid or o.symbol == sym), None)
            
        # Check historical orders for this candidate specifically
        exact_orders = [o for o in all_orders if o.get("candidate_id") == cid]
        latest_exact_order = exact_orders[0] if exact_orders else None
        
        # Determine truthful execution state and structured reasons
        execution_state = "NOT_SUBMITTED"
        order_id = None
        order_status = None
        order_price = None
        order_created_at = None
        rejection_reason = None
        not_submitted_reason = None
        
        if open_pos and open_pos.setup_id == cid:
            execution_state = "OPEN"
            order_id = open_pos.order_id
            order_status = "FILLED"
            order_price = open_pos.entry_price
            order_created_at = datetime.fromtimestamp(open_pos.entry_timestamp_ms / 1000.0, tz=timezone.utc).isoformat()
        elif pending_order and pending_order.candidate_id == cid:
            execution_state = "ENTRY_PENDING"
            order_id = pending_order.order_id
            order_status = "PENDING"
            order_price = pending_order.price
            order_created_at = pending_order.created_at_utc
        elif latest_exact_order:
            st = (latest_exact_order.get("status") or "").upper()
            order_id = latest_exact_order.get("order_id")
            order_status = st
            order_price = latest_exact_order.get("price")
            order_created_at = latest_exact_order.get("created_at_utc")
            if st == "PENDING":
                execution_state = "ENTRY_PENDING"
            elif st == "FILLED":
                # Check if position is currently open or closed
                open_trade = next((t for t in all_trades if t.get("setup_id") == cid and t.get("is_open")), None)
                execution_state = "OPEN" if open_trade else "CLOSED"
            elif st == "REJECTED":
                execution_state = "REJECTED"
                rejection_reason = latest_exact_order.get("rejection_reason")
            elif st == "EXPIRED":
                execution_state = "EXPIRED"
            elif st == "INVALIDATED":
                execution_state = "CANCELLED"
            else:
                execution_state = st or "NOT_SUBMITTED"
        else:
            # Reconstruct from candidate details or explain why NOT_SUBMITTED
            details_exec = details.get("execution_status")
            if details_exec and details_exec not in ("NOT_SUBMITTED", None):
                execution_state = details_exec
                order_id = details.get("order_id")
            elif open_pos and open_pos.symbol == sym:
                execution_state = "NOT_SUBMITTED"
                not_submitted_reason = f"Submission suppressed by deduplication: Active position already open on {sym}"
            elif pending_order and pending_order.symbol == sym:
                execution_state = "NOT_SUBMITTED"
                not_submitted_reason = f"Submission suppressed by deduplication: Active pending order already exists on {sym}"
            elif item.get("status") == "READY":
                execution_state = "NOT_SUBMITTED"
                if details.get("rejection_reasons"):
                    not_submitted_reason = "; ".join(details["rejection_reasons"])
                else:
                    not_submitted_reason = "Waiting for entry trigger"
            else:
                execution_state = "NOT_SUBMITTED"
                rej_list = details.get("rejection_reasons") or item.get("rejection_reasons") or []
                not_submitted_reason = "; ".join(rej_list) if rej_list else "Qualification criteria not met"

        if not rejection_reason and details.get("rejection_reasons"):
            rejection_reason = "; ".join(details["rejection_reasons"])
        if not not_submitted_reason and execution_state == "NOT_SUBMITTED":
            not_submitted_reason = rejection_reason or "Waiting for entry trigger"

        item["execution_state"] = execution_state
        item["order_id"] = order_id
        item["order_status"] = order_status
        item["order_price"] = order_price
        item["order_created_at"] = order_created_at
        if rejection_reason:
            item["rejection_reason"] = rejection_reason
        if not_submitted_reason:
            item["not_submitted_reason"] = not_submitted_reason
            
        enriched_setups.append(item)

    return {"setups": enriched_setups, "count": len(enriched_setups), "model_id": "MODEL_B"}



@app.get("/api/model_b/orders")
def get_model_b_orders(
    symbol: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Retrieve simulated paper orders isolated to Model B with status and symbol filtering."""
    orders = journal.get_paper_orders(
        symbol=symbol,
        status=status,
        model_id="MODEL_B",
    )
    pending_count = len(model_b_runner.paper_engine.pending_orders) if model_b_runner else 0
    return {
        "model_id": "MODEL_B",
        "orders": orders[:limit],
        "count": len(orders),
        "pending_count": pending_count,
    }


@app.get("/api/model_b/journal")
def get_model_b_journal(
    outcome: Optional[str] = Query(None),
    open_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
):
    """Retrieve simulated paper trading journal, metrics, pending orders, and lifecycle events for Model B."""
    trades = journal.get_trades(
        outcome=outcome,
        open_only=open_only,
        limit=limit,
        model_id="MODEL_B",
    )
    all_model_b_trades = journal.get_trades(open_only=False, limit=5000, model_id="MODEL_B")
    metrics = journal.compute_trade_metrics(all_model_b_trades)
    
    pending_orders = [o.to_dict() for o in model_b_runner.paper_engine.pending_orders.values()] if model_b_runner else []
    all_orders = journal.get_paper_orders(model_id="MODEL_B")
    lifecycle_events = journal.get_lifecycle_events(limit=limit, model_id="MODEL_B")
    
    return {
        "model_id": "MODEL_B",
        "trades": trades,
        "metrics": metrics,
        "total_count": len(trades),
        "pending_orders": pending_orders,
        "pending_orders_count": len(pending_orders),
        "paper_orders": all_orders[:limit],
        "paper_orders_count": len(all_orders),
        "lifecycle_events": lifecycle_events[:limit],
        "lifecycle_events_count": len(lifecycle_events),
    }


@app.get("/api/model_b/coverage")
def get_model_b_coverage():
    """Retrieve multi-exchange perpetual coverage matrix and discovery status."""
    if not model_b_runner.discovered_universe:
        model_b_runner.discover_universe()

    universe = model_b_runner.discovered_universe
    summary = {
        ex: {
            "count": len(insts),
            "sample_symbols": [inst.symbol for inst in insts[:5]],
            "status": "CONNECTED" if len(insts) > 0 else "EMPTY",
        }
        for ex, insts in universe.items()
    }
    return {
        "model_id": "MODEL_B",
        "total_discovered": sum(len(v) for v in universe.values()),
        "venues": summary,
        "verified_venues": ["asterdex", "binance", "hyperliquid"],
        "planned_venues": ["okx", "bybit"],
    }


@app.get("/api/model_b/orderbook/{symbol}")
def get_model_b_orderbook(symbol: str):
    """Fetch live multi-venue Level-2 order book depth for a symbol."""
    books = model_b_runner.exchange_service.get_aggregated_order_books(symbol)
    result: Dict[str, Any] = {}
    for ex, b in books.items():
        if b is not None:
            result[ex] = b.to_dict()
        else:
            result[ex] = None
    return {"symbol": symbol, "books": result}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api.app:app", host=cfg.host, port=cfg.port, reload=False)


