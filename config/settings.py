"""Configuration management for SMC-SNR ML Perpetual Setup Scanner.

Driven by Pydantic models with support for YAML configuration files and environment overrides.
"""

from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from src.risk.tier import EquityTierConfig

# Automatically load environment variables from .env file if present
load_dotenv()



class ExecutionMode(str, Enum):
    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    LIVE = "LIVE"


class ExchangeConfig(BaseModel):
    name: str = "Aster DEX"
    base_url: str = "https://fapi.asterdex.com"
    request_timeout: int = 15
    max_retries: int = 4
    retry_delay_sec: float = 1.0
    rate_limit_calls_per_min: int = 600
    maker_fee_rate: float = 0.0002   # 0.02%
    taker_fee_rate: float = 0.0005   # 0.05%
    slippage_rate: float = 0.0005    # 0.05%


class RiskConfig(BaseModel):
    initial_equity_usdt: float = Field(default=16.0, gt=0)
    margin_mode: str = Field(default="ISOLATED", description="Must be ISOLATED")
    max_leverage: int = Field(default=10, ge=1, le=10)
    target_isolated_margin_usdt: float = Field(default=1.00, ge=0.5, le=10.0, description="Target isolated margin per trade ($1.00-$1.50)")
    target_isolated_margin_max_usdt: float = Field(default=1.50, ge=0.5, le=10.0, description="Max target isolated margin ($1.50)")
    hard_risk_limit_usdt: float = Field(default=1.50, gt=0, description="Hard risk limit per trade ceiling in USDT")
    risk_per_trade_usdt: float = Field(default=0.25, gt=0, description="Nominal risk per trade in USDT")
    daily_loss_limit_usdt: float = Field(default=0.50, gt=0, description="Tier 1 daily loss cap ($0.50)")
    weekly_loss_limit_usdt: float = Field(default=1.25, gt=0, description="Approved Tier 1 weekly loss cap ($1.25)")
    max_open_positions: int = Field(default=3, ge=1, le=10)
    max_entries_per_week: Optional[int] = Field(default=None, description="Deprecated weekly entry hard cap; entry frequency governed by risk controls")
    min_notional_usdt: float = Field(default=5.0, gt=0)
    buffer_pct_for_fees: float = Field(default=0.02, ge=0)
    equity_tier: EquityTierConfig = Field(default_factory=EquityTierConfig)

    @field_validator("margin_mode")
    @classmethod
    def validate_margin_mode(cls, v: str) -> str:
        if v.upper() != "ISOLATED":
            raise ValueError("Only ISOLATED margin mode is permitted.")
        return v.upper()


class StrategyConfig(BaseModel):
    execution_timeframe: str = "1h"
    bias_timeframe: str = "4h"
    swing_left_bars: int = 3
    swing_right_bars: int = 3
    atr_period: int = 14
    sl_buffer_atr_multiple: float = 0.5
    min_reward_to_risk: float = 1.5
    fvg_min_size_atr_ratio: float = 0.1
    sr_cluster_tolerance_pct: float = 0.015
    # Tuned SMC & Dealing Range Parameters (MODEL_A_PAPER_V2)
    premium_threshold: float = 0.70
    discount_threshold: float = 0.30
    min_extension_48h: float = 0.035
    min_impulse_12h: float = 0.012
    short_macro_cap_168h: float = 0.120
    bull_breakout_pos_72h: float = 0.85
    bull_breakout_ret_24h: float = 0.035
    bull_breakout_ret_48h: float = 0.045
    bull_breakout_ret_168h: float = 0.060
    bull_variance_ratio_min: float = 1.05
    full_exit_at_tp1: bool = True
    tp1_rr_multiple: float = 1.65
    min_stop_distance_pct: float = 0.012


class MLConfig(BaseModel):
    enabled: bool = True
    model_type: str = "lightgbm"  # "lightgbm" or "logistic_regression"
    label_horizon_bars: int = 12
    train_split: float = 0.60
    val_split: float = 0.20
    test_split: float = 0.20
    calibration_method: str = "isotonic"  # "isotonic" or "sigmoid"
    min_confidence_threshold: float = 0.55
    min_samples_required: int = 100


class AIMode(str, Enum):
    NO_AI = "NO_AI"
    AI_ANALYSIS = "AI_ANALYSIS"
    SHADOW = "SHADOW"


