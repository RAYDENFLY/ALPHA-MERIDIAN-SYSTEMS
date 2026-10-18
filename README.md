# SignalGenerator — Quantitative Multi-Model Cryptocurrency Perpetual Engine

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/Tests-464%20Passed-emerald.svg)](tests/)
[![Execution Mode](https://img.shields.io/badge/Mode-PAPER%20ONLY-amber.svg)](#safety-invariants)
[![Exchange](https://img.shields.io/badge/Exchange-AsterDEX%20Perpetuals%20V3-blueviolet.svg)](https://fapi.asterdex.com)

A high-performance quantitative cryptocurrency perpetual futures setup scanner, real-time risk guardian, and paper execution engine built from first principles. Built with **zero generative AI dependencies**, **zero lookahead leakage**, and **100% local, deterministic, and walk-forward machine learning**.

Designed specifically for small-account capital preservation, strict mathematical risk governance, multi-exchange liquidity intelligence, and auditable paper forward-testing.

---

## 🏛️ Multi-Model Research Architecture

SignalGenerator operates three independent, mathematically isolated quantitative research models under a single unified dashboard:

```
+---------------------------------------------------------------------------------------------------------+
|                                        SIGNALGENERATOR PLATFORM                                         |
+------------------------------------+------------------------------------+-------------------------------+
|     MODEL A — DEFENSIVE SMC/SNR    |  MODEL B — LIQUIDITY & CATALYST    |  MODEL C — SOLANA MOMENTUM    |
+------------------------------------+------------------------------------+-------------------------------+
| Status: FROZEN FORWARD COHORT      | Status: ACTIVE PAPER RESEARCH      | Status: DEFERRED / DISABLED   |
| Baseline Capital: $16.00 USDT      | Baseline Capital: $100.00 USDT     | Baseline Capital: $100.00     |
| 11 Whitelisted Major Perpetuals    | Universal Tradable Universe        | High-Beta Solana DEX / Perps  |
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
- **Methodology**: Multi-venue L2 order book depth tracking, Liquidity Wall Hunter (tracking persistence, spoof pulling, and depth replenishment), 25 bps depth imbalances, cumulative volume delta (CVD) absorption, and 1H liquidity sweep-reclaim execution.
- **Universe**: All currently active AsterDEX USDT perpetual contracts (dynamic catalog discovery).
- **Risk Policy**: Isolated $100 paper portfolio, $1.00 target risk per trade, max 3 concurrent positions, $15 margin cap, $3.00 daily loss cap, and $7.50 weekly loss cap.

### 3. Model C — Solana Momentum Engine (Deferred)
- **Status**: **DEFERRED**. Awaiting completion and review of Model B forward observations. Disabled by default in both API and dashboard.

---

## ⚡ Core Platform Capabilities

### 1. Unified Interactive Web Dashboard
- Single-page dashboard served via FastAPI and Tailwind CSS (`http://127.0.0.1:8000`).
- Model tabs with independent portfolio views, risk meters, and candidate signals.
- Live candle rendering, multi-timeframe swing indicators, and real-time execution logs.

### 2. Trading Journal & Performance Analytics
- **Model Isolation**: Prominent model selector (`All Models`, `Model A`, `Model B`) to filter trades and calculate model-specific performance metrics.
- **Accounting Governance**: Strict separation between floating unrealized PnL (open positions) and audited realized PnL (closed trades). Floating PnL is never mixed into realized equity.
- **Telemetry**: Tracks gross PnL, maker/taker fees, funding payments, execution slippage, net PnL, win rate, and duration.
- **Model Column**: Every trade and lifecycle transition records its explicit `model_id` (`MODEL_A`, `MODEL_B`, or `UNKNOWN`).

### 3. Universal AsterDEX Perpetual Price Alerts
- User-configurable alerts supporting all 600+ tradable AsterDEX perpetual contracts independently of scanner whitelists.
- Real-time mark price and last-traded-price evaluation with configurable crossing conditions (`PRICE_ABOVE`, `PRICE_BELOW`).
- **Discord Webhook Notifications**: Optional asynchronous Discord rich embed alerts with target price, observed price, trigger timestamps, and data freshness indicators.

### 4. Read-Only Live AsterDEX Positions & Health Diagnostics
- Direct read-only integration with AsterDEX Futures API V3 via Web3 API-Wallet EIP-712 signing.
- Audits open positions, entry prices, mark prices, leverage, and liquidation safety distance.
- **Protection Classification**: Evaluates resting TP/SL orders into `PROTECTED`, `PARTIALLY_PROTECTED`, or `UNPROTECTED`.
- Strictly read-only: **Zero exchange order placement, modification, or cancellation**.

---

## 🛡️ Safety Invariants & Governance

1. **Paper Mode Enforced**: `SCANNER_MODE=PAPER` and `live_trading_locked=True` are hardcoded defaults. The system will never submit real exchange orders.
2. **Model A Frozen**: Strategy logic, ML weights, and risk budgets for Model A are locked to prevent survivorship or lookahead bias during forward testing.
3. **Storage Immutability**: Historical paper trades, lifecycle transition logs, and cohort records are never deleted, reset, or fabricated.
4. **Authoritative Provenance**: Model ownership is read strictly from dedicated database columns (`model_id`), never inferred from string prefixes or tickers.

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
│   ├── forward_paper_activation_audit.md   # Paper execution engine activation audit
│   ├── mode2c_strategy_spec.md             # Model strategy specifications
│   ├── profitability_baseline_report.md    # Empirical profitability research
│   └── ...                                 # (See Documentation Hub below)
├── scripts/                # Standalone research & execution scripts
│   ├── run_research_backtest.py            # Quantitative backtest runner
│   └── market_audit.py                     # AsterDEX live market query tool
├── src/                    # Core Application Source Code
│   ├── alerts/             # Universal Perpetual Price Alert engine & storage
│   ├── api/                # FastAPI application, REST endpoints & dashboard.html
│   ├── backtest/           # Historical backtesting engine & metrics calculators
│   ├── catalyst/           # News and catalyst sentiment feed analyzers
│   ├── core/               # Shared types, logging, and application configuration
│   ├── data/               # SQLite database manager, schemas & migrations
│   ├── exchange/           # AsterDEX V3 REST adapter, account service & EIP-712 signer
│   ├── execution/          # Paper execution engine, bracket orders & lifecycle dispatcher
│   ├── integrations/       # External notification services (Discord webhooks)
│   ├── journal/            # Trade journal repository & performance KPI aggregator
│   ├── liquidity/          # Order book depth trackers, wall hunters & imbalance math
│   ├── ml/                 # Walk-forward feature pipelines & calibrated classifiers
│   ├── risk/               # Tier 1 & Model B multi-model risk managers
│   ├── scheduler/          # Background scanning runners (Model A & Model B)
│   ├── smc/                # Smart Money Concepts deterministic detectors
│   ├── snr/                # Support & Resistance density clustering
│   └── strategy/           # Strategy pipelines and opportunity evaluators
├── tests/                  # Automated pytest test suites (464 tests)
│   ├── test_api.py                         # FastAPI endpoint integration tests
│   ├── test_journal_model_isolation.py     # Journal model separation tests
│   ├── test_model_b_strategy_and_risk.py   # Model B liquidity & risk tests
│   ├── test_tier1_risk_policy.py           # Tier 1 mathematical risk invariant tests
│   └── test_v3_order_lifecycle.py          # Protective order lifecycle tests
├── .env.example            # Environment configuration template
├── .gitignore              # Git ignore rules for keys, DBs, logs & caches
├── pytest.ini              # Pytest configuration and test paths
├── requirements.txt        # Python dependency manifest
└── README.md               # Repository documentation entry point
```

---

## 📚 Documentation Hub (`docs/`)

All project specifications, quantitative audits, and research reports are centralized in [`docs/`](docs/):

### 1. Architecture & Specifications
- [`docs/architecture.md`](docs/architecture.md) — Comprehensive technical architecture, data pipelines, and component boundaries.
- [`docs/mode2c_strategy_spec.md`](docs/mode2c_strategy_spec.md) — Multi-timeframe strategy evaluation specification.
- [`docs/visual_strategy_experiment_plan.md`](docs/visual_strategy_experiment_plan.md) — Experimental plan for visual chart overlays and signal validation.

### 2. Forward Paper Trading & Cohort Governance
- [`docs/forward_cohort_integrity.md`](docs/forward_cohort_integrity.md) — Forward testing cohort rules and frozen anchor policies.
- [`docs/forward_paper_activation_audit.md`](docs/forward_paper_activation_audit.md) — Audit of simulated execution engine activation.
- [`docs/forward_paper_activation_test_results.md`](docs/forward_paper_activation_test_results.md) — Execution simulation test results.
- [`docs/forward_risk_guard_interaction.md`](docs/forward_risk_guard_interaction.md) — Interaction protocol between risk guards and paper execution.
- [`docs/forward_risk_recovery_audit.md`](docs/forward_risk_recovery_audit.md) — Audit of crash recovery and risk state persistence across restarts.
- [`docs/forward_scan_observability_audit.md`](docs/forward_scan_observability_audit.md) — Scan observability, telemetry, and candle age audit.

### 3. Quantitative Profitability & Risk Audits
- [`docs/AUDIT_FULL_SYSTEM_READINESS.md`](docs/AUDIT_FULL_SYSTEM_READINESS.md) — Full institutional system readiness audit.
- [`docs/PROFITABILITY_REMEDIATION_FINAL_REPORT.md`](docs/PROFITABILITY_REMEDIATION_FINAL_REPORT.md) — Final report on edge verification and profitability remediation.
- [`docs/REMEDIATION_REPORT.md`](docs/REMEDIATION_REPORT.md) — Remediation details for risk policy and sizing bugs.
- [`docs/profitability_baseline_report.md`](docs/profitability_baseline_report.md) — Walk-forward performance baseline report.
- [`docs/profitability_failure_attribution.md`](docs/profitability_failure_attribution.md) — Statistical attribution of historical drawdowns.
- [`docs/profitability_phase0_audit.md`](docs/profitability_phase0_audit.md) — Initial codebase and data integrity audit.
- [`docs/research_backtest_report.md`](docs/research_backtest_report.md) — Event-driven backtest results and walk-forward decomposition.
- [`docs/threshold_sensitivity_report.md`](docs/threshold_sensitivity_report.md) — Sensitivity curves for ML probability thresholds.
- [`docs/weekly_entry_policy_audit.md`](docs/weekly_entry_policy_audit.md) — Weekly entry quotas and risk-meter audit.
- [`docs/weekly_entry_policy_experiment_report.md`](docs/weekly_entry_policy_experiment_report.md) — Experimental validation of weekly entry gates.
- [`docs/zero_setup_regression_audit.md`](docs/zero_setup_regression_audit.md) — Regression audit verifying setup filtering sensitivity.
- [`docs/runtime_reload_verification.md`](docs/runtime_reload_verification.md) — Hot-reload safety and background runner persistence audit.
- [`docs/runtime_verification_report.md`](docs/runtime_verification_report.md) — Comprehensive runtime behavior verification.

---

## 🚀 Quickstart & Setup

### 1. Prerequisites
- Python 3.11+
- Windows PowerShell, macOS, or Linux terminal
- Git

### 2. Installation
```powershell
# Clone the repository
git clone https://github.com/your-username/SignalGenerator.git
cd SignalGenerator

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # On Linux/macOS: source .venv/bin/activate

# Install required dependencies
pip install -r requirements.txt
```

### 3. Configuration
Copy the configuration template and configure optional Discord notifications:
```powershell
copy .env.example .env
```
Key settings in `.env`:
- `SCANNER_MODE=PAPER` (default and required for safety)
- `DISCORD_PRICE_ALERT_WEBHOOK_URL` (optional: webhook URL for perpetual price alerts)
- `ASTER_USER_ADDRESS`, `ASTER_SIGNER_ADDRESS`, `ASTER_SIGNER_PRIVATE_KEY` (optional: read-only live account monitoring)

### 4. Running Tests
Run the complete automated test suite (464 tests):
```powershell
pytest
```
Run focused tests:
```powershell
# Test Trading Journal model isolation
pytest tests/test_journal_model_isolation.py -v

# Test Tier 1 risk mathematical invariants
pytest tests/test_tier1_risk_policy.py -v

# Test Model B strategy and execution
pytest tests/test_model_b_strategy_and_risk.py -v
```

### 5. Launching the Web Dashboard
```powershell
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```
Open your browser and navigate to:  
👉 **`http://127.0.0.1:8000`**

---

## 🔒 License & Disclaimer

This software is strictly an experimental quantitative research and simulation tool. It is **not** an automated trading bot and does **not** provide financial advice. Live order execution is permanently disabled by safety policy (`live_trading_locked=True`). Always manage risk responsibly.
