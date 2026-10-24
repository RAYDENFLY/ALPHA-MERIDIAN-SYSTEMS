"""Focused verification and regression tests for Model B defect remediations.

Verifies:
- Defect 1: AsterDEX 24h quoteVolume ingestion, volume_status tracking, and focus filtering.
- Defect 2: HyperliquidAdapter get_order_book limit parameter support across bounds.
- Defect 3: Full opportunity engine integration into ModelBStrategyGenerator and ModelBRunner.
"""

from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock, patch

from config.settings import ModelBConfig
from src.catalyst.engine import NewsCatalystEngine
from src.catalyst.types import CatalystCategory, CatalystEvent, VerificationStatus
from src.core.types import Candle, CandidateStatus, Direction, EntryCategory, MarketBias, SetupCandidate, StructureState
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.hyperliquid_adapter import HyperliquidAdapter
from src.exchange.new_market_scanner import NewMarketOpportunityScanner
from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook, OrderBookLevel
from src.exchange.service import MultiExchangeService
from src.liquidity.collector import LiquidityCollector
from src.liquidity.wall_hunter import LiquidityWallHunter
from src.risk.model_b_risk import ModelBRiskManager
from src.scheduler.model_b_runner import ModelBRunner
from src.strategy.model_b.generator import ModelBStrategyGenerator


# ==============================================================================
# Defect 1 Tests: AsterDEX 24h Volume and Focus Discovery
# ==============================================================================