class AIConfig(BaseModel):
    enabled: bool = False
    mode: AIMode = AIMode.NO_AI
    api_url: str = "https://4810-35-253-184-120.ngrok-free.app/generate"
    api_token: Optional[str] = None
    model_id: str = "Qwen/Qwen2.5-7B-Instruct"
    connect_timeout_sec: float = 3.0
    read_timeout_sec: float = 15.0
    max_tokens: int = 400
    max_retries: int = 2
    retry_delay_sec: float = 0.5


DEFAULT_WATCHLIST = [
    "ADAUSDT",
    "XRPUSDT",
    "BTCUSDT",
    "BNBUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "XMRUSDT",
    "DOGEUSDT",
    "LTCUSDT",
]


class PaperExecutionConfig(BaseModel):
    enabled: bool = True
    slippage_rate: float = Field(default=0.0005, ge=0.0001, le=0.005)  # 0.05% default (0.02%-0.06% typical)
    maker_fee_rate: float = Field(default=0.0002, ge=0.0)             # 0.02% maker
    taker_fee_rate: float = Field(default=0.0005, ge=0.0)             # 0.05% taker
    pending_expiry_bars: int = Field(default=6, ge=1)                 # 6 1H bars expiry (aligned with pending_order_ttl_hours=6.0)
    conservative_fill_buffer_pct: float = Field(default=0.00025, ge=0.0)  # requires price penetration past entry
    discord_webhook_url: Optional[str] = None
    enable_tp1_trailing_stop: bool = Field(
        default=True,
        description="Enable partial close at TP1, SL move to fee-adjusted break-even, and dynamic trailing stop to TP2",
    )
    tp1_partial_close_pct: float = Field(
        default=0.50, ge=0.0, le=1.0, description="Fraction of position closed at TP1 when TP2 is available"
    )
    trailing_stop_distance_r: float = Field(
        default=1.0, gt=0.0, description="Trailing stop distance expressed in multiples of initial risk R"
    )
    move_sl_to_breakeven_on_tp1: bool = Field(
        default=True, description="Automatically move stop-loss to fee-adjusted break-even when TP1 is hit"
    )
    full_exit_at_tp1: bool = Field(
        default=True,
        description="When True, closes 100% of the position immediately at take_profit_1 with no trailing runner",
    )


class ModelBLiveMicroConfig(BaseModel):
    """Dedicated live micro-account configuration (~$16 USDT Aster account)."""
    target_trade_risk_usdt: float = Field(default=0.45, gt=0.0, description="Target live risk per trade")
    max_margin_usdt: float = Field(default=4.00, gt=0.0, description="Max isolated margin per live trade")
    daily_loss_limit_usdt: float = Field(default=1.35, gt=0.0, description="Daily circuit breaker (3R)")
    weekly_loss_limit_usdt: float = Field(default=3.00, gt=0.0, description="Weekly circuit breaker")
    max_open_positions: int = Field(default=2, ge=1, le=5, description="Max concurrent live positions")
    max_open_per_direction: int = Field(default=1, ge=1, le=5, description="Max live positions per direction")
    min_notional_buffer: float = Field(default=5.50, gt=0.0, description="Aster DEX min_notional buffer")
    leverage: int = Field(default=10, ge=1, le=20, description="Live micro-account leverage")


