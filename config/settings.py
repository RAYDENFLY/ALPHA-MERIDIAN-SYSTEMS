"""Configuration management for SMC-SNR ML Perpetual Setup Scanner.

Driven by Pydantic models with support for YAML configuration files and environment overrides.
"""

from enum import Enum
from pathlib import Path
from typing import List, Optional
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
    max_open_positions: int = Field(default=1, ge=1, le=1)
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
    enabled: bool = False
    slippage_rate: float = Field(default=0.0005, ge=0.0001, le=0.005)  # 0.05% default (0.02%-0.06% typical)
    maker_fee_rate: float = Field(default=0.0002, ge=0.0)             # 0.02% maker
    taker_fee_rate: float = Field(default=0.0005, ge=0.0)             # 0.05% taker
    pending_expiry_bars: int = Field(default=12, ge=1)                # 12 1H bars expiry
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


class AppConfig(BaseModel):
    app_name: str = "SMC-SNR ML Perpetual Scanner"
    version: str = "1.0.0"
    mode: ExecutionMode = ExecutionMode.RESEARCH
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