class TestAsterDEXVolumeRemediation:
    """Verifies AsterAdapter correctly queries /fapi/v1/ticker/24hr and maps quoteVolume."""

    @patch("requests.Session.get")
    def test_discover_instruments_maps_quote_volume_and_status(self, mock_get):
        mock_info = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "pricePrecision": 2,
                    "quantityPrecision": 3,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
                    ],
                },
                {
                    "symbol": "ETHUSDT",
                    "baseAsset": "ETH",
                    "quoteAsset": "USDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "pricePrecision": 2,
                    "quantityPrecision": 2,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        {"filterType": "LOT_SIZE", "minQty": "0.01", "stepSize": "0.01"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
                    ],
                },
                {
                    "symbol": "DELISTEDUSDT",
                    "baseAsset": "DELISTED",
                    "quoteAsset": "USDT",
                    "contractType": "PERPETUAL",
                    "status": "BREAK",
                    "filters": [],
                },
            ]
        }
        mock_tickers = [
            {"symbol": "BTCUSDT", "quoteVolume": "33500000.50", "lastPrice": "68500.0"},
            {"symbol": "ETHUSDT", "quoteVolume": "12400000.25", "lastPrice": "2600.0"},
        ]

        def get_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "exchangeInfo" in url:
                mock_resp.json.return_value = mock_info
            elif "ticker/24hr" in url:
                mock_resp.json.return_value = mock_tickers
            return mock_resp

        mock_get.side_effect = get_side_effect

        adapter = AsterAdapter(base_url="https://fapi.asterdex.com")
        instruments = adapter.discover_instruments()

        assert len(instruments) == 2
        btc = next(i for i in instruments if i.symbol == "BTCUSDT")
        assert btc.volume_24h_usdt == 33500000.50
        assert btc.volume_status == "AVAILABLE"
        assert btc.last_price == 68500.0
        assert btc.is_active is True

        eth = next(i for i in instruments if i.symbol == "ETHUSDT")
        assert eth.volume_24h_usdt == 12400000.25
        assert eth.volume_status == "AVAILABLE"

    @patch("requests.Session.get")
    def test_discover_instruments_handles_unmatched_or_missing_volume(self, mock_get):
        mock_info = {
            "symbols": [
                {
                    "symbol": "SOLUSDT",
                    "baseAsset": "SOL",
                    "quoteAsset": "USDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "filters": [],
                },
                {
                    "symbol": "XRPUSDT",
                    "baseAsset": "XRP",
                    "quoteAsset": "USDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "filters": [],
                },
            ]
        }
        # SOL has no quoteVolume, XRP is not in tickers list at all
        mock_tickers = [
            {"symbol": "SOLUSDT", "lastPrice": "150.0"},
        ]

        def get_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "exchangeInfo" in url:
                mock_resp.json.return_value = mock_info
            elif "ticker/24hr" in url:
                mock_resp.json.return_value = mock_tickers
            return mock_resp

        mock_get.side_effect = get_side_effect

        adapter = AsterAdapter(base_url="https://fapi.asterdex.com")
        instruments = adapter.discover_instruments()

        sol = next(i for i in instruments if i.symbol == "SOLUSDT")
        assert sol.volume_24h_usdt is None
        assert sol.volume_status == "MISSING_FIELD"

        xrp = next(i for i in instruments if i.symbol == "XRPUSDT")
        assert xrp.volume_24h_usdt is None
        assert xrp.volume_status == "UNMATCHED_TICKER"

    def test_get_focus_symbols_filtering_and_sorting(self):
        db = MagicMock()
        journal = MagicMock()
        runner = ModelBRunner(db=db, journal=journal, config=ModelBConfig(min_24h_volume_usdt=1000000.0))

        # Setup universe with valid, below threshold, and missing volume instruments
        runner.discovered_universe = {
            "asterdex": [
                NormalizedInstrument("BTCUSDT", "asterdex", "BTC", "USDT", 0.001, 0.001, 0.1, 5.0, volume_24h_usdt=50000000.0, volume_status="AVAILABLE"),
                NormalizedInstrument("LOWVOLUSDT", "asterdex", "LOW", "USDT", 1.0, 1.0, 0.01, 5.0, volume_24h_usdt=500000.0, volume_status="AVAILABLE"),
                NormalizedInstrument("MISSINGUSDT", "asterdex", "MIS", "USDT", 1.0, 1.0, 0.01, 5.0, volume_24h_usdt=None, volume_status="UNMATCHED_TICKER"),
                NormalizedInstrument("ETHUSDT", "asterdex", "ETH", "USDT", 0.01, 0.01, 0.01, 5.0, volume_24h_usdt=20000000.0, volume_status="AVAILABLE"),
            ]
        }

        focus = runner.get_focus_symbols(max_symbols=10)
        assert len(focus) == 2
        assert focus[0].symbol == "BTCUSDT"
        assert focus[1].symbol == "ETHUSDT"


# ==============================================================================
# Defect 2 Tests: HyperliquidAdapter get_order_book Limit Parameter
# ==============================================================================

class TestHyperliquidLimitRemediation:
    """Verifies HyperliquidAdapter.get_order_book supports the limit parameter correctly."""

    @patch("requests.Session.post")
    def test_get_order_book_limit_slicing(self, mock_post):
        # 10 levels of bids and asks
        mock_levels = [
            [{"px": str(100 - i), "sz": "1.0", "n": 1} for i in range(10)], # bids
            [{"px": str(101 + i), "sz": "1.0", "n": 1} for i in range(10)], # asks
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"levels": mock_levels}
        mock_post.return_value = mock_resp

        adapter = HyperliquidAdapter()

        # Test limit = 5
        book_5 = adapter.get_order_book("BTC", limit=5)
        assert book_5 is not None
        assert len(book_5.bids) == 5
        assert len(book_5.asks) == 5

        # Test limit = None (returns all levels)
        book_all = adapter.get_order_book("BTC", limit=None)
        assert book_all is not None
        assert len(book_all.bids) == 10
        assert len(book_all.asks) == 10

        # Test limit <= 0
        book_0 = adapter.get_order_book("BTC", limit=0)
        assert book_0 is not None
        assert len(book_0.bids) == 0
        assert len(book_0.asks) == 0

        # Test limit negative
        book_neg = adapter.get_order_book("BTC", limit=-5)
        assert book_neg is not None
        assert len(book_neg.bids) == 0
        assert len(book_neg.asks) == 0

        # Test default limit (50)
        book_default = adapter.get_order_book("BTC")
        assert book_default is not None
        assert len(book_default.bids) == 10