class ModelBConfig(BaseModel):
    """Configuration for Model B Aggressive Liquidity Research Engine."""
    enabled: bool = Field(default=True, description="Enable Model B liquidity engine scanner")
    model_id: str = "MODEL_B"
    version: str = "1.0.0-alpha"
    trigger_timeframe: str = Field(default="1h", description="Trigger candle timeframe (e.g. '1h' or '15m')")
    trend_timeframe: str = Field(default="4h", description="Higher-timeframe EMA trend alignment timeframe (e.g. '4h' or '1h')")
    pending_expiry_bars: int = Field(default=6, ge=1, le=100, description="Pending limit order TTL in trigger bars")
    initial_equity_usdt: float = Field(default=100.0, gt=0.0, description="Independent Model B paper equity")
    target_account_equity_usdt: float = Field(default=100.0, gt=0.0, description="Target account equity reference")
    target_trade_risk_usdt: float = Field(default=1.00, gt=0.0, description="Target dollar risk per trade")
    daily_loss_limit_usdt: float = Field(default=3.00, gt=0.0, description="Daily circuit breaker loss limit")
    weekly_loss_limit_usdt: float = Field(default=7.50, gt=0.0, description="Weekly circuit breaker loss limit")
    max_open_positions: int = Field(default=6, ge=1, le=10, description="Max concurrent open paper positions (tactical V2 limit)")
    max_open_per_direction: int = Field(default=3, ge=1, le=10, description="Max concurrent open paper positions per direction (tactical V2 limit)")
    cohort_version: str = Field(default="MODEL_B_V2_REMEDIATED", description="Active Model B forward cohort tag")
    cohort_cutover_ms: int = Field(default=1792838400000, description="Model B V2 cutover timestamp in ms")
    # Live Micro-Account Profile (~$16 USDT)
    live_target_trade_risk_usdt: float = Field(default=0.45, gt=0.0, description="Live target risk per trade (~$16 profile)")
    live_max_margin_usdt: float = Field(default=4.00, gt=0.0, description="Live max margin per position")
    live_daily_loss_limit_usdt: float = Field(default=1.35, gt=0.0, description="Live daily loss breaker (3R)")
    live_weekly_loss_limit_usdt: float = Field(default=3.00, gt=0.0, description="Live weekly loss breaker")
    live_max_open_positions: int = Field(default=2, ge=1, le=5, description="Live max concurrent positions")
    live_max_open_per_direction: int = Field(default=1, ge=1, le=5, description="Live max concurrent per direction")
    max_leverage: int = Field(default=10, ge=1, le=20, description="Max leverage allowed")
    max_margin_usdt: float = Field(default=15.00, gt=0.0, description="Max isolated margin allowed per position")
    min_risk_reward: float = Field(default=1.5, gt=0.0, description="Minimum net reward-to-risk ratio")
    max_spread_bps: float = Field(default=15.0, gt=0.0, description="Max allowable bid-ask spread in bps")
    min_depth_imbalance: float = Field(default=0.20, ge=0.0, le=1.0, description="Minimum order book depth imbalance")
    min_imbalance_ratio: float = Field(default=0.05, description="Minimum depth imbalance ratio for signal confirmation")
    min_absorption_score: float = Field(default=0.25, description="Minimum volume absorption score")
    pending_order_ttl_hours: float = Field(default=6.0, gt=0.0, description="Simulated limit order TTL in hours")
    min_24h_volume_usdt: float = Field(default=1000000.0, ge=0.0, description="Minimum 24h volume filter")
    stage1_min_24h_volume_usdt: float = Field(default=50000.0, ge=0.0, description="Stage 1 bulk screen minimum 24h volume filter")
    stage1_max_spread_bps: float = Field(default=35.0, ge=1.0, description="Stage 1 bulk screen maximum spread in bps")
    max_deep_eval_symbols: int = Field(default=35, ge=5, le=100, description="Max symbols deep evaluated per cycle")
    scanner_max_workers: int = Field(default=8, ge=1, le=20, description="ThreadPoolExecutor concurrency for candle/depth fetches")
    rotation_slice_size: int = Field(default=25, ge=1, le=50, description="Number of rotating mid-cap symbols per cycle")
    anchor_symbol_count: int = Field(default=10, ge=1, le=20, description="Number of top-volume anchor symbols evaluated every cycle")
    diagnostic_breaker_override: bool = Field(default=False, description="TEMPORARY Model B paper-only diagnostic: bypass daily/weekly loss breaker entry blocks. Baseline limits are NOT altered. Env: MODEL_B_DIAGNOSTIC_BREAKER_OVERRIDE")
    entry_kill_switch: bool = Field(default=False, description="Immediately blocks all new Model B paper entries. Env: MODEL_B_ENTRY_KILL_SWITCH")
    short_regime_gate_enabled: bool = Field(default=True, description="Require actual closed-bar HTF/EMA/CVD alignment for SHORT paper entries")
    min_stop_distance_pct: float = Field(default=0.015, ge=0.0, le=0.10)
    min_stop_atr_multiple: float = Field(default=1.25, ge=0.0, le=10.0)
    short_cooldown_minutes: int = Field(default=240, ge=0, le=10080)
    short_reentry_min_displacement_atr: float = Field(default=0.5, ge=0.0, le=10.0)
    scan_interval_sec: int = Field(default=60, ge=5, le=3600, description="Evaluation loop cadence")
    enabled_venues: List[str] = Field(
        default_factory=lambda: ["asterdex", "binance", "hyperliquid"],
        description="Active exchanges for universal perpetual market discovery",
    )
    # Engine 1: Liquidity Wall Hunter
    wall_min_persistence_sec: int = Field(default=15, description="Minimum wall persistence duration")
    wall_depth_ratio_threshold: float = Field(default=3.0, description="Minimum multiple over instrument baseline depth")
    # Engine 2: New Market Opportunity Scanner
    new_market_max_age_days: int = Field(default=14, description="Maximum listing age in days to qualify as new market")
    # Engine 3: News & Catalyst Engine
    enable_catalyst_engine: bool = Field(default=True, description="Enable news and catalyst intelligence")
    catalyst_min_confidence: float = Field(default=0.70, description="Minimum catalyst confidence score")
    discord_webhook_url: Optional[str] = Field(
        default=None, description="Optional Discord webhook for Model B notifications"
    )
    # Light-Touch V3 Tuned Parameters (Proven on 1H and 15m)
    min_rejection_wick_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum rejection wick ratio (wick / (high-low)) required for sweep-reclaim confirmation",
    )
    min_volume_sma_mult: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description="Minimum trigger candle volume multiple over 20-period volume SMA",
    )
    full_exit_at_tp1: bool = Field(
        default=True,
        description="When True, close 100% of position at TP1 with no trailing runner",
    )
    tp1_rr_multiple: float = Field(
        default=1.50,
        ge=1.0,
        le=5.0,
        description="Take profit 1 reward-to-risk multiple (default 1.50R for 1H, 1.65R for V3)",
    )
    symbol_blacklist: List[str] = Field(
        default_factory=list,
        description="Symbols excluded from Model B evaluation (e.g. structural chop pairs)",
    )
    allowed_directions: List[str] = Field(
        default_factory=lambda: ["LONG", "SHORT"],
        description="Permitted trade directions (e.g. ['LONG'] for long-only bias)",
    )
    require_confirmed_body: bool = Field(
        default=True,
        description="When True, require candle close > open for Long, close < open for Short (V2). When False, use original responsive V1 sweep-reclaim.",
    )
    require_htf_ema_alignment: bool = Field(
        default=True,
        description="When True, enforces HTF EMA trend alignment veto. When False (V1-Pro), restores responsive mean-reversion bottom sweep entries.",
    )


