"""FastAPI Application and REST API Endpoints.

Provides endpoints for scanner status, watchlist controls, setup cards,
backtest comparisons, and signal journal. Serves the interactive dashboard.
"""

import asyncio
from dataclasses import asdict
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
from src.research_data.storage import ResearchDataStore
from src.research_data.scheduler import ResearchDataScheduler
from src.research_data.audit import audit_dataset
from src.research_data.retail_positioning import RetailPositioningProvider
from src.exchange.aster import AsterDexAdapter
from src.exchange.aster_v3 import AsterDexV3Adapter
from src.exchange.account_service import AsterAccountService, AccountConnectionStatus
from src.journal.repository import JournalRepository
from src.risk.manager import RiskManager
from src.risk.model_b_risk import ModelBRiskManager
from src.scheduler.model_b_runner import ModelBRunner
from src.risk.model_b_diagnostic import resolve_override_state
from src.research.model_b_diagnostics import build_report as build_model_b_diagnostic_report
from src.scheduler.runner import ScannerRunner, rank_candidates
from src.strategy.generator import SetupGenerator
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
from src.intelligence.whale_ranking import WhaleRankingEngine
from src.research import (
    ModelBShadowEvaluator,
    ResearchStorage,
    ResearchVariant,
    VARIANT_METADATA,
    VariantRuleEngine,
)
from src.exchange.aster_adapter import AsterAdapter
from src.demand_zone.scanner import AsterDexDemandZoneScanner
from src.demand_zone.storage import DemandZoneStorage
from src.demand_zone.types import (
    BTCZonePolicy,
    DemandZoneCandidateStatus,
    DemandZoneConfig,
)
from src.screener import (
    CrossExchangeMarketAsset,
    MultiExchangeMarketScreener,
    ScreenerFilterParams,
    VenueMarketMetrics,
)
from src.ai_analyst import (
    AIAnalystConfig,
    AIAnalystService,
    AIAnalystStorage,
    AnalysisRequest,
    AIConfigUpdateRequest,
)
from src.model_d import ModelDCollector
from src.model_d.binance import BinanceModelDCollector
from src.model_d.features import build_features
from src.model_d.ledger import ModelDShadowLedger
from src.model_d.bots import BotConfig, PersistentBotStore, default_bots
from src.model_d.universe import ModelDUniverse
from src.model_d.scanner import ModelDScanWorker, ModelDExitMonitor
from src.model_d.wallets import WalletRegistry, JsonRpcProvider, WalletPipelineWorker
import sqlite3



# App state containers
cfg = get_default_config()
db = DatabaseManager(cfg.db_path)
research_store = ResearchDataStore(db)
research_scheduler: Optional[ResearchDataScheduler] = None
retail_positioning_provider = RetailPositioningProvider()
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
# Diagnostic override is honored only in PAPER/RESEARCH with live trading locked (Model B scope only).
model_b_diagnostic_state = resolve_override_state(cfg)
model_b_risk_mgr = ModelBRiskManager(config=model_b_config)
model_b_risk_mgr.diagnostic_state = model_b_diagnostic_state
model_b_runner = ModelBRunner(
    db=db,
    journal=journal,
    exchange=exchange,
    config=model_b_config,
    paper_config=cfg.paper,
    risk_manager=model_b_risk_mgr,
)

# Model D is read-only and independently scoped. It is not started as a background
# executor here; collection is explicit and paper research never reaches order APIs.
model_d_config = cfg.model_d
model_d_aster_reference = AsterAdapter(base_url=cfg.exchange.base_url)
model_d_collector = (BinanceModelDCollector(config=model_d_config, reference_adapter=model_d_aster_reference)
                     if model_d_config.market_primary_venue == "binance"
                     else ModelDCollector(adapter=model_d_aster_reference, config=model_d_config))
model_d_ledger = ModelDShadowLedger(
    fee_rate=cfg.exchange.taker_fee_rate,
    slippage_rate=cfg.exchange.slippage_rate,
)
model_d_wallet_registry = WalletRegistry(db)
model_d_wallet_providers = {
    int(chain_id): JsonRpcProvider(int(chain_id), url)
    for chain_id, url in model_d_config.wallet_rpc_urls.items() if url
}
model_d_wallet_pipeline = WalletPipelineWorker(
    model_d_wallet_registry, model_d_wallet_providers,
    interval_seconds=model_d_config.wallet_scan_interval_seconds,
    enrichment_enabled=model_d_config.wallet_enrichment_enabled,
    ranking_enabled=model_d_config.wallet_ranking_enabled,
    max_workers=model_d_config.wallet_max_concurrency,
    confirmation_depths=model_d_config.wallet_confirmation_depths,
)
model_d_universe = ModelDUniverse(model_d_aster_reference)
model_d_bot_store = PersistentBotStore(db)
model_d_bot_store.initialize([BotConfig("MODEL_D_BOT_15", 15.0), BotConfig("MODEL_D_BOT_25", 25.0), BotConfig("MODEL_D_BOT_100", 100.0)])
model_d_bots = model_d_bot_store.load()
model_d_scan_worker = ModelDScanWorker(db, model_d_universe, model_d_collector, model_d_config, bots=model_d_bots, bot_store=model_d_bot_store)
model_d_exit_monitor = ModelDExitMonitor(model_d_collector, model_d_bots, model_d_bot_store, interval_sec=getattr(model_d_config, "scan_interval_sec", 30))

# AsterDEX Position Intelligence & Market Liquidity Context Service
market_context_service = MarketContextService()
liquidity_chart_service = LiquidityChartService(adapter=market_context_service.adapter, db=db)
whale_ranking_engine = WhaleRankingEngine(
    exchange_service=model_b_runner.exchange_service,
    whale_detector=market_context_service.whale_detector,
    db=db,
)

# AsterDEX Demand-Zone LONG Scanner
demand_zone_storage = DemandZoneStorage(db)
demand_zone_scanner = AsterDexDemandZoneScanner(
    db=db,
    adapter=AsterAdapter(),
    storage=demand_zone_storage,
)

# Multi-Exchange Perpetual Futures Market Screener (Read-Only)
market_screener = MultiExchangeMarketScreener()

# Groq AI Trading Analyst (Logically isolated, advisory research only)
ai_analyst_storage = AIAnalystStorage(db)
ai_analyst_service = AIAnalystService(
    db=db,
    adapter=AsterAdapter(),
    storage=ai_analyst_storage,
)

specs_map: Dict[str, Any] = {}
metadata_status = "METADATA_UNAVAILABLE"
metadata_error: Optional[str] = None
metadata_initialized_utc: Optional[str] = None
metadata_lock = asyncio.Lock()
runner: Optional[ScannerRunner] = None
enabled_symbols: set = set(cfg.enabled_symbols)

# Concurrency and background execution controls
scan_lock = asyncio.Lock()
stop_event = asyncio.Event()
bg_task: Optional[asyncio.Task] = None
BACKGROUND_SCAN_ENABLED: bool = True
CANDLE_GRACE_PERIOD_SEC: int = 60


async def initialize_model_a_metadata() -> bool:
    """Load and validate Model A exchange metadata exactly once at a time."""
    global specs_map, metadata_status, metadata_error, metadata_initialized_utc
    async with metadata_lock:
        if specs_map:
            metadata_status = "READY"
            return True
        metadata_status = "METADATA_LOADING"
        metadata_error = None
        try:
            loaded = await asyncio.to_thread(exchange.get_exchange_info)
            required = set(cfg.watchlist)
            if not isinstance(loaded, dict) or not loaded or not required.issubset(set(loaded)):
                missing = sorted(required - set(loaded or {}))
                raise RuntimeError(f"Exchange metadata incomplete; missing symbols: {missing}")
            specs_map = {symbol: loaded[symbol] for symbol in required}
            metadata_initialized_utc = datetime.now(timezone.utc).isoformat()
            metadata_status = "READY"
            return True
        except Exception as exc:
            specs_map = {}
            metadata_error = str(exc)
            metadata_status = "METADATA_UNAVAILABLE"
            logger.warning(f"Model A metadata initialization failed: {exc}")
            return False

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
    global runner, specs_map, bg_task, stop_event, research_scheduler
    logger.info("Initializing scanner API server...")
    # Load metadata before Model A's worker. Failure is non-fatal to the API
    # and independent Model B services, but leaves Model A stopped.
    specs_map = {}
    await initialize_model_a_metadata()

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
    metadata_available = bool(specs_map)
    if metadata_available:
        bg_task = asyncio.create_task(background_scan_worker())
        logger.info("Background scanner task started.")
    else:
        bg_task = None
        logger.warning("Public exchange metadata unavailable; Model A background scans remain stopped.")

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

    if model_d_scan_worker.state["enabled"]:
        model_d_scan_worker.start()
        logger.info("Model D scan worker started.")
        model_d_exit_monitor.start()
    wallet_master_enabled = bool(model_d_config.enabled and model_d_config.wallet_tracking_enabled and model_d_config.wallet_scanner_enabled)
    if wallet_master_enabled:
        model_d_wallet_pipeline.start()

    # Research synchronization is opt-in and isolated from all trading workers.
    if os.getenv("RESEARCH_DATA_SCHEDULER_ENABLED", "false").strip().lower() == "true":
        research_scheduler = ResearchDataScheduler(research_store)
        research_scheduler.start()
        logger.info("Research data scheduler started (opt-in).")

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

    if research_scheduler:
        await research_scheduler.stop()
        research_scheduler = None

    # Stop Model B runner
    try:
        model_b_runner.stop()
        logger.info("Model B runner stopped.")
    except Exception as e:
        logger.warning(f"Error stopping Model B runner: {e}")
    model_d_scan_worker.stop()
    model_d_exit_monitor.stop()
    model_d_wallet_pipeline.stop()

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
        "forward_validation": journal.compute_forward_validation_cohort(model_id="MODEL_A"),
        "discord": {
            "enabled": bool(cfg.paper.discord_webhook_url and validate_webhook_url(cfg.paper.discord_webhook_url)),
            "webhook_configured": bool(cfg.paper.discord_webhook_url),
            "webhook_masked": mask_webhook_url(cfg.paper.discord_webhook_url) if cfg.paper.discord_webhook_url else None,
        },
        "last_scan_utc": runner.last_scan_utc if runner else None,
    }