# ==============================================================================
# Defect 3 Tests: Opportunity Engines Integration & Deduplication
# ==============================================================================

class TestOpportunityEnginesIntegration:
    """Verifies full wiring of WallHunter, NewMarketScanner, and CatalystEngine."""

    def _create_sweep_candles(self, base_price: float = 50.0, count: int = 25) -> list:
        """Construct a 25-candle 1H series featuring a valid swing low sweep-reclaim."""
        candles = []
        for i in range(count):
            t_ms = 1700000000000 + i * 3600000
            if i == 10:
                # Key swing low formed at candle 10 (low=48.0)
                o, h, l, c = 50.0, 51.0, 48.0, 50.0
            elif i == count - 1:
                # Last candle sweeps below 48.0 (low=47.5) and reclaims (close=49.5)
                o, h, l, c = 48.5, 50.0, 47.5, 49.5
            else:
                o, h, l, c = 50.0, 51.5, 49.0, 50.5

            candles.append(
                Candle(
                    symbol="TESTUSDT",
                    timeframe="1h",
                    timestamp_ms=t_ms,
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=1000.0,
                    close_time_ms=t_ms + 3600000,
                )
            )
        return candles

    def test_generator_evaluates_all_engines_and_records_evidence(self):
        wall_hunter = LiquidityWallHunter(wall_multiple_threshold=3.0, min_persistence_sec=60.0)
        new_market_scanner = NewMarketOpportunityScanner(max_listing_age_days=14.0)
        catalyst_engine = NewsCatalystEngine()

        generator = ModelBStrategyGenerator(
            wall_hunter=wall_hunter,
            new_market_scanner=new_market_scanner,
            catalyst_engine=catalyst_engine,
        )

        # Mock liquidity collector
        generator.liquidity_collector = MagicMock()
        snapshot_mock = MagicMock()
        snapshot_mock.spread_bps = 2.0
        snapshot_mock.imbalance_25bps = 0.25
        snapshot_mock.absorption_score = 0.5
        snapshot_mock.cvd_1h = 10000.0
        snapshot_mock.exchange_name = "asterdex"
        generator.liquidity_collector.collect_for_symbol.return_value = snapshot_mock

        instrument = NormalizedInstrument(
            symbol="TESTUSDT",
            exchange_name="asterdex",
            base_asset="TEST",
            quote_asset="USDT",
            min_qty=0.1,
            step_size=0.1,
            tick_size=0.01,
            min_notional=5.0,
            last_price=50.0,
            is_active=True,
            volume_24h_usdt=5000000.0,
            volume_status="AVAILABLE",
        )

        candles = self._create_sweep_candles(base_price=50.0)
        order_book = NormalizedOrderBook(
            symbol="TESTUSDT",
            exchange_name="asterdex",
            timestamp_ms=1700000000000,
            bids=[OrderBookLevel(price=49.98, size=200.0)],
            asks=[OrderBookLevel(price=50.02, size=200.0)],
        )

        # Neutral evaluation
        now_ts = 1700000000000 + 25 * 3600000
        candidate = generator.evaluate_symbol(
            symbol="TESTUSDT",
            candles=candles,
            instrument=instrument,
            order_book=order_book,
            now_ms=now_ts,
            listing_time_ms=now_ts - int(5 * 86400 * 1000), # 5 days old
        )

        assert candidate is not None
        assert candidate.status == CandidateStatus.READY
        assert candidate.ai_analysis is not None
        assert "engine_evidence" in candidate.ai_analysis
        evidence = candidate.ai_analysis["engine_evidence"]
        assert "wall_hunter" in evidence
        assert "new_market_scanner" in evidence
        assert "news_catalyst" in evidence
        assert evidence["new_market_scanner"]["is_new_market"] is True
        assert generator.engine_telemetry["wall_hunter"]["invocations"] >= 1
        assert generator.engine_telemetry["new_market_scanner"]["evaluated"] >= 1
        assert generator.engine_telemetry["news_catalyst"]["queries"] >= 1

    def test_catalyst_opposing_news_vetoes_candidate(self):
        catalyst_engine = NewsCatalystEngine()
        generator = ModelBStrategyGenerator(catalyst_engine=catalyst_engine)

        generator.liquidity_collector = MagicMock()
        snapshot_mock = MagicMock()
        snapshot_mock.spread_bps = 2.0
        snapshot_mock.imbalance_25bps = 0.25
        snapshot_mock.absorption_score = 0.5
        snapshot_mock.cvd_1h = 10000.0
        snapshot_mock.exchange_name = "asterdex"
        generator.liquidity_collector.collect_for_symbol.return_value = snapshot_mock

        instrument = NormalizedInstrument(
            symbol="BEARUSDT",
            exchange_name="asterdex",
            base_asset="BEAR",
            quote_asset="USDT",
            min_qty=0.1,
            step_size=0.1,
            tick_size=0.01,
            min_notional=5.0,
            last_price=50.0,
            is_active=True,
            volume_24h_usdt=2000000.0,
            volume_status="AVAILABLE",
        )
        candles = self._create_sweep_candles(base_price=50.0)

        # Ingest confirmed bearish event (e.g. delisting)
        event = CatalystEvent(
            event_id="CAT_DELIST_1",
            title="BEARUSDT delisting scheduled",
            source_name="Exchange Notice",
            source_url="https://exchange.com/notice/1",
            published_at_utc=datetime.now(timezone.utc).isoformat(),
            ingested_at_utc=datetime.now(timezone.utc).isoformat(),
            category=CatalystCategory.EXCHANGE_DELISTING,
            verification_status=VerificationStatus.CONFIRMED_FACT,
            affected_symbols=["BEARUSDT"],
            confidence_score=0.95,
        )
        catalyst_engine.ingest_event(event)

        # Evaluate candidate (SMC generator produces LONG on sweep)
        candidate = generator.evaluate_symbol(
            symbol="BEARUSDT",
            candles=candles,
            instrument=instrument,
        )

        assert candidate is not None
        # Vetoed: status should be INFEASIBLE and have veto rejection reason
        assert candidate.status == CandidateStatus.INFEASIBLE
        assert any("Vetoed by confirmed opposing catalyst" in r for r in candidate.rejection_reasons)
        assert candidate.ai_analysis["engine_evidence"]["news_catalyst"]["status"] == "VETOED"
        assert generator.engine_telemetry["news_catalyst"]["vetoed"] >= 1

    def test_unverified_catalyst_does_not_veto_or_alter_confidence(self):
        catalyst_engine = NewsCatalystEngine()
        generator = ModelBStrategyGenerator(catalyst_engine=catalyst_engine)

        generator.liquidity_collector = MagicMock()
        snapshot_mock = MagicMock()
        snapshot_mock.spread_bps = 2.0
        snapshot_mock.imbalance_25bps = 0.25
        snapshot_mock.absorption_score = 0.5
        snapshot_mock.cvd_1h = 10000.0
        snapshot_mock.exchange_name = "asterdex"
        generator.liquidity_collector.collect_for_symbol.return_value = snapshot_mock

        instrument = NormalizedInstrument(
            symbol="RUMORUSDT",
            exchange_name="asterdex",
            base_asset="RUMOR",
            quote_asset="USDT",
            min_qty=0.1,
            step_size=0.1,
            tick_size=0.01,
            min_notional=5.0,
            last_price=50.0,
            is_active=True,
            volume_24h_usdt=2000000.0,
            volume_status="AVAILABLE",
        )
        candles = self._create_sweep_candles(base_price=50.0)

        # Ingest unverified rumor
        event = CatalystEvent(
            event_id="CAT_RUMOR_1",
            title="Rumor of major hack on RUMORUSDT",
            source_name="X Chatter",
            source_url="https://x.com/post/1",
            published_at_utc=datetime.now(timezone.utc).isoformat(),
            ingested_at_utc=datetime.now(timezone.utc).isoformat(),
            category=CatalystCategory.SECURITY_INCIDENT,
            verification_status=VerificationStatus.UNVERIFIED_REPORT,
            affected_symbols=["RUMORUSDT"],
            confidence_score=0.30,
        )
        catalyst_engine.ingest_event(event)

        valid_book = NormalizedOrderBook(
            symbol="RUMORUSDT",
            exchange_name="asterdex",
            timestamp_ms=1700000000000,
            bids=[OrderBookLevel(price=49.98, size=200.0)],
            asks=[OrderBookLevel(price=50.02, size=200.0)],
        )
        candidate = generator.evaluate_symbol(
            symbol="RUMORUSDT",
            candles=candles,
            instrument=instrument,
            order_book=valid_book,
        )

        assert candidate is not None
        # Unverified rumor must be ignored! Not vetoed.
        assert not any("Vetoed by confirmed opposing catalyst" in r for r in candidate.rejection_reasons)
        assert candidate.status == CandidateStatus.READY
        assert generator.engine_telemetry["news_catalyst"]["unverified_ignored"] >= 1

    def test_runner_deduplication_by_symbol_and_direction(self):
        db = MagicMock()
        journal = MagicMock()
        paper_engine = MagicMock()
        paper_engine.open_positions = {}
        paper_engine.pending_orders = {}

        runner = ModelBRunner(
            db=db,
            journal=journal,
            paper_engine=paper_engine,
        )

        # Mock generator to return candidates with same (symbol, direction) but different confidence
        c1 = SetupCandidate(
            candidate_id="MCAND_1",
            symbol="BTCUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=68000.0,
            stop_loss=67000.0,
            take_profit_1=69500.0,
            ml_probability=0.70,
            status=CandidateStatus.READY,
            entry_category=EntryCategory.REVERSAL,
            model_id="MODEL_B",
        )
        c2 = SetupCandidate(
            candidate_id="MCAND_2",
            symbol="BTCUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=68050.0,
            stop_loss=67050.0,
            take_profit_1=69550.0,
            ml_probability=0.85,
            status=CandidateStatus.READY,
            entry_category=EntryCategory.REVERSAL,
            model_id="MODEL_B",
        )

        # Setup focus symbols returning 2 items
        runner.get_focus_symbols = MagicMock(return_value=[
            NormalizedInstrument("BTCUSDT", "asterdex", "BTC", "USDT", 0.001, 0.001, 0.1, 5.0, volume_24h_usdt=10000000.0, volume_status="AVAILABLE"),
            NormalizedInstrument("BTCUSDT", "asterdex", "BTC", "USDT", 0.001, 0.001, 0.1, 5.0, volume_24h_usdt=10000000.0, volume_status="AVAILABLE"),
        ])
        candles = self._create_sweep_candles(base_price=68000.0)
        runner.exchange = MagicMock()
        runner.exchange.get_klines.return_value = candles

        # Generator returns c1 first then c2
        runner.generator.evaluate_symbol = MagicMock(side_effect=[c1, c2])

        candidates = runner.run_scan_cycle(now_ms=1000)

        # Submitted only ONCE to paper engine with higher confidence (c2)
        assert paper_engine.submit_candidate.call_count == 1
        submitted_cand = paper_engine.submit_candidate.call_args[0][0]
        assert submitted_cand.candidate_id == "MCAND_2"
        assert submitted_cand.ml_probability == 0.85
        assert len(candidates) == 1