def create_model_b_fast_config(base_config: Optional[ModelBConfig] = None) -> ModelBConfig:
    """Create the parallel Fast Scalping instance configuration (MODEL_B_FAST: 15m Trigger / 1H Trend)."""
    data = base_config.model_dump() if base_config is not None else {}
    data.update({
        "enabled": True,
        "model_id": "MODEL_B_FAST",
        "cohort_version": "MODEL_B_FAST_V2",
        "trigger_timeframe": "15m",
        "trend_timeframe": "1h",
        "min_stop_distance_pct": 0.008,
        "min_stop_atr_multiple": 1.25,
        "pending_expiry_bars": 8,
        "pending_order_ttl_hours": 2.0,
        "initial_equity_usdt": 100.0,
        "target_account_equity_usdt": 100.0,
        "target_trade_risk_usdt": 1.00,
        "max_open_positions": 2,
        "max_open_per_direction": 1,
        "daily_loss_limit_usdt": 3.00,
        "weekly_loss_limit_usdt": 7.50,
        "min_rejection_wick_ratio": 0.0,
        "min_volume_sma_mult": 0.0,
        "full_exit_at_tp1": False,
        "tp1_rr_multiple": 1.50,
        "symbol_blacklist": [],
    })
    return ModelBConfig(**data)


def create_model_b_v3_config(base_config: Optional[ModelBConfig] = None) -> ModelBConfig:
    """Create the parallel V1-Pro instance configuration (MODEL_B_V3: 15m V1 Responsive Core + Long-Only + Correlation Protection)."""
    data = base_config.model_dump() if base_config is not None else {}
    data.update({
        "enabled": True,
        "model_id": "MODEL_B_V3",
        "cohort_version": "MODEL_B_V1_PRO",
        "trigger_timeframe": "15m",
        "trend_timeframe": "1h",
        "min_stop_distance_pct": 0.010,
        "min_stop_atr_multiple": 1.25,
        "pending_expiry_bars": 8,
        "pending_order_ttl_hours": 2.0,
        "initial_equity_usdt": 100.0,
        "target_account_equity_usdt": 100.0,
        "target_trade_risk_usdt": 1.00,
        "max_open_positions": 2,
        "max_open_per_direction": 1,
        "daily_loss_limit_usdt": 3.00,
        "weekly_loss_limit_usdt": 7.50,
        "require_confirmed_body": False,
        "require_htf_ema_alignment": False,
        "allowed_directions": ["LONG"],
        "min_rejection_wick_ratio": 0.0,
        "min_volume_sma_mult": 0.0,
        "full_exit_at_tp1": True,
        "tp1_rr_multiple": 1.50,
        "symbol_blacklist": [],
    })
    return ModelBConfig(**data)


create_model_b_v1_pro_config = create_model_b_v3_config