@app.get("/api/model_a/diagnostics/funnel")
def get_model_a_funnel_diagnostics(since_ms: Optional[int] = Query(None, ge=0)) -> Dict[str, Any]:
    """Read-only structured Model A funnel and scan-lifecycle reconciliation."""
    report = db.aggregate_model_a_funnel(since_ms=since_ms)
    clauses = ["model_id='MODEL_A'", "stage IN ('SCAN_STARTED','SCAN_COMPLETED','SCAN_FAILED')"]
    params: List[Any] = []
    if since_ms is not None:
        clauses.append("timestamp_ms >= ?"); params.append(since_ms)
    with db.get_connection() as conn:
        events = [dict(r) for r in conn.execute(
            f"SELECT candidate_id,stage,timestamp_ms,reason_code,metadata_json FROM paper_lifecycle_events WHERE {' AND '.join(clauses)} ORDER BY timestamp_ms DESC LIMIT 5000", params
        ).fetchall()]
    by_scan: Dict[str, Dict[str, Any]] = {}
    for event in events:
        item = by_scan.setdefault(event["candidate_id"], {"scan_id": event["candidate_id"], "started": False, "terminal_status": None, "terminal_timestamp_ms": None})
        if event["stage"] == "SCAN_STARTED": item["started"] = True; item["started_timestamp_ms"] = event["timestamp_ms"]
        elif event["stage"] in ("SCAN_COMPLETED", "SCAN_FAILED"):
            item["terminal_status"] = "COMPLETED" if event["stage"] == "SCAN_COMPLETED" else "FAILED"; item["terminal_timestamp_ms"] = event["timestamp_ms"]
    report["lifecycle_reconciliation"] = {
        "cycles": list(by_scan.values()),
        "missing_terminal_count": sum(1 for item in by_scan.values() if item["started"] and not item["terminal_status"]),
        "denominator": "distinct scan lifecycle candidate_id rows in the bounded event query",
    }
    report["denominators"] = {"funnel_rows": "one persisted row per scan_id and symbol", "predicate_counts": "each predicate occurrence across funnel rows", "first_rejection_counts": "one first-rejection value per funnel row"}
    return report


