# SMC-SNR ML Perpetual Setup Scanner

A cryptocurrency perpetual futures setup scanner built from scratch with zero generative AI, zero external LLMs, and zero paid inference dependencies. Designed specifically for small-account capital preservation and growth ($16 initial equity on Aster DEX).

## Key Features
- **Deterministic Market Structure**: Smart Money Concepts (Swing pivots without lookahead, BOS, CHoCH, Order Blocks, FVGs, Liquidity Sweeps) + Support & Resistance (Density clustering, rejection, retest strength).
- **Leakage-Safe Local Machine Learning**: Logistic Regression and LightGBM models trained on strictly chronological walk-forward splits with calibrated probabilities (Brier score & Isotonic/Platt calibration).
- **Institutional Risk Governance for $16 USDT**:
  - $16.00 initial capital in `ISOLATED` margin mode.
  - Hard limit of 10x leverage.
  - Risk budget: $0.09 per trade.
  - Daily loss limit: $0.18 (2 trade losses halts trading for the day).
  - Weekly loss limit: $0.54 (6 trade losses halts trading for the week).
  - Maximum 1 concurrent position across the entire account.
  - Maximum 3 entries per calendar week.
  - Strict minimum notional ($5.00) and lot size rounding. Infeasible trades are explicitly marked `INFEASIBLE`.
- **Target Exchange**: Aster DEX Perpetual Futures (`https://fapi.asterdex.com`).
- **Watchlist (11 Verified Pairs)**: ADAUSDT, XRPUSDT, BTCUSDT, BNBUSDT, ETHUSDT, SOLUSDT, AVAXUSDT, LINKUSDT, XMRUSDT, DOGEUSDT, LTCUSDT.
- **Modes**:
  - `RESEARCH`: Historical data ingestion, feature generation, ML training, backtesting.
  - `PAPER`: Live market data polling and simulated signal journaling without real capital exposure.
  - `LIVE`: Permanently locked and disabled in this release.

## Quickstart & Installation
```powershell
# Clone or navigate to repository
cd c:\Users\Administrator\Documents\SignalGenerator

# Install dependencies
pip install -r requirements.txt

# Run all test suites
pytest
```

## Running the Scanner & Dashboard
```powershell
# Start FastAPI backend and interactive dashboard
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
```
Navigate to `http://127.0.0.1:8000` to view the scanner dashboard.
