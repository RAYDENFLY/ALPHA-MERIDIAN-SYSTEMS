# SignalGenerator — Quantitative Multi-Model Cryptocurrency Perpetual Analytics & Execution Terminal

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/Tests-630%20Passed-emerald.svg)](tests/)
[![Execution Mode](https://img.shields.io/badge/Mode-PAPER%20ONLY-amber.svg)](#safety-invariants)
[![Exchange](https://img.shields.io/badge/Exchange-AsterDEX%20Perpetuals%20V3-blueviolet.svg)](https://fapi.asterdex.com)
[![License](https://img.shields.io/badge/License-MIT%20Research-slate.svg)](#license--disclaimer)

A deterministic, high-performance quantitative cryptocurrency perpetual futures terminal, real-time risk guardian, and paper execution engine built from first principles. Built with **zero lookahead leakage**, **100% local mathematical risk governance**, and **cross-exchange liquidity intelligence**.

Inspired by the density and clarity of professional platforms like Coinglass and Bloomberg, SignalGenerator combines multi-model setup discovery, multi-exchange order-book depth tracking, historical bar-by-bar liquidity replay, automated paper trading, and an optional AI market analyst.

---

## 🏛️ Multi-Model Research Architecture

SignalGenerator operates three mathematically isolated quantitative models under a single unified terminal:

```
+---------------------------------------------------------------------------------------------------------+
|                                        SIGNALGENERATOR PLATFORM                                         |
+------------------------------------+------------------------------------+-------------------------------+
|     MODEL A — DEFENSIVE SMC/SNR    |  MODEL B — LIQUIDITY & CATALYST    |  MODEL C — SOLANA MOMENTUM    |
+------------------------------------+------------------------------------+-------------------------------+
| Status: FROZEN FORWARD COHORT      | Status: ACTIVE PAPER RESEARCH      | Status: DEFERRED / DISABLED   |
| Baseline Capital: $16.00 USDT      | Baseline Capital: $100.00 USDT     | Baseline Capital: $100.00     |
| 11 Whitelisted Major Perpetuals    | Dynamic Tradable Universe          | High-Beta Solana DEX / Perps  |
| 1H/4H Structural Swings & FVGs     | L2 Liquidity Depth & Imbalances    | Microcap Catalyst Engine      |
| Strict 1 Position Max              | Up to 3 Concurrent Positions       | Pending Phase Validation      |
| Max Daily Loss: $0.50 (2 Losses)   | Max Daily Loss: $3.00 (3% Equity)  |                               |
| Max Weekly Loss: $1.25 (6 Losses)  | Max Weekly Loss: $7.50 (7.5%)      |                               |
| Risk Cap per Trade: ~$0.09–$0.25   | Target Risk per Trade: $1.00       |                               |
+------------------------------------+------------------------------------+-------------------------------+
```

### 1. Model A — Defensive SMC/SnR (Frozen Forward Cohort)
- **Status**: **FROZEN**. Strategy parameters, whitelist, and risk thresholds are immutable to preserve forward validation cohort integrity.
- **Methodology**: Smart Money Concepts (swing pivots, break of structure, change of character, order blocks, fair value gaps, liquidity sweeps) combined with support/resistance density clustering and rejection volume.
- **Watchlist (11 Whitelisted Markets)**: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BNBUSDT`, `DOGEUSDT`, `XRPUSDT`, `ADAUSDT`, `AVAXUSDT`, `LINKUSDT`, `LTCUSDT`, `XMRUSDT`.
- **Risk Policy**: Tier 1 small-account governance ($16 starting equity, 10x isolated leverage max, minimum notional enforcement, strict 1 concurrent position).

### 2. Model B — Aggressive Liquidity & Catalyst (Active Research Engine)
- **Status**: **ACTIVE PAPER RESEARCH**. Operates autonomously in parallel with Model A.
- **Methodology**: Multi-venue L2 order book depth tracking, Liquidity Wall Hunter (tracking persistence, spoof pulling, and depth replenishment), depth imbalances, cumulative volume delta (CVD) absorption, and 1H liquidity sweep-reclaim execution.
- **Universe**: All active AsterDEX USDT perpetual contracts (dynamic catalog discovery).
- **Risk Policy**: Isolated $100 paper portfolio, $1.00 target risk per trade, max 3 concurrent positions, $15 margin cap, $3.00 daily loss cap, and $7.50 weekly loss cap.

### 3. Model C — Solana Momentum Engine (Deferred)
- **Status**: **DEFERRED**. Awaiting completion and review of Model B forward observations. Disabled by default in both API and dashboard.

---

## ⚡ Core Terminal Modules & Features

### 1. Coinglass-Inspired Multi-Exchange Market Screener
- **Cross-Exchange Intelligence**: Aggregates real-time perpetual market telemetry across **AsterDEX V3**, **Binance Futures**, and **Bybit Linear**.
- **Real-Time Market Table**: 24h volume, 24h price change, open interest (USDT), 8h funding rate, bid/ask spread (bps), and cross-exchange price divergence.
- **Filtering & Presets**: Top Gainers, Top Losers, High Volume, Open Interest Gainers, Funding Extreme, Tightest Spread, and high-divergence arbitrage setups.
- **Client-Side Search & Sorting**: Instant symbol search, multi-column sortable table, and sticky column headers.

### 2. Interactive Liquidity Sweep & Reclaim Chart (Feature E)
- **TradingView Lightweight Charts**: Smooth interactive charting supporting **15M**, **1H**, **4H**, and **1D** candle timeframes.
- **Historical Bar Replay**: Step bar-by-bar backward and forward in time to audit exact structural levels without lookahead leakage.
- **1D Macro Level Overlay**: Renders confirmed 1D daily support/resistance levels onto intraday 15M/1H charts strictly when confirmed historically.
- **Multi-Exchange Resting Depth**: Overlays visible resting maker limit walls from AsterDEX, Binance, and Bybit.
- **Venue Selector**: Filter chart order-book depth to `Combined`, `AsterDEX`, `Binance`, or `Bybit`.

### 3. Market Liquidity & Order Flow Intelligence (Auto-Refresh)
- **Staggered Multi-Cadence Scheduling**:
  - **5s Cadence (Fast)**: Level-2 order-book depth, bid/ask spread, CVD net taker delta, and mark price.
  - **10s Cadence (Medium)**: Whale activity detector, adaptive depth walls, pre-touch spoof cancellations, and cross-exchange venue health.
  - **30s Cadence (Slow)**: Liquidation concentration heatmap, open interest, and 8h funding rate skew.
  - **1s Local Clock**: Dynamic countdown timer ("Next in Xs") and relative sync age ("Just now", "5s ago").
- **Resilience**: In-flight request mutex (prevents request stampedes), Page Visibility API background throttling (pauses polling when browser tab is hidden), and bounded exponential backoff with jitter on network errors.
- **Exchange Freshness Pills**: Dedicated `LIVE`, `STALE`, `ERROR`, or `UNAVAILABLE` indicators for each exchange venue.

### 4. Groq Trading Analyst
- **Hybrid Quantitative + LLM Synthesis**: Combines multi-timeframe SMC market structure, order-book depth imbalance, liquidation magnet distances, and BTC correlation with optional Groq LLM narrative reasoning.
- **Scenario Comparison**: Side-by-side risk-adjusted LONG vs SHORT trade plan analysis with defined entries, invalidation stop losses, and multi-tier take profits.
- **Deterministic Fallback**: Operates 100% offline and deterministically without an API key if Groq is unconfigured or unreachable.

### 5. Universal AsterDEX Perpetual Price Alerts
- User-configurable alerts supporting all 600+ tradable AsterDEX perpetual contracts independently of scanner whitelists.
- Real-time mark price and last-traded-price evaluation with configurable crossing conditions (`PRICE_ABOVE`, `PRICE_BELOW`).
- **Discord Webhook Notifications**: Optional asynchronous Discord rich embed alerts with target price, observed price, trigger timestamps, and data freshness indicators.

### 6. Read-Only Live Positions & Health Diagnostics
- Direct read-only integration with AsterDEX Futures API V3 via Web3 API-Wallet EIP-712 signing.
- Audits open positions, entry prices, mark prices, leverage, and liquidation safety distance.
- **Protection Classification**: Evaluates resting TP/SL orders into `PROTECTED`, `PARTIALLY_PROTECTED`, or `UNPROTECTED`.
- Strictly read-only: **Zero exchange order placement, modification, or cancellation**.

### 7. Trading Journal & Performance Analytics
- **Model Isolation**: Prominent model selector (`All Models`, `Model A`, `Model B`) to filter trades and calculate model-specific performance metrics.
- **Accounting Governance**: Strict separation between floating unrealized PnL (open positions) and audited realized PnL (closed trades). Floating PnL is never mixed into realized equity.
- **Telemetry**: Tracks gross PnL, maker/taker fees, funding payments, execution slippage, net PnL, win rate, and duration.
- **Model Column**: Every trade and lifecycle transition records its explicit `model_id` (`MODEL_A`, `MODEL_B`, or `UNKNOWN`).

### 8. Bilingual Localization & Unified Pagination
- **Bilingual Support**: Instant, zero-reload switching between **English (EN)** and **Bahasa Indonesia (ID)** with complete coverage across headers, tooltips, cards, tables, and metrics.
- **Shared Pagination**: Consistent page size selector (10, 25, 50, 100) and navigation across setups, watchlist, trading journal, whale activity, and market screener.

---

## 🛡️ Safety Invariants & Governance

1. **Paper Mode Enforced**: `SCANNER_MODE=PAPER` and `live_trading_locked=True` are hardcoded defaults. The system will never submit real exchange orders.
2. **Model A Frozen**: Strategy logic, ML weights, and risk budgets for Model A are locked to prevent survivorship or lookahead bias during forward testing.
3. **Storage Immutability**: Historical paper trades, lifecycle transition logs, and cohort records are never deleted, reset, or fabricated.
4. **Authoritative Provenance**: Model ownership is read strictly from dedicated database columns (`model_id`), never inferred from string prefixes or tickers.
5. **No Secrets in Repository**: Zero API keys, private keys, or webhook tokens exist in the codebase or git history. All sensitive credentials load strictly from `.env` (gitignored).

---

## 📁 Repository Directory Layout

```
SignalGenerator/
├── config/                 # Application configuration & YAML files
├── data/                   # SQLite databases (gitignored runtime state)
│   ├── scanner.db          # Unified scanner, journal, alerts, and lifecycle DB
│   └── backups/            # Database point-in-time backups
├── docs/                   # Centralized System Documentation & Audit Reports
│   ├── architecture.md                     # System architecture & data flow
│   ├── AUDIT_FULL_SYSTEM_READINESS.md      # Full system readiness audit
│   ├── forward_cohort_integrity.md         # Forward cohort validation governance
│   └── ...                                 # (See Documentation Hub below)
├── scripts/                # Standalone research & execution scripts
│   ├── run_research_backtest.py            # Quantitative backtest runner
│   └── market_audit.py                     # AsterDEX live market query tool
├── src/                    # Core Application Source Code
│   ├── ai_analyst/         # Groq AI Trading Analyst, quantitative extractor & storage
│   ├── alerts/             # Universal Perpetual Price Alert engine & storage
│   ├── api/                # FastAPI application, REST endpoints & dashboard.html
│   ├── backtest/           # Historical backtesting engine & metrics calculators
│   ├── catalyst/           # News and catalyst sentiment feed analyzers
│   ├── core/               # Shared types, logging, and application configuration
│   ├── data/               # SQLite database manager, schemas & migrations
│   ├── demand_zone/        # Demand zone detector, risk sizer & TP planner
│   ├── exchange/           # AsterDEX V3, Binance Futures, Bybit Linear adapters & L2 aggregator
│   ├── execution/          # Paper execution engine, bracket orders & lifecycle dispatcher
│   ├── integrations/       # External notification services (Discord webhooks)
│   ├── intelligence/       # Market context, liquidation heatmap, whale detector & chart service
│   ├── journal/            # Trade journal repository & performance KPI aggregator
│   ├── liquidity/          # Order book depth trackers, wall hunters & imbalance math
│   ├── ml/                 # Walk-forward feature pipelines & calibrated classifiers
│   ├── research/           # Model B shadow evaluator, variant engine & research journal
│   ├── risk/               # Tier 1 & Model B multi-model risk managers
│   ├── scheduler/          # Background scanning runners (Model A & Model B)
│   ├── screener/           # Multi-exchange perpetual market screener service
│   ├── smc/                # Smart Money Concepts deterministic detectors
│   ├── snr/                # Support & Resistance density clustering
│   ├── static/             # Modular CSS styling and JavaScript terminal application
│   │   ├── css/            # base.css, components.css, charts.css
│   │   └── js/             # Modular JS components (screener, analyst, chart, intel, etc.)
│   └── strategy/           # Strategy pipelines and opportunity evaluators
├── tests/                  # Automated pytest test suites (630 tests)
│   ├── test_ai_analyst.py                  # Groq AI Analyst integration tests
│   ├── test_dashboard_js_syntax.py         # JavaScript syntax compilation & handler tests
│   ├── test_feature_e_venue_selector.py    # Chart venue isolation & telemetry tests
│   ├── test_liquidity_1d_and_cross_exchange.py # 1D non-lookahead & L2 aggregation tests
│   ├── test_liquidity_intel_auto_refresh.py # Staggered auto-refresh & health tests
│   ├── test_market_screener.py             # Multi-exchange screener tests
│   ├── test_navigation_tab_isolation.py    # Mutual exclusive tab DOM isolation tests
│   └── ...                                 # Comprehensive regression tests
├── .env.example            # Environment configuration template
├── .gitignore              # Git ignore rules for keys, DBs, logs & caches
├── pytest.ini              # Pytest configuration and test paths
├── requirements.txt        # Python dependency manifest
└── README.md               # Repository documentation entry point
```

---

## 🚀 Quickstart & Setup

### 1. Prerequisites
- Python 3.11+
- Windows PowerShell, macOS, or Linux terminal
- Git

### 2. Installation
```powershell
# Clone the repository
git clone https://github.com/RAYDENFLY/SignalGenerator.git
cd SignalGenerator

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # On Linux/macOS: source .venv/bin/activate

# Install required dependencies
pip install -r requirements.txt
```

### 3. Configuration
Copy the configuration template:
```powershell
copy .env.example .env
```
Key settings in `.env`:
- `SCANNER_MODE=PAPER` (default and required for safety)
- `DISCORD_PRICE_ALERT_WEBHOOK_URL` (optional: webhook URL for perpetual price alerts)
- `GROQ_API_KEY` (optional: free API key from https://console.groq.com for AI Analyst narrative)
- `ASTER_USER_ADDRESS`, `ASTER_SIGNER_ADDRESS`, `ASTER_SIGNER_PRIVATE_KEY` (optional: read-only live account monitoring)

### 4. Running Tests
Run the complete automated test suite (630 tests):
```powershell
pytest
```
Run focused module test suites:
```powershell
# Test Market Liquidity Auto-Refresh & Concurrency
pytest tests/test_liquidity_intel_auto_refresh.py -v

# Test Multi-Exchange Perpetual Screener
pytest tests/test_market_screener.py -v

# Test Feature E Chart & 1D Replay
pytest tests/test_liquidity_1d_and_cross_exchange.py -v

# Test Groq AI Analyst
pytest tests/test_ai_analyst.py -v

# Test Navigation & JavaScript Syntax
pytest tests/test_navigation_tab_isolation.py tests/test_dashboard_js_syntax.py -v
```

### 5. Launching the Web Dashboard
```powershell
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```
Open your browser and navigate to:  
👉 **`http://127.0.0.1:8000`**

---

## 🔒 License & Disclaimer

This software is strictly an experimental quantitative research and market simulation tool. It is **not** an automated trading bot and does **not** provide financial advice. Live order execution is permanently disabled by safety policy (`live_trading_locked=True`). Always manage risk responsibly.
