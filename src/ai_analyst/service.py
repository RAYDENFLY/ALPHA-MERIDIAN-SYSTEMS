"""AI Analyst Service Coordinator.

Integrates market data extraction, Groq LLM synthesis, request deduplication,
in-memory caching, and isolated SQLite record keeping.
"""

from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional

from src.ai_analyst.groq_client import GroqAnalystClient
from src.ai_analyst.market_analyzer import AIMarketAnalyzer
from src.ai_analyst.risk_calculator import AIRiskCalculator
from src.ai_analyst.storage import AIAnalystStorage
from src.ai_analyst.types import (
    AIAnalystConfig,
    AIAnalystResult,
    AnalysisRequest,
    QuantitativeMarketSnapshot,
)
from src.core.logger import logger
from src.data.database import DatabaseManager
from src.exchange.aster_adapter import AsterAdapter


class AIAnalystService:
    """Master coordinator service for on-demand AI market analysis."""

    def __init__(
        self,
        db: DatabaseManager,
        adapter: Optional[AsterAdapter] = None,
        config: Optional[AIAnalystConfig] = None,
        storage: Optional[AIAnalystStorage] = None,
        groq_client: Optional[GroqAnalystClient] = None,
    ) -> None:
        self.db = db
        self.adapter = adapter or AsterAdapter()
        self.config = config or AIAnalystConfig()
        self.storage = storage or AIAnalystStorage(db)
        self.risk_calc = AIRiskCalculator()
        self.market_analyzer = AIMarketAnalyzer(self.adapter)
        self.groq_client = groq_client or GroqAnalystClient(
            api_key=self.config.groq_api_key,
            model_id=self.config.groq_model_id,
            risk_calculator=self.risk_calc,
            fallback_a_key=self.config.groq_api_key_fallback_a,
            fallback_b_key=self.config.groq_api_key_fallback_b,
        )

        # In-memory latest analysis cache
        self.latest_analyses: Dict[str, Dict[str, Any]] = {}

    def analyze_symbol(
        self,
        symbol: str,
        timeframe: str = "1h",
        risk_budget_usdt: Optional[float] = None,
        force_refresh: bool = False,
        model_id_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process manual analysis request with deduplication and LLM synthesis."""
        norm_sym = symbol.upper().strip()
        norm_tf = timeframe.lower().strip()
        budget = risk_budget_usdt if risk_budget_usdt and risk_budget_usdt > 0 else self.config.default_risk_budget_usdt
        cache_key = f"{norm_sym}_{norm_tf}"

        # 1. Request Deduplication Check (unless force_refresh is True)
        if not force_refresh:
            cached = self.storage.get_latest_cached(
                symbol=norm_sym,
                timeframe=norm_tf,
                max_age_ms=int(self.config.cache_ttl_sec * 1000),
            )
            if cached:
                logger.info(f"AIAnalystService: Serving deduplicated analysis for {cache_key} (cached).")
                self.latest_analyses[cache_key] = cached
                return cached

        logger.info(f"AIAnalystService: Executing fresh quantitative & LLM analysis for {norm_sym} on {norm_tf}...")

        # 2. Extract Quantitative Market Snapshot
        snapshot = self.market_analyzer.analyze_market(
            symbol=norm_sym,
            timeframe=norm_tf,
        )

        # 3. Prompt Groq LLM (or fallback synthesizer)
        result = self.groq_client.analyze(
            snapshot=snapshot,
            risk_budget_usdt=budget,
            model_override=model_id_override or self.config.groq_model_id,
        )

        # 4. Persist to isolated SQLite storage
        self.storage.save_analysis(result)

        result_dict = result.to_dict()
        self.latest_analyses[cache_key] = result_dict
        return result_dict

    def get_supported_symbols(self) -> List[str]:
        """Fetch available perpetual symbols for analysis selection."""
        try:
            instruments = self.adapter.discover_instruments()
            # Return liquid symbols first
            liquid = [i.symbol for i in instruments if (i.volume_24h_usdt or 0) >= 1000000]
            if liquid:
                return sorted(liquid)
            return sorted([i.symbol for i in instruments])
        except Exception:
            return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "DOGEUSDT", "XRPUSDT", "ASTERUSDT", "SPCXUSDT"]

    def update_config(
        self,
        groq_api_key: Optional[str] = None,
        groq_model_id: Optional[str] = None,
        risk_budget_usdt: Optional[float] = None,
        default_leverage: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Update runtime service parameters."""
        if groq_api_key is not None:
            self.config.groq_api_key = groq_api_key.strip()
            self.groq_client = GroqAnalystClient(
                api_key=self.config.groq_api_key,
                model_id=self.config.groq_model_id,
                risk_calculator=self.risk_calc,
            )
        if groq_model_id is not None:
            self.config.groq_model_id = groq_model_id.strip()
            self.groq_client.model_id = self.config.groq_model_id
        if risk_budget_usdt is not None:
            self.config.default_risk_budget_usdt = float(risk_budget_usdt)
        if default_leverage is not None:
            self.config.default_leverage = float(default_leverage)

        return self.config.to_dict()