class ModelCConfig(BaseModel):
    """Configuration and Simulation Parameters for Model C (Pump.fun Rapid-Execution Research).
    
    Strictly paper research only. No live Solana wallet or private keys are utilized.
    """
    enabled: bool = Field(default=False, description="Enable Model C pump.fun simulation scanner")
    model_id: str = "MODEL_C"
    version: str = "0.1.0-spec"
    initial_equity_usdt: float = Field(default=50.0, gt=0.0, description="Model C paper portfolio equity in USD equivalent")
    target_trade_risk_usdt: float = Field(default=0.50, gt=0.0, description="Target risk per paper trade")
    daily_loss_limit_usdt: float = Field(default=2.00, gt=0.0, description="Daily circuit breaker limit")
    weekly_loss_limit_usdt: float = Field(default=5.00, gt=0.0, description="Weekly circuit breaker limit")
    max_open_positions: int = Field(default=2, ge=1, le=5, description="Maximum simultaneous open token positions")
    max_leverage: int = Field(default=1, ge=1, le=1, description="Always 1x spot; NO leverage or margin on spot meme tokens")
    max_holding_time_minutes: int = Field(default=15, ge=1, le=120, description="Max holding time before mandatory emergency paper exit")
    simulated_slippage_bps: float = Field(default=250.0, ge=50.0, description="Base simulated slippage in bps (2.5% to 25%+)")
    simulated_priority_fee_sol: float = Field(default=0.005, description="Simulated Jito/Priority fee per transaction in SOL")
    min_bonding_curve_pct: float = Field(default=10.0, description="Minimum bonding curve progress threshold")
    max_bonding_curve_pct: float = Field(default=90.0, description="Maximum bonding curve progress before Raydium migration danger")
    max_dev_holding_pct: float = Field(default=10.0, description="Maximum allowable developer wallet holding percentage")
    max_top10_holding_pct: float = Field(default=25.0, description="Maximum allowable top 10 holders concentration percentage")
    helius_rpc_url: Optional[str] = Field(default=None, description="Optional public/private Solana RPC endpoint for metadata reading")
    jito_block_engine_url: Optional[str] = Field(default=None, description="Optional Jito block engine endpoint for bundle simulation")


class ModelDConfig(BaseModel):
    """Independent, paper-only configuration for Whale & Order Flow research."""
    enabled: bool = Field(default=False, description="Enable Model D research collection and evaluation")
    paper_only: bool = Field(default=True, description="Hard invariant: Model D never places exchange orders")
    collector_enabled: bool = Field(default=False, description="Enable Model D public market-data collection")
    whale_tracking_enabled: bool = Field(default=True, description="Enable observable trade and resting-liquidity analysis")
    wallet_tracking_enabled: bool = Field(default=False, description="Enable only when a documented chain source is configured")
    model_id: str = "MODEL_D"
    version: str = "0.1.0-research"
    symbols: List[str] = Field(default_factory=list, description="Empty means use the configured perpetual universe")
    decision_timeframe: str = "1h"
    max_event_age_sec: int = Field(default=30, ge=1)
    max_book_age_sec: int = Field(default=10, ge=1)
    trade_reference_window: int = Field(default=200, ge=20)
    wall_reference_window: int = Field(default=50, ge=10)
    shadow_horizon_bars: int = Field(default=12, ge=1)
    simulated_fill_model: str = "conservative_midpoint"
    market_primary_venue: str = Field(default="binance", pattern="^(binance|asterdex)$")
    wallet_scanner_enabled: bool = Field(default=False, description="Enable multi-chain wallet discovery")
    wallet_discovery_target: int = Field(default=1000, ge=1, le=100000)
    wallet_enrichment_enabled: bool = True
    wallet_ranking_enabled: bool = True
    wallet_max_concurrency: int = Field(default=2, ge=1, le=20)
    wallet_scan_interval_seconds: int = Field(default=3600, ge=60)
    wallet_rpc_urls: Dict[int, Optional[str]] = Field(default_factory=lambda: {1: None, 56: None, 42161: None, 8453: None})
    wallet_confirmation_depths: Dict[int, int] = Field(default_factory=lambda: {1: 12, 56: 15, 42161: 20, 8453: 20})


class GroqAutoTraderConfig(BaseModel):
    """Configuration for Groq AI 10 -> 3 -> 1 Funnel Screener & Auto-Execution Pipeline."""
    api_key: Optional[str] = Field(default=None, description="Groq API Key (Free tier)")
    model: str = Field(default="qwen/qwen3.8-27b", description="Primary Groq model ID")
    base_url: str = Field(default="https://api.groq.com/openai/v1", description="Groq API endpoint URL")
    auto_execution_venue: str = Field(default="PAPER", description="Default execution venue: PAPER or LIVE")
    temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    timeout_sec: float = Field(default=25.0, ge=5.0, le=60.0)