@app.get("/api/model_a/status")
def get_model_a_status() -> Dict[str, Any]:
    """Read-only authoritative Model A Engine telemetry, configuration, forward validation, positions, journal, and diagnostics."""
    now_utc = datetime.now(timezone.utc)
    now_ms = int(now_utc.timestamp() * 1000)
    stale_threshold_hours = float(getattr(cfg, "stale_threshold_hours", 3.0))

    # 1. Lifecycle & Funnel Aggregation from SQLite (strictly model_id='MODEL_A')
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT stage, reason_code, COUNT(*) as cnt, MIN(created_at_utc) as min_utc, MAX(created_at_utc) as max_utc, MIN(timestamp_ms) as min_ts, MAX(timestamp_ms) as max_ts "
            "FROM paper_lifecycle_events WHERE UPPER(model_id) = 'MODEL_A' GROUP BY stage, reason_code ORDER BY cnt DESC"
        )
        lifecycle_breakdown = [dict(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT MIN(created_at_utc) as earliest_setup_utc, MAX(created_at_utc) as latest_setup_utc, "
            "MIN(timestamp_ms) as earliest_setup_ts, MAX(timestamp_ms) as latest_setup_ts "
            "FROM setup_candidates WHERE UPPER(model_id) = 'MODEL_A'"
        )
        setup_time_bounds = dict(cur.fetchone() or {})

        cur.execute(
            "SELECT symbol, timeframe, total_candles, earliest_timestamp_ms, latest_timestamp_ms, last_sync_utc "
            "FROM candle_sync_state ORDER BY symbol ASC, timeframe ASC"
        )
        sync_rows = [dict(r) for r in cur.fetchall()]

    scan_started_events = sum(r["cnt"] for r in lifecycle_breakdown if r["stage"] == "SCAN_STARTED")
    scan_completed_events = sum(r["cnt"] for r in lifecycle_breakdown if r["stage"] == "SCAN_COMPLETED")
    scan_failed_events = sum(r["cnt"] for r in lifecycle_breakdown if r["stage"] == "SCAN_FAILED")

    last_completed_event_utc = None
    earliest_event_utc = None
    for r in lifecycle_breakdown:
        if r.get("min_utc") and (earliest_event_utc is None or r["min_utc"] < earliest_event_utc):
            earliest_event_utc = r["min_utc"]
        if r["stage"] == "SCAN_COMPLETED" and r.get("max_utc"):
            if last_completed_event_utc is None or r["max_utc"] > last_completed_event_utc:
                last_completed_event_utc = r["max_utc"]

    cohort_start_utc = setup_time_bounds.get("earliest_setup_utc") or earliest_event_utc

    # Pre-screen rejection counts from lifecycle events
    rejection_reasons_breakdown: Dict[str, int] = {}
    for r in lifecycle_breakdown:
        if r["stage"] == "SETUP_REJECTED":
            code = str(r.get("reason_code") or "REJECTED")
            rejection_reasons_breakdown[code] = rejection_reasons_breakdown.get(code, 0) + int(r["cnt"])

    # Also aggregate rejection reasons from setup_candidates (MODEL_A only)
    model_a_setups = journal.get_latest_setups(limit=5000, model_id="MODEL_A")
    setups_by_status: Dict[str, int] = {
        "READY": 0,
        "VALID": 0,
        "WATCHLIST": 0,
        "INFEASIBLE": 0,
        "INVALIDATED": 0,
        "STALE": 0,
        "ERROR": 0,
    }
    candidate_rejection_reasons: Dict[str, int] = {}
    for s in model_a_setups:
        st = str(s.get("status", "UNKNOWN")).upper()
        setups_by_status[st] = setups_by_status.get(st, 0) + 1
        det = s.get("details") or {}
        rejs = det.get("rejection_reasons") or []
        for r_txt in rejs:
            r_str = str(r_txt).strip()
            if not r_str:
                continue
            r_low = r_str.lower()
            if "exceeds hard risk limit" in r_low or "estimated loss at sl" in r_low:
                bucket = "HARD_RISK_LIMIT_EXCEEDED ($1.50 SL Ceiling)"
            elif "exceeds trade risk budget" in r_low or "risk budget" in r_low:
                bucket = "TRADE_RISK_BUDGET_EXCEEDED ($0.09-$0.25 Budget)"
            elif "minimum notional" in r_low or "sizing infeasible" in r_low or "infeasible" in r_low:
                bucket = "SIZING_INFEASIBLE_MIN_NOTIONAL ($5.00 Min)"
            elif "expired" in r_low or "ttl" in r_low:
                bucket = "TTL_EXPIRED_12H"
            elif "candle data age" in r_low or "stale" in r_low:
                bucket = "STALE_CANDLE_DATA (>3.0h)"
            elif "invalidation" in r_low:
                bucket = "STRUCTURAL_INVALIDATION_BREACHED"
            elif "scanner is stopped" in r_low:
                bucket = "SCANNER_STOPPED"
            else:
                bucket = r_str[:80]
            candidate_rejection_reasons[bucket] = candidate_rejection_reasons.get(bucket, 0) + 1

    # 2. Candle Freshness & Missing Candles Audit across Watchlist
    sync_by_sym_tf: Dict[str, Dict[str, Any]] = {}
    for row in sync_rows:
        sync_by_sym_tf[f"{row['symbol']}_{row['timeframe']}"] = row

    symbol_data_audit: List[Dict[str, Any]] = []
    missing_candles_symbols: List[str] = []
    stale_symbols: List[str] = []
    max_latest_1h_ts = 0
    total_1h_candles_cached = 0
    total_4h_candles_cached = 0

    for sym in cfg.watchlist:
        s1h = sync_by_sym_tf.get(f"{sym}_1h")
        s4h = sync_by_sym_tf.get(f"{sym}_4h")
        cnt_1h = int(s1h["total_candles"]) if s1h and s1h.get("total_candles") is not None else db.get_candle_count(sym, "1h")
        cnt_4h = int(s4h["total_candles"]) if s4h and s4h.get("total_candles") is not None else db.get_candle_count(sym, "4h")
        total_1h_candles_cached += cnt_1h
        total_4h_candles_cached += cnt_4h

        latest_1h_ts = int(s1h["latest_timestamp_ms"]) if s1h and s1h.get("latest_timestamp_ms") else None
        if latest_1h_ts and latest_1h_ts > max_latest_1h_ts:
            max_latest_1h_ts = latest_1h_ts

        age_hours = round((now_ms - latest_1h_ts) / (3600.0 * 1000.0), 2) if latest_1h_ts else None
        is_sym_stale = (age_hours is None) or (age_hours > stale_threshold_hours)
        has_min_bars = cnt_1h >= 170 and cnt_4h >= 40
        if not has_min_bars:
            missing_candles_symbols.append(sym)
        if is_sym_stale:
            stale_symbols.append(sym)

        symbol_data_audit.append({
            "symbol": sym,
            "enabled": sym in enabled_symbols,
            "candles_1h": cnt_1h,
            "candles_4h": cnt_4h,
            "required_1h_bars": 170,
            "has_sufficient_bars": has_min_bars,
            "latest_1h_timestamp_ms": latest_1h_ts,
            "latest_1h_utc": datetime.fromtimestamp(latest_1h_ts / 1000.0, tz=timezone.utc).isoformat() if latest_1h_ts else None,
            "last_sync_utc": s1h.get("last_sync_utc") if s1h else None,
            "candle_age_hours": age_hours,
            "is_stale": is_sym_stale,
        })

    overall_candle_age_sec = int((now_ms - max_latest_1h_ts) / 1000) if max_latest_1h_ts > 0 else (runner.candle_age_seconds if runner else None)
    overall_is_stale = (overall_candle_age_sec is None) or (overall_candle_age_sec > (stale_threshold_hours * 3600))

    # 3. Scanner Status & Health
    is_worker_active = bool(runner is not None and BACKGROUND_SCAN_ENABLED and not stop_event.is_set() and bg_task is not None and not bg_task.done())
    if runner is None:
        engine_state = "FROZEN_DEFERRED"
        scanner_health = "DEFERRED_NO_RUNNER"
        process_status = "Frozen Cohort Baseline (Startup exchange metadata deferred; background worker inactive)"
    elif not is_worker_active:
        engine_state = "STOPPED"
        scanner_health = "WORKER_INACTIVE"
        process_status = "Stopped / Paused"
    elif runner.last_error:
        engine_state = "ERROR"
        scanner_health = "DEGRADED_ERROR"
        process_status = f"Error: {runner.last_error}"
    elif overall_is_stale:
        engine_state = "STALE"
        scanner_health = "STALE_MARKET_DATA"
        process_status = "Stale Candle Data (>3.0h)"
    else:
        engine_state = runner.scanner_state
        scanner_health = "HEALTHY"
        process_status = "Active Background Worker"

    if metadata_status != "READY":
        scanner_health = metadata_status
    worker_status = "SCANNING" if is_worker_active and runner and runner.scanner_state == "SCANNING" else ("WORKER_STOPPED" if not is_worker_active else "IDLE")
    candle_sync_status = "DATA_STALE" if overall_is_stale else ("READY_TO_SCAN" if metadata_status == "READY" else metadata_status)

    last_scan_utc = (runner.last_scan_utc if (runner and runner.last_scan_utc) else last_completed_event_utc)
    last_successful_scan_utc = (runner.last_successful_scan_utc if (runner and runner.last_successful_scan_utc) else last_completed_event_utc)
    session_scan_count = int(getattr(runner, "scan_count", 0)) if runner else 0

    # 4. Strategy Configuration (Read-Only Frozen Parameters)
    gen = runner.generator if (runner and getattr(runner, "generator", None)) else SetupGenerator(
        swing_left_bars=cfg.strategy.swing_left_bars,
        swing_right_bars=cfg.strategy.swing_right_bars,
        atr_period=cfg.strategy.atr_period,
        sl_buffer_atr_multiple=cfg.strategy.sl_buffer_atr_multiple,
        min_reward_to_risk=cfg.strategy.min_reward_to_risk,
        fvg_min_size_ratio=cfg.strategy.fvg_min_size_atr_ratio,
        sr_cluster_tolerance_pct=cfg.strategy.sr_cluster_tolerance_pct,
    )

    strategy_config_readonly = {
        "config_id": f"MODEL_A_FROZEN_v{cfg.version}",
        "strategy_version": "MODEL_A_FROZEN",
        "venue": cfg.exchange.name,
        "venue_base_url": cfg.exchange.base_url,
        "whitelisted_symbols": list(cfg.watchlist),
        "enabled_symbols": [s for s in cfg.watchlist if s in enabled_symbols],
        "timeframes": {
            "bias_timeframe": cfg.strategy.bias_timeframe,
            "structure_timeframe": cfg.strategy.execution_timeframe,
            "execution_timeframe": cfg.strategy.execution_timeframe,
            "macro_lookback_bars_1h": 170,
            "dealing_range_bars_1h": 72,
        },
        "strategy_parameters": {
            "swing_left_bars": cfg.strategy.swing_left_bars,
            "swing_right_bars": cfg.strategy.swing_right_bars,
            "atr_period": cfg.strategy.atr_period,
            "sl_buffer_atr_multiple": cfg.strategy.sl_buffer_atr_multiple,
            "min_reward_to_risk": cfg.strategy.min_reward_to_risk,
            "fvg_min_size_atr_ratio": cfg.strategy.fvg_min_size_atr_ratio,
            "sr_cluster_tolerance_pct": cfg.strategy.sr_cluster_tolerance_pct,
            "premium_threshold_72h": gen.premium_threshold,
            "discount_threshold_72h": gen.discount_threshold,
            "min_extension_48h": gen.min_extension_48h,
            "min_impulse_12h": gen.min_impulse_12h,
            "short_macro_cap_168h": gen.short_macro_cap_168h,
            "bull_breakout_pos_72h": gen.bull_breakout_pos_72h,
            "bull_breakout_ret_24h": gen.bull_breakout_ret_24h,
            "bull_breakout_ret_48h": gen.bull_breakout_ret_48h,
            "bull_breakout_ret_168h": gen.bull_breakout_ret_168h,
            "bull_variance_ratio_min": gen.bull_variance_ratio_min,
            "stale_threshold_hours": stale_threshold_hours,
            "pending_expiry_bars_1h": cfg.paper.pending_expiry_bars,
        },
        "ml_parameters": {
            "enabled": cfg.ml.enabled,
            "role": "INFORMATIVE_RANKING_NON_BLOCKING",
            "model_type": cfg.ml.model_type,
            "calibration_method": cfg.ml.calibration_method,
            "min_confidence_threshold": cfg.ml.min_confidence_threshold,
            "label_horizon_bars": cfg.ml.label_horizon_bars,
        },
        "risk_settings": {
            "initial_equity_usdt": cfg.risk.initial_equity_usdt,
            "current_paper_equity_usdt": round(risk_mgr.equity_usdt, 4),
            "margin_mode": cfg.risk.margin_mode,
            "max_leverage": cfg.risk.max_leverage,
            "target_isolated_margin_usdt": cfg.risk.target_isolated_margin_usdt,
            "target_isolated_margin_max_usdt": cfg.risk.target_isolated_margin_max_usdt,
            "hard_risk_limit_usdt": cfg.risk.hard_risk_limit_usdt,
            "nominal_risk_per_trade_usdt": cfg.risk.risk_per_trade_usdt,
            "effective_trade_risk_usdt": round(risk_mgr.get_effective_trade_risk(now_ms), 4),
            "daily_loss_limit_usdt": round(cfg.risk.daily_loss_limit_usdt, 4),
            "weekly_loss_limit_usdt": round(cfg.risk.weekly_loss_limit_usdt, 4),
            "max_open_positions": cfg.risk.max_open_positions,
            "max_entries_per_week": cfg.risk.max_entries_per_week,
            "min_notional_usdt": cfg.risk.min_notional_usdt,
            "maker_fee_rate": cfg.paper.maker_fee_rate,
            "taker_fee_rate": cfg.paper.taker_fee_rate,
            "slippage_rate": cfg.paper.slippage_rate,
            "conservative_fill_buffer_pct": cfg.paper.conservative_fill_buffer_pct,
        },
        "forward_validation_anchor": {
            "cohort_start_utc": cohort_start_utc,
            "required_sample_size": 30,
            "frozen_invariant": "Model A strategy logic, thresholds, and risk parameters are frozen during forward validation.",
        },
    }

    # 5. Model A Open Positions & Pending Orders (Strictly model_id == 'MODEL_A')
    raw_open_trades = journal.get_trades(model_id="MODEL_A", open_only=True, limit=100)
    open_positions_list: List[Dict[str, Any]] = []
    for t in raw_open_trades:
        if str(t.get("model_id", "")).upper() != "MODEL_A":
            continue
        sym = str(t.get("symbol", "")).upper()
        entry_px = float(t.get("entry_price", 0.0))
        qty = float(t.get("quantity", 0.0))
        margin = float(t.get("margin_usdt", 0.0))
        direction_str = str(t.get("direction", "LONG")).upper()

        mark_px = None
        mark_source = "UNAVAILABLE"
        if model_b_runner is not None:
            try:
                mp = model_b_runner.get_mark_price(sym)
                if mp is not None and mp > 0:
                    mark_px = float(mp)
                    mark_source = "LIVE_TICKER_CACHE"
            except Exception:
                pass
        if mark_px is None:
            latest_c = db.get_latest_candle(sym, "1h")
            if latest_c and latest_c.close > 0:
                mark_px = float(latest_c.close)
                mark_source = "1H_CLOSED_CANDLE"

        upnl = None
        roe_pct = None
        if mark_px is not None and qty > 0:
            raw_pnl = (mark_px - entry_px) * qty if direction_str == "LONG" else (entry_px - mark_px) * qty
            fees_so_far = float(t.get("fee_paid", 0.0)) + float(t.get("funding_paid", 0.0))
            upnl = round(raw_pnl - fees_so_far, 4)
            roe_pct = round((upnl / margin * 100.0), 2) if margin > 0 else 0.0

        entry_ts = t.get("entry_timestamp_ms")
        age_sec = max(0, int((now_ms - int(entry_ts)) / 1000)) if entry_ts else None
        if age_sec is not None:
            age_display = f"{age_sec / 3600.0:.1f}h" if age_sec >= 3600 else f"{age_sec // 60}m"
        else:
            age_display = "--"

        det = {}
        if t.get("details_json"):
            try:
                det = json.loads(t["details_json"])
            except Exception:
                det = {}

        open_positions_list.append({
            "trade_id": t.get("trade_id"),
            "setup_id": t.get("setup_id"),
            "symbol": sym,
            "direction": direction_str,
            "entry_timestamp_ms": entry_ts,
            "entry_time_utc": t.get("entry_time_utc"),
            "entry_price": entry_px,
            "mark_price": mark_px,
            "mark_price_source": mark_source,
            "quantity": qty,
            "notional_usdt": float(t.get("notional_usdt", 0.0)),
            "margin_usdt": margin,
            "leverage": float(t.get("leverage", 10.0)),
            "stop_loss": float(t.get("stop_loss", 0.0)),
            "take_profit_1": float(det.get("take_profit_1") or t.get("take_profit") or 0.0),
            "take_profit_2": float(det["take_profit_2"]) if det.get("take_profit_2") is not None else None,
            "unrealized_pnl_usdt": upnl,
            "roe_pct": roe_pct,
            "age_seconds": age_sec,
            "age_display": age_display,
            "entry_category": t.get("entry_category", "Unclassified"),
            "source_strategy": "MODEL_A (SMC/SnR Frozen)",
            "model_id": "MODEL_A",
        })

    model_a_orders = [
        o for o in journal.get_paper_orders(model_id="MODEL_A")
        if str(o.get("model_id", "")).upper() == "MODEL_A"
    ]
    pending_orders_list = [o for o in model_a_orders if str(o.get("status", "")).upper() in ("PENDING", "CREATED", "SUBMITTED")]

    # 6. Model A Trade Journal & Validation Metrics (Strictly model_id == 'MODEL_A')
    model_a_trades = [
        t for t in journal.get_trades(model_id="MODEL_A", limit=1000)
        if str(t.get("model_id", "")).upper() == "MODEL_A"
    ]
    for t in model_a_trades:
        if t.get("signal_source") == "MODEL_B_SCANNER":
            t["signal_source"] = "MODEL_A_SCANNER"

    closed_trades = [t for t in model_a_trades if not t.get("is_open", False) and t.get("exit_timestamp_ms") is not None]
    closed_count = len(closed_trades)
    min_sample_size = 30
    is_sample_sufficient = closed_count >= min_sample_size

    wins = [t for t in closed_trades if float(t.get("pnl_net", 0.0)) > 0.0001]
    losses = [t for t in closed_trades if float(t.get("pnl_net", 0.0)) < -0.0001]
    breakevens = [t for t in closed_trades if -0.0001 <= float(t.get("pnl_net", 0.0)) <= 0.0001]

    gross_profit = sum(float(t.get("pnl_net", 0.0)) for t in wins)
    gross_loss_abs = abs(sum(float(t.get("pnl_net", 0.0)) for t in losses))
    net_realized_pnl = round(sum(float(t.get("pnl_net", 0.0)) for t in closed_trades), 4)
    total_fees_paid = round(sum(float(t.get("total_fees", 0.0)) for t in closed_trades), 4)

    win_rate_pct = round((len(wins) / closed_count * 100.0), 2) if closed_count > 0 else None
    expectancy_usdt = round((net_realized_pnl / closed_count), 4) if closed_count > 0 else None
    profit_factor = round((gross_profit / gross_loss_abs), 2) if gross_loss_abs > 0 else (None if closed_count == 0 else float("inf"))
    avg_win_usdt = round((gross_profit / len(wins)), 4) if wins else None
    avg_loss_usdt = round((-gross_loss_abs / len(losses)), 4) if losses else None

    # Max Drawdown across closed Model A trades ordered chronologically
    max_drawdown_usdt = None
    if closed_count > 0:
        chronological = sorted(closed_trades, key=lambda x: int(x.get("exit_timestamp_ms") or x.get("entry_timestamp_ms") or 0))
        cum_pnl = 0.0
        peak_pnl = 0.0
        max_dd = 0.0
        for ct in chronological:
            cum_pnl += float(ct.get("pnl_net", 0.0))
            if cum_pnl > peak_pnl:
                peak_pnl = cum_pnl
            dd = peak_pnl - cum_pnl
            if dd > max_dd:
                max_dd = dd
        max_drawdown_usdt = round(max_dd, 4)

    long_closed = [t for t in closed_trades if str(t.get("direction", "")).upper() == "LONG"]
    short_closed = [t for t in closed_trades if str(t.get("direction", "")).upper() == "SHORT"]
    long_wins = [t for t in long_closed if float(t.get("pnl_net", 0.0)) > 0.0001]
    short_wins = [t for t in short_closed if float(t.get("pnl_net", 0.0)) > 0.0001]

    validation_metrics = {
        "model_id": "MODEL_A",
        "sample_status": "SUFFICIENT_SAMPLE" if is_sample_sufficient else "INSUFFICIENT_SAMPLE",
        "is_sample_sufficient": is_sample_sufficient,
        "required_sample_size": min_sample_size,
        "completed_trades_count": closed_count,
        "wins_count": len(wins),
        "losses_count": len(losses),
        "breakeven_count": len(breakevens),
        "win_rate_pct": win_rate_pct,
        "net_realized_pnl_usdt": net_realized_pnl if closed_count > 0 else 0.0,
        "total_fees_usdt": total_fees_paid if closed_count > 0 else 0.0,
        "expectancy_per_trade_usdt": expectancy_usdt,
        "profit_factor": profit_factor if profit_factor != float("inf") else "INF",
        "max_drawdown_usdt": max_drawdown_usdt,
        "avg_win_usdt": avg_win_usdt,
        "avg_loss_usdt": avg_loss_usdt,
        "long_breakdown": {
            "count": len(long_closed),
            "wins": len(long_wins),
            "losses": len(long_closed) - len(long_wins),
            "win_rate_pct": round(len(long_wins) / len(long_closed) * 100.0, 2) if long_closed else None,
            "net_pnl_usdt": round(sum(float(t.get("pnl_net", 0.0)) for t in long_closed), 4) if long_closed else 0.0,
        },
        "short_breakdown": {
            "count": len(short_closed),
            "wins": len(short_wins),
            "losses": len(short_closed) - len(short_wins),
            "win_rate_pct": round(len(short_wins) / len(short_closed) * 100.0, 2) if short_closed else None,
            "net_pnl_usdt": round(sum(float(t.get("pnl_net", 0.0)) for t in short_closed), 4) if short_closed else 0.0,
        },
    }

    # 7. Forward Validation Cohort & Funnel
    cohort_data = journal.compute_forward_validation_cohort(min_required_trades=min_sample_size, model_id="MODEL_A")
    total_pre_screen_rejections = sum(rejection_reasons_breakdown.values())
    total_candidates_persisted = len(model_a_setups)
    total_bar_evaluations = total_pre_screen_rejections + total_candidates_persisted
    valid_ready_setups_count = setups_by_status.get("READY", 0) + setups_by_status.get("VALID", 0)
    rejected_setups_count = (
        setups_by_status.get("INFEASIBLE", 0)
        + setups_by_status.get("INVALIDATED", 0)
        + setups_by_status.get("STALE", 0)
        + setups_by_status.get("ERROR", 0)
    )

    forward_validation_section = {
        "model_id": "MODEL_A",
        "cohort_start_utc": cohort_start_utc,
        "completed_scan_cycles": scan_completed_events,
        "started_scan_cycles": scan_started_events,
        "bar_evaluations_logged": total_bar_evaluations,
        "cached_candles_1h_total": total_1h_candles_cached,
        "cached_candles_4h_total": total_4h_candles_cached,
        "required_sample_size": min_sample_size,
        "is_sample_sufficient": is_sample_sufficient,
        "validation_status": cohort_data.get("validation_status", "UNPROVEN_NO_COMPLETED_FORWARD_TRADES"),
        "sample_badge": "SUFFICIENT_SAMPLE" if is_sample_sufficient else "INSUFFICIENT_SAMPLE",
        "status_explanation": cohort_data.get("status_explanation", ""),
        "candidate_setups_evaluated": total_candidates_persisted,
        "valid_ready_setups": valid_ready_setups_count,
        "watchlist_setups": setups_by_status.get("WATCHLIST", 0),
        "rejected_setups": rejected_setups_count,
        "setups_by_status": setups_by_status,
        "pre_screen_rejection_reasons": rejection_reasons_breakdown,
        "candidate_rejection_reasons": candidate_rejection_reasons,
        "paper_orders_created": len(model_a_orders),
        "paper_orders_pending": len(pending_orders_list),
        "paper_orders_filled": sum(1 for o in model_a_orders if str(o.get("status", "")).upper() == "FILLED"),
        "completed_paper_trades": closed_count,
        "open_paper_positions": len(open_positions_list),
    }

    # 8. Diagnostics
    funnel_diag = db.aggregate_model_a_funnel()
    diagnostics_section = {
        "scanner_errors": runner.last_error if (runner and runner.last_error) else None,
        "scan_failed_events_count": scan_failed_events,
        "is_stale_market_data": overall_is_stale,
        "candle_age_seconds": overall_candle_age_sec,
        "candle_age_hours": round(overall_candle_age_sec / 3600.0, 2) if overall_candle_age_sec is not None else None,
        "stale_threshold_hours": stale_threshold_hours,
        "stale_symbols_count": len(stale_symbols),
        "stale_symbols": stale_symbols,
        "missing_candles_symbols_count": len(missing_candles_symbols),
        "missing_candles_symbols": missing_candles_symbols,
        "invalidated_setups_count": setups_by_status.get("INVALIDATED", 0),
        "stale_setups_count": setups_by_status.get("STALE", 0),
        "infeasible_setups_count": setups_by_status.get("INFEASIBLE", 0),
        "pending_orders_count": len(pending_orders_list),
        "last_scan_utc": last_scan_utc,
        "last_successful_scan_utc": last_successful_scan_utc,
        "metadata_available": bool(specs_map),
        "runner_initialized": runner is not None,
        "background_worker_active": is_worker_active,
        "symbol_data_audit": symbol_data_audit,
        "funnel_diagnostics": funnel_diag,
    }

    return {
        "status": "SUCCESS",
        "model_id": "MODEL_A",
        "timestamp_utc": now_utc.isoformat(),
        "strategy_status": {
            "model_id": "MODEL_A",
            "name": "Model A — Defensive SMC & SnR Perpetual Engine",
            "state": engine_state,
        "scanner_health": scanner_health,
        "metadata_initialization_status": metadata_status,
        "metadata_symbol_count": len(specs_map),
        "metadata_initialized_utc": metadata_initialized_utc,
        "last_initialization_error": metadata_error,
        "worker_status": worker_status,
        "candle_synchronization_status": candle_sync_status,
        "latest_closed_1h_utc": max((r.get("latest_1h_utc") for r in symbol_data_audit if r.get("latest_1h_utc")), default=None),
        "latest_closed_4h_utc": max((datetime.fromtimestamp(int(sync_by_sym_tf[f'{s}_4h']['latest_timestamp_ms']) / 1000, tz=timezone.utc).isoformat() for s in cfg.watchlist if sync_by_sym_tf.get(f'{s}_4h') and sync_by_sym_tf[f'{s}_4h'].get('latest_timestamp_ms')), default=None),
        "per_symbol_freshness": symbol_data_audit,
        "scanner_readiness": bool(metadata_status == "READY" and not overall_is_stale and not missing_candles_symbols),
        "last_synchronization_error": getattr(runner, "last_error", None) if runner else None,
            "process_status": process_status,
            "mode": cfg.mode.value,
            "paper_only": True,
            "live_trading_locked": cfg.live_trading_locked,
            "background_worker_active": is_worker_active,
            "runner_initialized": runner is not None,
            "metadata_available": bool(specs_map),
            "session_scan_count": session_scan_count,
            "historical_scan_cycles_completed": scan_completed_events,
            "last_scan_utc": last_scan_utc,
            "last_successful_scan_utc": last_successful_scan_utc,
            "active_config_id": f"MODEL_A_FROZEN_v{cfg.version}",
        },
        "strategy_configuration": strategy_config_readonly,
        "forward_validation": forward_validation_section,
        "open_positions": open_positions_list,
        "pending_orders": pending_orders_list,
        "recent_candidates": model_a_setups[:50],
        "trade_journal": model_a_trades[:100],
        "validation_metrics": validation_metrics,
        "diagnostics": diagnostics_section,
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
    timeframe: str = Query("1h", description="Candle timeframe e.g. 15m, 1h, 4h, 1d"),
    limit: int = Query(500, ge=10, le=5000, description="Number of candles"),
    start_time_ms: Optional[int] = Query(None, description="Start time in ms"),
    end_time_ms: Optional[int] = Query(None, description="End time in ms"),
    before_time_ms: Optional[int] = Query(None, description="Cursor timestamp in ms (load candles strictly older than this timestamp)"),
    before: Optional[int] = Query(None, description="Alias for before_time_ms cursor"),
    venue: Optional[str] = Query(None, description="Venue name: asterdex, hyperliquid, binance"),
) -> List[Dict[str, Any]]:
    """Retrieve OHLCV candles formatted for TradingView Lightweight Charts with cursor pagination."""
    effective_before = before_time_ms if before_time_ms is not None else before
    return liquidity_chart_service.get_chart_klines(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        before_time_ms=effective_before,
        venue=venue,
    )


@app.get("/api/chart/liquidity_layers")
async def chart_liquidity_layers_endpoint(
    symbol: str = Query(..., description="Perpetual symbol e.g. BTCUSDT"),
    timeframe: str = Query("1h", description="Candle timeframe e.g. 15m, 1h, 4h, 1d"),
    limit: int = Query(500, ge=20, le=5000, description="Number of candles"),
    before_time_ms: Optional[int] = Query(None, description="Cursor timestamp in ms (load candles strictly older than this timestamp)"),
    before: Optional[int] = Query(None, description="Alias for before_time_ms cursor"),
    start_time_ms: Optional[int] = Query(None, description="Start time in ms"),
    end_time_ms: Optional[int] = Query(None, description="End time in ms"),
    as_of_index: Optional[int] = Query(None, description="Historical replay bar index limit"),
    as_of_timestamp_ms: Optional[int] = Query(None, description="Historical replay timestamp cutoff"),
    include_1d_overlay: bool = Query(False, description="Overlay confirmed 1D structural levels on lower timeframes"),
    venue_source: str = Query("combined", description="Venue filter: combined, asterdex, binance, bybit"),
) -> Dict[str, Any]:
    """Retrieve complete liquidity layers: structural levels, sweep/reclaim events, order-book depth, and liquidation estimates."""
    effective_before = before_time_ms if before_time_ms is not None else before
    return liquidity_chart_service.get_liquidity_layers(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        before_time_ms=effective_before,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        as_of_index=as_of_index,
        as_of_timestamp_ms=as_of_timestamp_ms,
        include_1d_overlay=include_1d_overlay,
        venue_source=venue_source,
    )


class ChartDrawingUpsertRequest(BaseModel):
    drawing_id: str
    user_profile: str = "default"
    symbol: str
    timeframe: str
    tool_type: str
    points: List[Dict[str, Any]] = []
    style: Dict[str, Any] = {}
    is_visible: bool = True
    created_at_utc: Optional[str] = None


@app.get("/api/chart/drawings")
async def get_chart_drawings_endpoint(
    symbol: str = Query(..., description="Symbol e.g. BTCUSDT"),
    timeframe: str = Query("1h", description="Timeframe e.g. 15m, 1h, 4h, 1d"),
    user_profile: str = Query("default", description="User profile key"),
) -> Dict[str, Any]:
    """Retrieve persisted chart drawings from SQLite for (user_profile, symbol, timeframe)."""
    drawings = db.get_chart_drawings(symbol=symbol, timeframe=timeframe, user_profile=user_profile)
    return {
        "symbol": symbol.strip().upper().replace(".P", ""),
        "timeframe": timeframe.strip().lower(),
        "user_profile": user_profile,
        "drawings": drawings,
        "count": len(drawings),
    }


@app.post("/api/chart/drawings")
async def save_chart_drawing_endpoint(req: ChartDrawingUpsertRequest) -> Dict[str, Any]:
    """Create or update a chart drawing in SQLite."""
    saved = db.save_chart_drawing(req.model_dump())
    return {"status": "SAVED", "drawing": saved}


@app.delete("/api/chart/drawings/{drawing_id}")
async def delete_chart_drawing_endpoint(
    drawing_id: str,
    user_profile: str = Query("default", description="User profile key"),
) -> Dict[str, Any]:
    """Delete a single chart drawing from SQLite."""
    deleted = db.delete_chart_drawing(drawing_id=drawing_id, user_profile=user_profile)
    return {"status": "DELETED" if deleted else "NOT_FOUND", "drawing_id": drawing_id, "deleted": deleted}


@app.delete("/api/chart/drawings")
async def clear_chart_drawings_endpoint(
    symbol: str = Query(..., description="Symbol e.g. BTCUSDT"),
    timeframe: str = Query("1h", description="Timeframe e.g. 15m, 1h, 4h, 1d"),
    user_profile: str = Query("default", description="User profile key"),
) -> Dict[str, Any]:
    """Delete all chart drawings for (user_profile, symbol, timeframe) from SQLite."""
    deleted_count = db.clear_chart_drawings(symbol=symbol, timeframe=timeframe, user_profile=user_profile)
    return {"status": "CLEARED", "deleted_count": deleted_count}


@app.get("/api/chart/cross_book")
async def chart_cross_book_endpoint(
    symbol: str = Query(..., description="Perpetual symbol e.g. LTCUSDT"),
    bucket_bps: float = Query(5.0, ge=1.0, le=50.0, description="Bucket size in basis points"),
    venue_source: str = Query("combined", description="Venue filter: combined, asterdex, binance, bybit"),
) -> Dict[str, Any]:
    """Retrieve multi-exchange Level-2 order-book liquidity heatmap across AsterDEX, Binance, and Bybit."""
    return liquidity_chart_service.get_cross_exchange_book(
        symbol=symbol,
        bucket_bps=bucket_bps,
        venue_filter=venue_source,
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
        "supported_timeframes": ["15m", "1h", "4h", "1d"],
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
    if runner is None:
        return {"status": "ERROR", "message": "Model A runner is not initialized.", "scanner_state": "STOPPED"}
    if not await initialize_model_a_metadata():
        BACKGROUND_SCAN_ENABLED = False
        stop_event.set()
        runner.scanner_state = "STOPPED"
        return {
            "status": "ERROR",
            "message": "Model A metadata unavailable; scanner remains stopped.",
            "scanner_state": "STOPPED",
            "metadata_status": metadata_status,
            "error": metadata_error,
        }
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
        db_setups = journal.get_latest_setups(limit=50, model_id="MODEL_A")
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

    if not specs_map:
        return {"status": "UNAVAILABLE", "message": "Public exchange metadata unavailable; scan was not started.", "setups": [], "count": 0, "valid_count": 0, "ready_count": 0}

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
        "forward_validation": journal.compute_forward_validation_cohort(model_id=m_id or "MODEL_A"),
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
    forward_cohort = journal.compute_forward_validation_cohort(model_id=target_model_id or "MODEL_A")
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


def _wallet_pipeline_status() -> str:
    """Return lifecycle state separately from provider validation state."""
    if not model_d_config.enabled or not model_d_config.wallet_tracking_enabled or not model_d_config.wallet_scanner_enabled:
        return "DISABLED"
    health = str(model_d_wallet_pipeline.state.get("health", "NOT_STARTED"))
    if health in {"NOT_STARTED", "RUNNING", "IDLE", "UNAVAILABLE", "ERROR"}:
        return health
    return "ERROR"


def _wallet_provider_status() -> Dict[str, Any]:
    chain_state = model_d_wallet_pipeline.state.get("chains", {})
    result: Dict[str, Any] = {}
    for chain_id, url in model_d_config.wallet_rpc_urls.items():
        key = str(chain_id)
        if not url:
            result[key] = {"status": "UNAVAILABLE", "reason": "missing_url"}
        elif key in chain_state:
            result[key] = dict(chain_state[key])
        else:
            result[key] = {"status": "NOT_STARTED", "reason": "provider_not_validated"}
    return result


@app.get("/api/model_d/status")
def get_model_d_status(symbol: Optional[str] = Query(None, max_length=32)) -> Dict[str, Any]:
    """Read-only Model D health and source freshness."""
    wallet_status = _wallet_pipeline_status()
    payload: Dict[str, Any] = {
        "model_id": "MODEL_D", "enabled": model_d_config.enabled,
        "paper_only": True, "live_trading_locked": bool(cfg.live_trading_locked),
        "collector_enabled": model_d_config.collector_enabled,
        "scan_worker": model_d_scan_worker.state,
        "exit_monitor": model_d_exit_monitor.state,
        "wallet_tracking": wallet_status, "wallet_pipeline": model_d_wallet_pipeline.state,
        "sources": {}, "symbol": None,
    }
    if symbol and model_d_config.collector_enabled:
        data = model_d_collector.collect_symbol(symbol)
        payload["symbol"] = data["symbol"]
        payload["sources"] = data["sources"]
    else:
        payload["sources"] = {k: v.to_dict() for k, v in model_d_collector.health.items()}
    return payload


@app.get("/api/model_d/universe")
def get_model_d_universe(refresh: bool = Query(False)) -> Dict[str, Any]:
    if refresh or not model_d_universe.instruments:
        model_d_universe.refresh(force=refresh)
    return model_d_universe.status()


@app.get("/api/model_d/scan/status")
def get_model_d_scan_status() -> Dict[str, Any]:
    return {"model_id": "MODEL_D", "scan_worker": model_d_scan_worker.state, "exit_monitor": model_d_exit_monitor.state}


@app.get("/api/model_d/scan/observations")
def get_model_d_scan_observations(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    rows = model_d_scan_worker.recent(limit)
    return {"model_id": "MODEL_D", "items": rows, "count": len(rows), "limit": limit}


@app.get("/api/model_d/signals/latest")
def get_model_d_latest_signals(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    with db.get_connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM model_d_signal_decisions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]
    return {"model_id": "MODEL_D", "items": rows, "count": len(rows), "limit": limit}


@app.get("/api/model_d/bots")
def get_model_d_bots() -> Dict[str, Any]:
    return {"model_id": "MODEL_D", "paper_only": True, "bots": [bot.summary() for bot in model_d_bots.values()]}


@app.get("/api/model_d/positions")
def get_model_d_positions(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> Dict[str, Any]:
    positions = [asdict(p) for bot in model_d_bots.values() for p in bot.positions.values()]
    return {"model_id": "MODEL_D", "paper_only": True, "items": positions[offset:offset + limit], "count": len(positions), "limit": limit, "offset": offset}


@app.get("/api/model_d/journal")
def get_model_d_journal(limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0), bot_id: Optional[str] = None, symbol: Optional[str] = Query(None, min_length=3, max_length=32), direction: Optional[str] = Query(None, pattern="^(LONG|SHORT)$"), lifecycle_status: Optional[str] = Query(None, pattern="^(OPEN|CLOSED)$"), entry_from: Optional[str] = None, entry_to: Optional[str] = None, exit_from: Optional[str] = None, exit_to: Optional[str] = None) -> Dict[str, Any]:
    def end_of_day(value): return f"{value}T23:59:59.999999" if value and len(value) == 10 else value
    items, count = model_d_bot_store.filtered_journal(limit, offset, bot_id, symbol, direction, lifecycle_status, entry_from, end_of_day(entry_to), exit_from, end_of_day(exit_to))
    return {"model_id": "MODEL_D", "paper_only": True, "items": items, "count": count, "limit": limit, "offset": offset}


@app.get("/api/model_d/signals")
def get_model_d_signals(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> Dict[str, Any]:
    """Bounded, read-only signal history; empty until the research runner records signals."""
    records = list(model_d_ledger.records.values())
    return {"model_id": "MODEL_D", "items": [r.__dict__ for r in records[offset:offset + limit]], "count": len(records), "offset": offset, "limit": limit}


@app.get("/api/model_d/liquidity")
def get_model_d_liquidity(symbol: str = Query(..., min_length=3, max_length=32)) -> Dict[str, Any]:
    if not model_d_config.collector_enabled:
        return {"model_id": "MODEL_D", "symbol": symbol.upper(), "status": "UNAVAILABLE", "reason": "collector_disabled"}
    data = model_d_collector.collect_symbol(symbol)
    features = build_features(data.get("order_book"), data.get("trades", []), model_d_config.trade_reference_window)
    return {"model_id": "MODEL_D", "symbol": data["symbol"], "features": features.to_dict(), "sources": data["sources"], "wallet_activity": "UNAVAILABLE"}


@app.get("/api/model_d/whale-events")
def get_model_d_whale_events(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> Dict[str, Any]:
    return {"model_id": "MODEL_D", "items": [], "count": 0, "offset": offset, "limit": limit, "status": "UNAVAILABLE", "reason": "event_persistence_not_configured"}


@app.get("/api/model_d/diagnostic/report")
def get_model_d_report() -> Dict[str, Any]:
    return {"model_id": "MODEL_D", "paper_only": True, "metrics": model_d_ledger.summary(), "long": {"sample_size": 0}, "short": {"sample_size": 0}, "warning": "Insufficient observations for performance claims."}


@app.get("/api/model_d/wallets/status")
def get_model_d_wallet_status() -> Dict[str, Any]:
    counts = model_d_wallet_registry.count_by_chain()
    lifecycle_status = _wallet_pipeline_status()
    provider_status = _wallet_provider_status()
    wallet_master_enabled = lifecycle_status != "DISABLED"
    return {"model_id": "MODEL_D", "enabled": wallet_master_enabled,
            "target": model_d_config.wallet_discovery_target, "counts_by_chain": counts,
            "unique_candidates": sum(counts.values()), "wallet_tracking": lifecycle_status,
            "worker_status": lifecycle_status, "source_status": provider_status,
            "configured_chains": sorted(model_d_config.wallet_rpc_urls.keys()),
            "provider_chains": sorted(model_d_wallet_providers.keys()),
            "worker": model_d_wallet_pipeline.state,
            "enrichment_enabled": model_d_config.wallet_enrichment_enabled,
            "ranking_enabled": model_d_config.wallet_ranking_enabled}


@app.get("/api/model_d/wallets")
def get_model_d_wallets(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), chain_id: Optional[int] = Query(None)) -> Dict[str, Any]:
    rows = model_d_wallet_registry.list_candidates(limit=limit, offset=offset, chain_id=chain_id)
    return {"model_id": "MODEL_D", "items": rows, "count": len(rows), "limit": limit, "offset": offset}


@app.get("/api/model_d/wallets/{wallet_id}")
def get_model_d_wallet(wallet_id: str) -> Dict[str, Any]:
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM model_d_wallet_candidates WHERE wallet_id=?", (wallet_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="wallet candidate not found")
    return {"model_id": "MODEL_D", "wallet": dict(row)}


@app.get("/api/model_b/diagnostic/report")
async def model_b_diagnostic_report(cohort: str = Query("auto", pattern="^(auto|experiment|baseline|all)$")) -> Dict[str, Any]:
    """Read-only LONG vs SHORT diagnostic metrics, signal/rejection stats and breaker reconciliation."""
    trades = journal.get_trades(open_only=False, limit=5000, model_id="MODEL_B") + journal.get_open_trades(model_id="MODEL_B")
    candidates = journal.get_latest_setups(limit=5000, model_id="MODEL_B")
    report = build_model_b_diagnostic_report(trades, candidates, {**model_b_risk_mgr.diagnostic_state, "active": model_b_risk_mgr.diagnostic_override_active}, cohort=cohort)
    status = model_b_runner.get_status()
    report["risk_status"] = status.get("risk_limits", {})
    report["diagnostic"] = status.get("diagnostic", {})
    last = status.get("last_successful_scan_utc")
    stale = None
    if last:
        stale = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() > 900
    report["data_quality"] = {"last_successful_scan_utc": last, "scan_stale": stale}
    return report


class ModelBKillSwitchPayload(BaseModel):
    enabled: bool


@app.post("/api/model_b/diagnostic/kill_switch", dependencies=[Depends(require_mutating_api_access)])
async def model_b_kill_switch(payload: ModelBKillSwitchPayload) -> Dict[str, Any]:
    """Immediately block (or re-allow) NEW Model B paper entries. Resting paper orders are cancelled when ON."""
    model_b_risk_mgr.entry_kill_switch = payload.enabled
    model_b_runner.config.entry_kill_switch = payload.enabled
    logger.warning(f"MODEL_B_ENTRY_KILL_SWITCH set to {payload.enabled}")
    return {"entry_kill_switch": payload.enabled, "paper_only": True}


@app.get("/api/model_b/scanner_telemetry")
def get_model_b_scanner_telemetry(limit: int = Query(10, ge=1, le=50)):
    """Retrieve real-world performance telemetry, coverage stats, and request latencies for Model B."""
    if not model_b_runner or not hasattr(model_b_runner, "telemetry_tracker"):
        return {"latest": None, "history": []}
    return {
        "latest": model_b_runner.telemetry_tracker.get_latest(),
        "history": model_b_runner.telemetry_tracker.get_history(limit=limit),
    }


@app.post("/api/model_b/scan")
async def trigger_model_b_scan():
    """Trigger an immediate 1H scan cycle across universal perpetual markets for Model B."""
    # Model B uses its own MultiExchangeService and does not depend on Model A specs_map
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

@app.get("/api/model_b/positions")
def get_model_b_positions():
    """Retrieve active open simulated paper positions for Model B enriched with live mark prices, UPnL, ROE, and margin."""
    positions = model_b_runner.get_enriched_open_positions() if model_b_runner else []
    return {
        "model_id": "MODEL_B",
        "positions": positions,
        "count": len(positions),
        "is_simulated": True,
        "mode": "SIMULATED_ISOLATED",
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


@app.get("/api/model_b/whale_watchlist")
async def get_model_b_whale_watchlist(
    max_symbols: int = Query(20, ge=1, le=50),
    force_refresh: bool = Query(False),
):
    """Retrieve ranked perpetuals by unusual whale buying and selling flow across venues."""
    try:
        watchlist = await asyncio.to_thread(
            whale_ranking_engine.scan_and_rank_universe,
            max_symbols=max_symbols,
            force_refresh=force_refresh,
        )
        return watchlist
    except Exception as err:
        logger.error(f"Error scanning whale watchlist: {err}")
        return {
            "scanned_count": 0,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "top_buy_activity": [],
            "top_sell_activity": [],
            "active_resting_walls": [],
            "error": str(err),
            "guardrail_notice": "Whale flow alerts and ranking scores are research and decision-support telemetry only. They NEVER automatically submit or modify live or paper orders.",
        }


@app.get("/api/model_b/whale_events")
def get_model_b_whale_events(
    symbol: Optional[str] = Query(None, description="Optional perpetual symbol filter"),
    event_type: Optional[str] = Query(None, description="Optional WhaleEventType filter"),
    venue: Optional[str] = Query(None, description="Optional exchange venue filter"),
    lifecycle_state: Optional[str] = Query(None, description="Optional lifecycle state filter"),
    limit: int = Query(50, ge=1, le=200),
):
    """Retrieve persisted institutional tape and resting depth events from SQLite."""
    events = db.get_whale_events(
        symbol=symbol,
        event_type=event_type,
        venue=venue,
        lifecycle_state=lifecycle_state,
        limit=limit,
    )
    return {
        "count": len(events),
        "events": events,
        "guardrail_notice": "Whale flow alerts are research and decision-support telemetry only.",
    }


@app.get("/api/model_b/whale_active_walls")
def get_model_b_whale_active_walls():
    """Retrieve currently active resting order book depth walls across monitored symbols."""
    active_walls = []
    for sym, walls_dict in whale_ranking_engine.whale_detector._tracked_walls.items():
        for w in walls_dict.values():
            if w.is_active:
                active_walls.append({
                    "symbol": w.symbol,
                    "venue": w.venue,
                    "side": w.side,
                    "price": round(w.price, 4),
                    "notional_usdt": round(w.notional_usdt, 2),
                    "distance_bps": round(w.min_distance_bps, 1),
                    "persistence_seconds": round(w.persistence_seconds, 1),
                    "is_spoof_candidate": w.is_spoof_candidate,
                    "first_seen_utc": datetime.fromtimestamp(w.first_seen_ms / 1000, tz=timezone.utc).isoformat(),
                })
    active_walls.sort(key=lambda x: x["notional_usdt"], reverse=True)
    return {
        "count": len(active_walls),
        "walls": active_walls[:50],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/model_b/whale_correlate")
async def post_model_b_whale_correlate():
    """Retrospective evaluation: update forward horizon prices (+15m, +1h, +4h) for historical alerts."""
    unresolved_15m = db.get_unresolved_whale_events(horizon="15m")
    unresolved_1h = db.get_unresolved_whale_events(horizon="1h")
    unresolved_4h = db.get_unresolved_whale_events(horizon="4h")

    updated_count = 0
    # Group by symbol to fetch price once
    symbols_to_check = {ev["symbol"] for ev in (unresolved_15m + unresolved_1h + unresolved_4h)}
    current_prices: Dict[str, float] = {}

    for sym in symbols_to_check:
        try:
            prem = market_context_service.adapter.get_premium_index(sym)
            if prem and prem.get("mark_price"):
                current_prices[sym] = float(prem["mark_price"])
        except Exception:
            pass

    for ev in unresolved_15m:
        px = current_prices.get(ev["symbol"])
        if px and px > 0:
            ret = ((px - ev["mark_price_at_event"]) / ev["mark_price_at_event"]) * 100.0 if ev["mark_price_at_event"] > 0 else 0.0
            is_buy = ev["side"] in ("BUY", "BID")
            if is_buy:
                outcome = "CONTINUATION" if ret >= 0.5 else ("REVERSAL" if ret <= -0.5 else "CHOP")
            else:
                outcome = "CONTINUATION" if ret <= -0.5 else ("REVERSAL" if ret >= 0.5 else "CHOP")
            if db.update_whale_event_forward_price(ev["event_id"], horizon="15m", price=px, outcome=outcome):
                updated_count += 1

    for ev in unresolved_1h:
        px = current_prices.get(ev["symbol"])
        if px and px > 0:
            ret = ((px - ev["mark_price_at_event"]) / ev["mark_price_at_event"]) * 100.0 if ev["mark_price_at_event"] > 0 else 0.0
            is_buy = ev["side"] in ("BUY", "BID")
            outcome = "CONTINUATION" if ((is_buy and ret >= 1.0) or (not is_buy and ret <= -1.0)) else ("REVERSAL" if ((is_buy and ret <= -1.0) or (not is_buy and ret >= 1.0)) else "CHOP")
            if db.update_whale_event_forward_price(ev["event_id"], horizon="1h", price=px, outcome=outcome):
                updated_count += 1

    for ev in unresolved_4h:
        px = current_prices.get(ev["symbol"])
        if px and px > 0:
            ret = ((px - ev["mark_price_at_event"]) / ev["mark_price_at_event"]) * 100.0 if ev["mark_price_at_event"] > 0 else 0.0
            is_buy = ev["side"] in ("BUY", "BID")
            outcome = "CONTINUATION" if ((is_buy and ret >= 2.0) or (not is_buy and ret <= -2.0)) else ("REVERSAL" if ((is_buy and ret <= -2.0) or (not is_buy and ret >= 2.0)) else "CHOP")
            if db.update_whale_event_forward_price(ev["event_id"], horizon="4h", price=px, outcome=outcome):
                updated_count += 1

    return {
        "status": "SUCCESS",
        "updated_events_count": updated_count,
        "checked_symbols": list(symbols_to_check),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
# Model B — Independent Live-Market Shadow Research API Endpoints
# =====================================================================

@app.get("/api/model_b/research/metrics")
def get_model_b_research_metrics():
    """Retrieve statistical performance metrics for all 6 strategy variants and synthetic portfolios."""
    storage = model_b_runner.shadow_evaluator.storage if (model_b_runner and hasattr(model_b_runner, "shadow_evaluator")) else ResearchStorage(db)

    # 1. Variant metrics
    variants_data = {}
    for var in ResearchVariant:
        m = storage.compute_metrics(var.value)
        variants_data[var.value] = m.to_dict()

    # 2. Synthetic portfolios
    portfolios = {
        "baseline": storage.compute_portfolio_metrics(
            long_variant=ResearchVariant.VARIANT_A_LONG_BASELINE.value,
            short_variant=ResearchVariant.VARIANT_B_SHORT_BASELINE.value,
        ),
        "trend_4h": storage.compute_portfolio_metrics(
            long_variant=ResearchVariant.VARIANT_A_LONG_BASELINE.value,
            short_variant=ResearchVariant.VARIANT_C_SHORT_4H_TREND.value,
        ),
        "confirmed_close": storage.compute_portfolio_metrics(
            long_variant=ResearchVariant.VARIANT_A_LONG_BASELINE.value,
            short_variant=ResearchVariant.VARIANT_D_SHORT_CONFIRMED_CLOSE.value,
        ),
        "order_flow": storage.compute_portfolio_metrics(
            long_variant=ResearchVariant.VARIANT_A_LONG_BASELINE.value,
            short_variant=ResearchVariant.VARIANT_E_SHORT_ORDER_FLOW.value,
        ),
        "combined_optimized": storage.compute_portfolio_metrics(
            long_variant=ResearchVariant.VARIANT_A_LONG_BASELINE.value,
            short_variant=ResearchVariant.VARIANT_F_SHORT_COMBINED.value,
        ),
    }

    # 3. Active paper portfolio status (isolated presentation)
    active_status = model_b_runner.get_status() if model_b_runner else {}

    # 4. Market-data freshness
    latest_telemetry = None
    if model_b_runner and hasattr(model_b_runner, "telemetry_tracker"):
        latest_telemetry = model_b_runner.telemetry_tracker.get_latest()

    return {
        "variants": variants_data,
        "portfolios": portfolios,
        "metadata": VARIANT_METADATA,
        "active_portfolio": {
            "capital": active_status.get("capital", {}),
            "risk_limits": active_status.get("risk_limits", {}),
            "execution_metrics": active_status.get("execution_metrics", {}),
            "circuit_breaker_active": active_status.get("risk_limits", {}).get("daily_circuit_breaker_enforced", False)
            or active_status.get("risk_limits", {}).get("weekly_circuit_breaker_enforced", False),
            "diagnostic": active_status.get("diagnostic", {}),
        },
        "market_data_freshness": {
            "last_scan_utc": active_status.get("last_scan_utc"),
            "discovered_markets_count": active_status.get("discovered_markets_count", 0),
            "latest_cycle_duration_ms": latest_telemetry.get("total_duration_ms") if latest_telemetry else None,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        },
        "cost_assumptions": {
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0005,
            "slippage_rate": 0.0005,
            "penetration_buffer_pct": 0.00025,
            "ttl_expiry_bars": 12,
        },
    }


@app.get("/api/model_b/research/signals")
def get_model_b_research_signals(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    symbol: Optional[str] = Query(None),
    direction: Optional[str] = Query(None),
):
    """Retrieve canonical research signals and their paired variant evaluations."""
    storage = model_b_runner.shadow_evaluator.storage if (model_b_runner and hasattr(model_b_runner, "shadow_evaluator")) else ResearchStorage(db)
    signals = storage.get_paired_signals_summary(limit=limit, offset=offset, symbol=symbol, direction=direction)
    return {
        "signals": signals,
        "count": len(signals),
        "limit": limit,
        "offset": offset,
        "symbol": symbol,
        "direction": direction,
    }


@app.get("/api/model_b/research/evaluations")
def get_model_b_research_evaluations(
    variant: str = Query(ResearchVariant.VARIANT_F_SHORT_COMBINED.value),
    direction: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Retrieve individual variant evaluations with optional status/direction filters."""
    storage = model_b_runner.shadow_evaluator.storage if (model_b_runner and hasattr(model_b_runner, "shadow_evaluator")) else ResearchStorage(db)
    evals = storage.get_evaluations_for_variant(variant=variant, direction=direction, status=status, limit=limit)
    return {
        "variant": variant,
        "direction": direction,
        "status": status,
        "count": len(evals),
        "evaluations": evals,
    }


@app.get("/api/model_b/research/status")
def get_model_b_research_status():
    """Retrieve runtime status comparing active paper execution and independent shadow evaluation."""
    storage = model_b_runner.shadow_evaluator.storage if (model_b_runner and hasattr(model_b_runner, "shadow_evaluator")) else ResearchStorage(db)
    open_orders = storage.get_open_shadow_orders()
    active_status = model_b_runner.get_status() if model_b_runner else {}

    return {
        "shadow_evaluator_active": True,
        "mode": "RESEARCH_SHADOW_SIMULATION",
        "open_shadow_orders_count": len(open_orders),
        "active_portfolio_halted": active_status.get("risk_limits", {}).get("daily_circuit_breaker_active", False)
        or active_status.get("risk_limits", {}).get("weekly_circuit_breaker_active", False),
        "active_portfolio_open_positions": active_status.get("risk_limits", {}).get("open_positions_count", 0),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/model_b/research/replay")
async def trigger_model_b_research_replay():
    """Trigger an on-demand historical candidate replay through the shadow evaluator."""
    storage = model_b_runner.shadow_evaluator.storage if (model_b_runner and hasattr(model_b_runner, "shadow_evaluator")) else ResearchStorage(db)
    evaluator = model_b_runner.shadow_evaluator if (model_b_runner and hasattr(model_b_runner, "shadow_evaluator")) else ModelBShadowEvaluator(db=db, storage=storage)

    with db.get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM setup_candidates WHERE model_id = 'MODEL_B' ORDER BY timestamp_ms ASC")
        rows = cur.fetchall()

    candidates = []
    for r in rows:
        d = dict(r)
        cand = SetupCandidate(
            candidate_id=d["candidate_id"],
            symbol=d["symbol"],
            timestamp_ms=d["timestamp_ms"],
            direction=Direction(d["direction"]),
            bias_4h=MarketBias(d["bias_4h"]),
            structure_1h=StructureState(d["structure_1h"]),
            entry_price=d["entry_price"],
            stop_loss=d["stop_loss"],
            take_profit_1=d["take_profit_1"],
            take_profit_2=d["take_profit_2"],
            gross_reward_to_risk=d["gross_rr"],
            net_reward_to_risk=d["net_rr"],
            invalidation_price=d["invalidation_price"],
            status=CandidateStatus(d["status"]),
            entry_category=EntryCategory(d.get("entry_category", "Reversal")),
            model_id="MODEL_B",
        )
        cand.details_json = d["details_json"]
        candidates.append(cand)

    symbols = list(set(c.symbol for c in candidates))
    adapter = AsterDexAdapter()
    candles_by_symbol = {}
    for sym in symbols:
        try:
            klines = adapter.get_klines(sym, "1h", limit=100)
            if klines:
                candles_by_symbol[sym] = klines
        except Exception:
            pass

    summary = await asyncio.to_thread(evaluator.replay_historical_candidates, candidates, candles_by_symbol)
    return {
        "status": "SUCCESS",
        "candidates_replayed": len(candidates),
        "symbols_count": len(symbols),
        "summary": {k: v.to_dict() for k, v in summary.items()},
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


# ==============================================================================
# AsterDEX Demand-Zone LONG Scanner REST API Endpoints
# ==============================================================================


class DemandZoneConfigUpdate(BaseModel):
    min_24h_volume_usdt: Optional[float] = None
    risk_budget_usdt: Optional[float] = None
    min_distance_to_demand_pct: Optional[float] = None
    max_distance_to_demand_pct: Optional[float] = None
    min_stop_loss_dist_pct: Optional[float] = None
    max_stop_loss_dist_pct: Optional[float] = None
    hard_max_stop_loss_pct: Optional[float] = None
    min_net_reward_to_risk: Optional[float] = None
    min_resistance_clearance_rr: Optional[float] = None
    btc_zone_lower: Optional[float] = None
    btc_zone_upper: Optional[float] = None
    btc_policy: Optional[str] = None
    leverage: Optional[float] = None


@app.get("/api/demand_zone/candidates")
async def get_demand_zone_candidates(
    status: Optional[str] = Query(None, description="Filter by candidate status (e.g. READY, APPROACHING, WATCHLIST, INFEASIBLE, INVALIDATED)"),
    symbol: Optional[str] = Query(None, description="Filter by symbol (e.g. BTCUSDT)"),
    min_score: float = Query(0.0, description="Minimum composite setup score"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Retrieve AsterDEX Demand-Zone LONG candidate setups with filtering and counts."""
    candidates = demand_zone_storage.get_recent_candidates(
        limit=limit,
        status=status,
        symbol=symbol,
        min_score=min_score,
    )
    if not candidates and demand_zone_scanner.latest_candidates:
        filtered = demand_zone_scanner.latest_candidates
        if status:
            filtered = [c for c in filtered if c.status.value == status.upper()]
        if symbol:
            filtered = [c for c in filtered if c.symbol.upper() == symbol.upper()]
        if min_score > 0:
            filtered = [c for c in filtered if c.composite_setup_score >= min_score]
        candidates = [c.to_dict() for c in filtered[offset : offset + limit]]

    all_recent = demand_zone_storage.get_recent_candidates(limit=500) or [c.to_dict() for c in demand_zone_scanner.latest_candidates]
    status_counts = {
        "READY": sum(1 for c in all_recent if c.get("status") == "READY"),
        "APPROACHING": sum(1 for c in all_recent if c.get("status") == "APPROACHING"),
        "WATCHLIST": sum(1 for c in all_recent if c.get("status") == "WATCHLIST"),
        "INFEASIBLE": sum(1 for c in all_recent if c.get("status") == "INFEASIBLE"),
        "INVALIDATED": sum(1 for c in all_recent if c.get("status") == "INVALIDATED"),
    }

    return {
        "status": "SUCCESS",
        "total_count": len(all_recent),
        "status_counts": status_counts,
        "returned_count": len(candidates),
        "candidates": candidates,
        "btc_context": demand_zone_scanner.latest_btc_context.to_dict() if demand_zone_scanner.latest_btc_context else None,
        "telemetry": demand_zone_scanner.latest_telemetry,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/demand_zone/btc_context")
async def get_demand_zone_btc_context():
    """Get current BTC Market Context, zone proximity, policy evaluation, and observation history."""
    btc_ctx = demand_zone_scanner.latest_btc_context
    if not btc_ctx:
        try:
            bulk = demand_zone_scanner.adapter.get_bulk_market_snapshot()
            btc_snap = bulk.get("BTCUSDT", {})
            btc_px = float(btc_snap.get("last_price") or btc_snap.get("mid_price") or 80000.0)
            klines = demand_zone_scanner.adapter.get_klines("BTCUSDT", "1h", limit=30)
            btc_ctx = demand_zone_scanner.btc_evaluator.evaluate(btc_px, klines)
            demand_zone_scanner.latest_btc_context = btc_ctx
        except Exception as e:
            logger.warning(f"Failed to fetch on-demand BTC context: {e}")

    return {
        "status": "SUCCESS",
        "btc_context": btc_ctx.to_dict() if btc_ctx else None,
        "history": demand_zone_scanner.btc_evaluator.history,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/demand_zone/config")
async def get_demand_zone_config():
    """Retrieve active Demand-Zone scanner configuration."""
    return {
        "status": "SUCCESS",
        "config": demand_zone_scanner.config.to_dict(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/demand_zone/config")
async def update_demand_zone_config(req: DemandZoneConfigUpdate):
    """Update Demand-Zone scanner configuration parameters."""
    c = demand_zone_scanner.config
    if req.min_24h_volume_usdt is not None:
        c.min_24h_volume_usdt = float(req.min_24h_volume_usdt)
    if req.risk_budget_usdt is not None:
        c.risk_budget_usdt = float(req.risk_budget_usdt)
    if req.min_distance_to_demand_pct is not None:
        c.min_distance_to_demand_pct = float(req.min_distance_to_demand_pct)
    if req.max_distance_to_demand_pct is not None:
        c.max_distance_to_demand_pct = float(req.max_distance_to_demand_pct)
    if req.min_stop_loss_dist_pct is not None:
        c.min_stop_loss_dist_pct = float(req.min_stop_loss_dist_pct)
    if req.max_stop_loss_dist_pct is not None:
        c.max_stop_loss_dist_pct = float(req.max_stop_loss_dist_pct)
    if req.hard_max_stop_loss_pct is not None:
        c.hard_max_stop_loss_pct = float(req.hard_max_stop_loss_pct)
    if req.min_net_reward_to_risk is not None:
        c.min_net_reward_to_risk = float(req.min_net_reward_to_risk)
    if req.min_resistance_clearance_rr is not None:
        c.min_resistance_clearance_rr = float(req.min_resistance_clearance_rr)
    if req.btc_zone_lower is not None:
        c.btc_zone_lower = float(req.btc_zone_lower)
    if req.btc_zone_upper is not None:
        c.btc_zone_upper = float(req.btc_zone_upper)
    if req.leverage is not None:
        c.leverage = float(req.leverage)
    if req.btc_policy is not None:
        p_str = req.btc_policy.strip()
        if p_str in ("A", "OBSERVE_CONTINUOUSLY", "Policy A"):
            c.btc_policy = BTCZonePolicy.OBSERVE_CONTINUOUSLY
        elif p_str in ("B", "PRIORITIZE_ON_APPROACH", "Policy B"):
            c.btc_policy = BTCZonePolicy.PRIORITIZE_ON_APPROACH
        elif p_str in ("C", "REQUIRE_CONFIRMATION", "Policy C"):
            c.btc_policy = BTCZonePolicy.REQUIRE_CONFIRMATION
        else:
            try:
                c.btc_policy = BTCZonePolicy(p_str)
            except ValueError:
                pass

    # Propagate updated config to sub-engines
    demand_zone_scanner.btc_evaluator.config = c
    demand_zone_scanner.zone_detector.config = c
    demand_zone_scanner.risk_sizer.config = c
    demand_zone_scanner.tp_planner.config = c
    demand_zone_scanner.ob_analyzer.config = c

    return {
        "status": "SUCCESS",
        "updated_config": c.to_dict(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/demand_zone/scan")
async def trigger_demand_zone_scan(
    max_symbols: int = Query(50, ge=5, le=100, description="Max symbols to evaluate"),
):
    """Trigger an on-demand scan across AsterDEX USDT perpetual markets."""
    candidates = await asyncio.to_thread(
        demand_zone_scanner.scan_universe,
        max_symbols_to_evaluate=max_symbols,
    )
    return {
        "status": "SUCCESS",
        "candidates_count": len(candidates),
        "telemetry": demand_zone_scanner.latest_telemetry,
        "ready_count": sum(1 for c in candidates if c.status == DemandZoneCandidateStatus.READY),
        "approaching_count": sum(1 for c in candidates if c.status == DemandZoneCandidateStatus.APPROACHING),
        "watchlist_count": sum(1 for c in candidates if c.status == DemandZoneCandidateStatus.WATCHLIST),
        "infeasible_count": sum(1 for c in candidates if c.status == DemandZoneCandidateStatus.INFEASIBLE),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/demand_zone/telemetry")
async def get_demand_zone_telemetry():
    """Retrieve latest scan telemetry and audit metrics."""
    return {
        "status": "SUCCESS",
        "telemetry": demand_zone_scanner.latest_telemetry,
        "recent_scans": demand_zone_storage.get_recent_scans(limit=10),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


# ==============================================================================
# Multi-Exchange Perpetual Futures Market Screener REST API Endpoints
# ==============================================================================


@app.get("/api/screener/markets")
async def get_screener_markets(
    search: Optional[str] = Query(None, description="Search symbol or base asset"),
    min_volume_usdt: float = Query(1000000.0, ge=0.0, description="Minimum 24h quote volume turnover"),
    max_volume_usdt: Optional[float] = Query(None, ge=0.0, description="Optional maximum volume turnover"),
    min_oi_usdt: Optional[float] = Query(None, ge=0.0, description="Optional minimum open interest notional"),
    max_oi_usdt: Optional[float] = Query(None, ge=0.0, description="Optional maximum open interest notional"),
    funding_direction: Optional[str] = Query(None, description="Filter funding direction: ALL, POSITIVE, NEGATIVE"),
    min_funding_abs_pct: Optional[float] = Query(None, ge=0.0, description="Minimum absolute funding percentage"),
    max_spread_bps: Optional[float] = Query(None, ge=0.0, description="Maximum bid-ask spread in bps"),
    exchanges: Optional[str] = Query(None, description="Comma-separated exchanges to include"),
    cross_listed_only: bool = Query(False, description="Filter assets listed on >= 2 venues"),
    sort_by: str = Query("volume_desc", description="Sort field and direction"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(25, ge=5, le=100, description="Items per page"),
    force_refresh: bool = Query(False, description="Force fresh network fetch"),
):
    """Retrieve filtered, sorted, paginated multi-exchange perpetual market assets."""
    ex_list = [e.strip().lower() for e in exchanges.split(",") if e.strip()] if exchanges else None
    params = ScreenerFilterParams(
        search=search,
        min_volume_usdt=min_volume_usdt,
        max_volume_usdt=max_volume_usdt,
        min_oi_usdt=min_oi_usdt,
        max_oi_usdt=max_oi_usdt,
        funding_direction=funding_direction,
        min_funding_abs_pct=min_funding_abs_pct,
        max_spread_bps=max_spread_bps,
        exchanges=ex_list,
        cross_listed_only=cross_listed_only,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )
    result = await asyncio.to_thread(market_screener.filter_and_paginate, params=params, force_refresh=force_refresh)
    return result


@app.get("/api/screener/venues")
async def get_screener_venues():
    """Retrieve connection telemetry and contract counts for all monitored exchanges."""
    return {
        "status": "SUCCESS",
        "venues": market_screener.get_venue_telemetry(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/screener/refresh")
async def refresh_screener_data():
    """Trigger manual cache invalidation and fresh multi-exchange fetch."""
    await asyncio.to_thread(market_screener.refresh_all_venues, force=True)
    return {
        "status": "SUCCESS",
        "venues_telemetry": market_screener.get_venue_telemetry(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


# ==============================================================================
# Groq AI Trading Analyst REST API Endpoints
# ==============================================================================


@app.post("/api/ai_analyst/analyze")
async def analyze_with_ai(req: AnalysisRequest):
    """Trigger manual qualitative and quantitative market structure analysis via Groq LLM."""
    try:
        result = await asyncio.to_thread(
            ai_analyst_service.analyze_symbol,
            symbol=req.symbol,
            timeframe=req.timeframe,
            risk_budget_usdt=req.risk_budget_usdt,
            force_refresh=req.force_refresh,
            model_id_override=req.model_id,
        )
        return {
            "status": "SUCCESS",
            "result": result,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"AI Analyst error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ai_analyst/history")
async def get_ai_analyst_history(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    timeframe: Optional[str] = Query(None, description="Filter by timeframe (15m, 1h, 4h, 1d)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Retrieve historical AI market analysis records."""
    records = ai_analyst_storage.get_history(symbol=symbol, timeframe=timeframe, limit=limit, offset=offset)
    return {
        "status": "SUCCESS",
        "total_returned": len(records),
        "history": records,
        "records": records,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/ai_analyst/symbols")
async def get_ai_analyst_symbols():
    """Retrieve list of eligible AsterDEX perpetual symbols for AI analysis."""
    symbols = ai_analyst_service.get_supported_symbols()
    return {
        "status": "SUCCESS",
        "symbols": symbols,
        "count": len(symbols),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/ai_analyst/config")
async def get_ai_analyst_config():
    """Get active AI Analyst configuration (keys masked)."""
    return {
        "status": "SUCCESS",
        "config": ai_analyst_service.config.to_dict(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/ai_analyst/config", dependencies=[Depends(require_mutating_api_access)])
async def update_ai_analyst_config(req: AIConfigUpdateRequest):
    """Update Groq model ID, API key, and risk budget."""
    updated = ai_analyst_service.update_config(
        groq_api_key=req.groq_api_key,
        groq_model_id=req.groq_model_id,
        risk_budget_usdt=req.default_risk_budget_usdt,
        default_leverage=req.default_leverage,
    )
    return {
        "status": "SUCCESS",
        "config": updated,
        "updated_config": updated,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/ai_analyst/{analysis_id}")
async def get_ai_analysis_by_id(analysis_id: str):
    """Retrieve a single AI analysis record by ID."""
    record = ai_analyst_storage.get_by_id(analysis_id)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis record not found")
    return {
        "status": "SUCCESS",
        "result": record,
        "record": record,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/research/data/sources")
async def research_sources_endpoint() -> Dict[str, Any]:
    with db.get_connection() as conn:
        return {"sources": [dict(row) for row in conn.execute("SELECT * FROM research_sources ORDER BY source_id").fetchall()]}


@app.get("/api/research/data/datasets")
async def research_datasets_endpoint() -> Dict[str, Any]:
    with db.get_connection() as conn:
        return {"datasets": [dict(row) for row in conn.execute("SELECT * FROM research_datasets ORDER BY dataset_id").fetchall()]}


@app.get("/api/research/data/{dataset_id}")
async def research_data_endpoint(dataset_id: str, symbol: Optional[str] = Query(None), limit: int = Query(500, ge=1, le=5000)) -> Dict[str, Any]:
    rows = research_store.list_observations(dataset_id, symbol=symbol, limit=limit)
    sync = research_store.get_sync_state(dataset_id)
    total = research_store.total_observation_count(dataset_id)
    status = (sync or {}).get("sync_status") or ("MISSING" if not rows else "AVAILABLE")
    return {"dataset_id": dataset_id, "observations": rows, "status": status, "sync": sync, "inserted_count": (sync or {}).get("observation_count", 0), "total_observation_count": total}


@app.get("/api/research/data/{dataset_id}/audit")
async def research_audit_endpoint(dataset_id: str, start: str = Query(...), end: str = Query(...)) -> Dict[str, Any]:
    try:
        from datetime import date
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        if start_date > end_date:
            raise ValueError("start date must not be after end date")
        return audit_dataset(db, dataset_id, start_date, end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/research/retail_positioning")
async def retail_positioning_endpoint(symbol: str = Query("BTCUSDT"), period: str = Query("1h"), limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    return await asyncio.to_thread(retail_positioning_provider.get, symbol, period, limit)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api.app:app", host=cfg.host, port=cfg.port, reload=False)