class LiveMicroRiskConfig(BaseModel):
    fixed_risk_usd: float = Field(default=0.20, description="Fixed dollar risk per trade ($0.20). Not a percentage of equity.")
    max_filled_entries_per_day: int = Field(default=3, ge=1, le=10)
    daily_loss_limit_usd: float = Field(default=0.45, ge=0.10)
    max_drawdown_from_equity_peak_pct: float = Field(default=25.0, ge=5.0, le=50.0)
    max_global_open_positions: int = Field(default=1, ge=1, le=1)
    max_isolated_margin_usd: float = Field(default=7.00, ge=1.0, le=20.0)
    max_isolated_margin_equity_pct: float = Field(default=45.0, ge=10.0, le=100.0)
    leverage: int = Field(default=10, ge=1, le=10)
    margin_mode: str = Field(default="isolated", pattern="^(isolated|ISOLATED)$")

    @field_validator("fixed_risk_usd")
    @classmethod
    def lock_fixed_risk(cls, v: float) -> float:
        if v > 0.20:
            raise ValueError(f"LIVE_MICRO_V1 fixed risk is locked at $0.20 USD max. Configured: {v}")
        return v

    @property
    def max_drawdown_from_peak_pct(self) -> float:
        return self.max_drawdown_from_equity_peak_pct

    @property
    def max_margin_usd(self) -> float:
        return self.max_isolated_margin_usd


class LiveMicroStrategyConfig(BaseModel):
    live_strategy_id: str = Field(default="15M_SWEEP_RECLAIM_SMC")
    shadow_strategy_id: str = Field(default="SWEEP_1M_V1")
    shadow_strategy_live_enabled: bool = Field(default=False)

    @field_validator("shadow_strategy_live_enabled")
    @classmethod
    def lock_shadow_mode(cls, v: bool) -> bool:
        if v:
            raise ValueError("SWEEP_1M_V1 is shadow-only and cannot be enabled for live orders in LIVE_MICRO_V1.")
        return False


class LiveMicroCalibrationConfig(BaseModel):
    minimum_filled_trades_before_parameter_review: int = Field(default=100)
    allow_live_parameter_optimization: bool = Field(default=False)

    @field_validator("allow_live_parameter_optimization")
    @classmethod
    def lock_parameter_optimization(cls, v: bool) -> bool:
        if v:
            raise ValueError("Live parameter optimization is forbidden before the first 100 filled trades.")
        return False

    @property
    def parameter_optimization_enabled(self) -> bool:
        return self.allow_live_parameter_optimization


class LiveMicroCostsConfig(BaseModel):
    maker_fee_rate: float = Field(default=0.0002)
    taker_fee_rate: float = Field(default=0.0005)
    slippage_rate: float = Field(default=0.0005)
    funding_fee_rate_est: float = Field(default=0.0001)
    min_net_rr: float = Field(default=1.50)
    min_stop_distance_pct: float = Field(default=0.0040)
    max_stop_distance_pct: float = Field(default=0.0150)


class LiveMicroV1Config(BaseModel):
    config_version: str = Field(default="LIVE_MICRO_V1")
    risk: LiveMicroRiskConfig = Field(default_factory=LiveMicroRiskConfig)
    strategy: LiveMicroStrategyConfig = Field(default_factory=LiveMicroStrategyConfig)
    calibration: LiveMicroCalibrationConfig = Field(default_factory=LiveMicroCalibrationConfig)
    costs: LiveMicroCostsConfig = Field(default_factory=LiveMicroCostsConfig)

    @classmethod
    def load_from_yaml(cls, path: Optional[Path] = None) -> "LiveMicroV1Config":
        config_path = path or Path("config/live_micro_v1.yaml")
        if not config_path.exists():
            return cls()
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)


def load_live_micro_v1_config(path: Optional[Path] = None) -> LiveMicroV1Config:
    return LiveMicroV1Config.load_from_yaml(path)


class AppConfig(BaseModel):
    app_name: str = "SMC-SNR ML Perpetual Scanner"
    version: str = "1.0.0"
    mode: ExecutionMode = ExecutionMode.PAPER
    live_trading_locked: bool = True
    host: str = Field(default="127.0.0.1", description="Default local loopback bind address")
    port: int = Field(default=8000, ge=1, le=65535)
    scanner_api_token: Optional[str] = Field(default=None, description="Optional API token for mutating endpoints")
    data_dir: Path = Field(default=Path("./data"))
    db_filename: str = "scanner.db"
    watchlist: List[str] = Field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    enabled_symbols: List[str] = Field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    ml: MLConfig = Field(default_factory=MLConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    paper: PaperExecutionConfig = Field(default_factory=PaperExecutionConfig)
    model_b: ModelBConfig = Field(default_factory=ModelBConfig)
    model_c: ModelCConfig = Field(default_factory=ModelCConfig)
    model_d: ModelDConfig = Field(default_factory=ModelDConfig)
    groq_autotrader: GroqAutoTraderConfig = Field(default_factory=GroqAutoTraderConfig)
    live_micro_v1: LiveMicroV1Config = Field(default_factory=LiveMicroV1Config)
    discord_price_alert_webhook_url: Optional[str] = Field(
        default=None,
        description="Optional Discord webhook URL for universal perpetual price alerts notifications",
    )

    @field_validator("mode")
    @classmethod
    def guard_live_mode(cls, v: ExecutionMode, info) -> ExecutionMode:
        if v == ExecutionMode.LIVE:
            raise ValueError("LIVE mode is permanently locked in this phase. Use RESEARCH or PAPER.")
        return v

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename

    @classmethod
    def load_from_yaml(cls, path: Path) -> "AppConfig":
        if not path.exists():
            cfg = cls()
        else:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            cfg = cls(**data)
        return _apply_env_overrides(cfg)


def _apply_env_overrides(cfg: AppConfig) -> AppConfig:
    import os
    env_host = os.getenv("HOST")
    if env_host and env_host.strip():
        cfg.host = env_host.strip()
    env_port = os.getenv("PORT")
    if env_port and env_port.strip():
        try:
            cfg.port = int(env_port.strip())
        except ValueError:
            pass
    env_api_token = os.getenv("SCANNER_API_TOKEN")
    if env_api_token and env_api_token.strip():
        cfg.scanner_api_token = env_api_token.strip()

    env_scanner_mode = os.getenv("SCANNER_MODE") or os.getenv("EXECUTION_MODE")
    if env_scanner_mode is not None:
        mode_str = env_scanner_mode.strip().upper()
        if mode_str in ExecutionMode.__members__:
            cfg.mode = ExecutionMode(mode_str)
            if cfg.mode == ExecutionMode.PAPER:
                cfg.paper.enabled = True
    env_discord = os.getenv("DISCORD_WEBHOOK_URL")
    if env_discord:
        cfg.paper.discord_webhook_url = env_discord.strip()
    env_alert_discord = os.getenv("DISCORD_PRICE_ALERT_WEBHOOK_URL")
    if env_alert_discord:
        cfg.discord_price_alert_webhook_url = env_alert_discord.strip()
    env_paper_slip = os.getenv("PAPER_SLIPPAGE_RATE")
    if env_paper_slip:
        try:
            cfg.paper.slippage_rate = float(env_paper_slip)
        except ValueError:
            pass

    env_enabled = os.getenv("QWEN_ANALYSIS_ENABLED")
    if env_enabled is not None:
        cfg.ai.enabled = env_enabled.strip().lower() in ("true", "1", "yes")
    env_mode = os.getenv("QWEN_AI_MODE")
    if env_mode is not None and env_mode.upper() in AIMode.__members__:
        cfg.ai.mode = AIMode(env_mode.upper())
    elif cfg.ai.enabled and cfg.ai.mode == AIMode.NO_AI:
        cfg.ai.mode = AIMode.AI_ANALYSIS
    env_url = os.getenv("QWEN_API_URL")
    if env_url:
        cfg.ai.api_url = env_url.strip()
    env_token = os.getenv("QWEN_API_TOKEN")
    if env_token:
        cfg.ai.api_token = env_token.strip()

    # Model B Environment Overrides
    env_mb_enabled = os.getenv("MODEL_B_ENABLED")
    if env_mb_enabled is not None:
        cfg.model_b.enabled = env_mb_enabled.strip().lower() in ("true", "1", "yes")
    env_mb_equity = os.getenv("MODEL_B_INITIAL_EQUITY_USDT")
    if env_mb_equity:
        try:
            cfg.model_b.initial_equity_usdt = float(env_mb_equity)
        except ValueError:
            pass
    env_mb_risk = os.getenv("MODEL_B_TARGET_TRADE_RISK_USDT")
    if env_mb_risk:
        try:
            cfg.model_b.target_trade_risk_usdt = float(env_mb_risk)
        except ValueError:
            pass
    env_mb_daily = os.getenv("MODEL_B_DAILY_LOSS_LIMIT_USDT")
    if env_mb_daily:
        try:
            cfg.model_b.daily_loss_limit_usdt = float(env_mb_daily)
        except ValueError:
            pass
    env_mb_weekly = os.getenv("MODEL_B_WEEKLY_LOSS_LIMIT_USDT")
    if env_mb_weekly:
        try:
            cfg.model_b.weekly_loss_limit_usdt = float(env_mb_weekly)
        except ValueError:
            pass
    env_mb_override = os.getenv("MODEL_B_DIAGNOSTIC_BREAKER_OVERRIDE")
    if env_mb_override is not None:
        cfg.model_b.diagnostic_breaker_override = env_mb_override.strip().lower() in ("true", "1", "yes")
    env_mb_kill = os.getenv("MODEL_B_ENTRY_KILL_SWITCH")
    if env_mb_kill is not None:
        cfg.model_b.entry_kill_switch = env_mb_kill.strip().lower() in ("true", "1", "yes")
    env_mb_discord = os.getenv("DISCORD_MODEL_B_WEBHOOK_URL")
    if env_mb_discord:
        cfg.model_b.discord_webhook_url = env_mb_discord.strip()

    # Model D environment overrides. These are deliberately independent of Model B.
    def _env_bool(name: str, current: bool) -> bool:
        value = os.getenv(name)
        return current if value is None else value.strip().lower() in ("true", "1", "yes")

    cfg.model_d.enabled = _env_bool("MODEL_D_ENABLED", cfg.model_d.enabled)
    cfg.model_d.collector_enabled = _env_bool("MODEL_D_COLLECTOR_ENABLED", cfg.model_d.collector_enabled)
    cfg.model_d.whale_tracking_enabled = _env_bool("MODEL_D_WHALE_TRACKING_ENABLED", cfg.model_d.whale_tracking_enabled)
    cfg.model_d.wallet_tracking_enabled = _env_bool("MODEL_D_WALLET_TRACKING_ENABLED", cfg.model_d.wallet_tracking_enabled)
    cfg.model_d.wallet_scanner_enabled = _env_bool("MODEL_D_WALLET_SCANNER_ENABLED", cfg.model_d.wallet_scanner_enabled)
    cfg.model_d.wallet_enrichment_enabled = _env_bool("MODEL_D_WALLET_ENRICHMENT_ENABLED", cfg.model_d.wallet_enrichment_enabled)
    cfg.model_d.wallet_ranking_enabled = _env_bool("MODEL_D_WALLET_RANKING_ENABLED", cfg.model_d.wallet_ranking_enabled)
    env_primary = os.getenv("MODEL_D_MARKET_PRIMARY_VENUE")
    if env_primary and env_primary.strip().lower() in {"binance", "asterdex"}:
        cfg.model_d.market_primary_venue = env_primary.strip().lower()
    for chain_id in (1, 56, 42161, 8453):
        env_rpc = os.getenv(f"MODEL_D_RPC_{chain_id}_URL")
        if env_rpc:
            cfg.model_d.wallet_rpc_urls[chain_id] = env_rpc.strip()
    # Paper-only is not operator-overridable: accepting false here would weaken the invariant.
    cfg.model_d.paper_only = True

    # Groq AutoTrader Environment Overrides
    env_groq_key = os.getenv("GROQ_API_KEY") or os.getenv("groq")
    if env_groq_key:
        cfg.groq_autotrader.api_key = env_groq_key.strip()
    env_groq_model = os.getenv("GROQ_MODEL")
    if env_groq_model:
        cfg.groq_autotrader.model = env_groq_model.strip()
    env_groq_base_url = os.getenv("GROQ_BASE_URL")
    if env_groq_base_url:
        cfg.groq_autotrader.base_url = env_groq_base_url.strip()
    env_groq_venue = os.getenv("GROQ_AUTO_EXECUTION_VENUE")
    if env_groq_venue and env_groq_venue.strip().upper() in ("PAPER", "LIVE"):
        cfg.groq_autotrader.auto_execution_venue = env_groq_venue.strip().upper()

    return cfg



def get_default_config() -> AppConfig:
    """Return default validated configuration."""
    yaml_path = Path(__file__).parent / "default_config.yaml"
    if yaml_path.exists():
        cfg = AppConfig.load_from_yaml(yaml_path)
    else:
        cfg = AppConfig()
        cfg = _apply_env_overrides(cfg)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg
