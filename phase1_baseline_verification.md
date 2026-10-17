# SignalGenerator — Phase 1 Baseline Verification Report

**Branch:** `research/profitability-validation`  
**Git Commit Hash:** `a89b15a4a24b84a4db5f7e622fefffc70c876f75`  
**Date of Verification:** 2026-10-17 22:40:00 UTC  
**Environment:** Local Research Environment (Windows, Python 3.12, SQLite `data/scanner.db`)

---

## 1. Executive Summary & Verification Objective

The mission of Task 1 is to independently verify the data integrity, execution assumptions, and exact filter implementation of the SignalGenerator baseline before evaluating any strategy modification or filter ablation. 

### Key Findings:
1. **Data Completeness & Integrity:** Exactly 1-year historical dataset across all 11 watchlist symbols in `data/scanner.db`:
   - **Total 1H Candles:** `95,007` bars (`0` missing bars / gaps, `0` duplicates).
   - **Total 4H Candles:** `23,741` bars (`0` missing bars / gaps, `0` duplicates).
   - Timestamp Range: `2025-10-14 11:00:00 UTC` to `2026-10-17 14:00:00 UTC` (368.1 days).
2. **72H Dealing Range Position Filter:** Mathematically verified in [`src/strategy/generator.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/strategy/generator.py#L118-L178). Computes `pos_in_range_72 = (current_price - min_low_72) / (max_high_72 - min_low_72)`. Baseline thresholds enforce `pos_in_range_72 <= 0.12` for discount longs, `>= 0.88` for premium shorts, and `>= 0.92` for high-momentum bull expansion longs.
3. **Execution Model Discrepancy (Identified & Resolved):** 
   - Historical backtest reports in early documentation showed negative expectancy primarily due to an aggressive market-taker execution model in `BacktestEngine` (0.05% taker fee + 0.05% slippage on entry, single-leg TP1, no trailing stop, holding cap 48 bars).
   - Under realistic `PaperExecutionEngine` rules (maker limit fills with 0.02% fee, 0.05% conservative price penetration buffer, pre-fill SL invalidation, TP1 50% partial close, fee-adjusted break-even move, and 0.5R trailing stop toward TP2), the **Baseline Standalone Expectancy is +$1.0470 per trade** across 74 completed fills (Win Rate 66.22%, Profit Factor 3.20, Max DD $7.15).
   - Under institutional portfolio risk constraints (max 1 open position, max 3 entries/week, $6 margin / $1.50 hard SL limit), **Baseline Portfolio Expectancy is +$1.0231 per trade** across 13 completed trades (Win Rate 69.23%, Profit Factor 3.38, Max DD $4.15).

---

## 2. 1-Year Historical Dataset Completeness Audit

Candle completeness and chronological continuity were audited directly via `DatabaseManager.get_candles()` across all 11 active symbols in the institutional watchlist.

| Symbol | 1H Candle Count | Earliest 1H (UTC) | Latest 1H (UTC) | 1H Gaps | 1H Dups | 4H Candle Count | Earliest 4H (UTC) | Latest 4H (UTC) | 4H Gaps |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **ADAUSDT** | 8,836 | 2025-10-14 11:00 | 2026-10-17 14:00 | 0 | 0 | 2,208 | 2025-10-14 12:00 | 2026-10-17 08:00 | 0 |
| **XRPUSDT** | 8,836 | 2025-10-14 11:00 | 2026-10-17 14:00 | 0 | 0 | 2,208 | 2025-10-14 12:00 | 2026-10-17 08:00 | 0 |
| **BTCUSDT** | 8,836 | 2025-10-14 11:00 | 2026-10-17 14:00 | 0 | 0 | 2,208 | 2025-10-14 12:00 | 2026-10-17 08:00 | 0 |
| **BNBUSDT** | 8,836 | 2025-10-14 11:00 | 2026-10-17 14:00 | 0 | 0 | 2,208 | 2025-10-14 12:00 | 2026-10-17 08:00 | 0 |
| **ETHUSDT** | 8,836 | 2025-10-14 11:00 | 2026-10-17 14:00 | 0 | 0 | 2,208 | 2025-10-14 12:00 | 2026-10-17 08:00 | 0 |
| **SOLUSDT** | 8,836 | 2025-10-14 11:00 | 2026-10-17 14:00 | 0 | 0 | 2,208 | 2025-10-14 12:00 | 2026-10-17 08:00 | 0 |
| **AVAXUSDT**| 8,836 | 2025-10-14 11:00 | 2026-10-17 14:00 | 0 | 0 | 2,208 | 2025-10-14 12:00 | 2026-10-17 08:00 | 0 |
| **LINKUSDT**| 8,836 | 2025-10-14 11:00 | 2026-10-17 14:00 | 0 | 0 | 2,208 | 2025-10-14 12:00 | 2026-10-17 08:00 | 0 |
| **XMRUSDT** | 6,647 | 2026-01-13 14:00 | 2026-10-17 14:00 | 0 | 0 | 1,661 | 2026-01-13 16:00 | 2026-10-17 08:00 | 0 |
| **DOGEUSDT**| 8,836 | 2025-10-14 11:00 | 2026-10-17 14:00 | 0 | 0 | 2,208 | 2025-10-14 12:00 | 2026-10-17 08:00 | 0 |
| **LTCUSDT** | 8,836 | 2025-10-14 11:00 | 2026-10-17 14:00 | 0 | 0 | 2,208 | 2025-10-14 12:00 | 2026-10-17 08:00 | 0 |
| **AGGREGATE**| **95,007** | **2025-10-14 11:00** | **2026-10-17 14:00** | **0** | **0** | **23,741** | **2025-10-14 12:00** | **2026-10-17 08:00** | **0** |

*Note on XMRUSDT:* Historical candle synchronization for XMRUSDT commences on 2026-01-13 14:00 UTC due to AsterDEX perpetual contract listing history. There are 0 internal gaps or duplicated timestamps.

---

## 3. Exact Implementation of Entry Filters & Evaluation Order

The production setup generation logic is codified in [`SetupGenerator.generate_candidate()`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/strategy/generator.py#L90-L240). Every 1H candle is passed through 7 sequential stages:

```
[1H Candle Close] 
       │
       ▼
[Stage 0: 72H Dealing Range & Multi-Horizon Momentum Pre-Screen]
   ├── Mode 1: SHORT Exhaustion (pos_72 >= 0.88 AND ret_48 >= 6.2% AND ret_12 >= 2.2% AND ret_168 <= 4.5% AND close < open)
   ├── Mode 2A: LONG Discount Rejection (pos_72 <= 0.12 AND ret_48 <= -6.2% AND ret_12 <= -2.2% AND ret_168 >= 2.0% AND close > open)
   └── Mode 2B: LONG Bull Expansion (pos_72 >= 0.92 AND ret_24 >= 5.0% AND ret_48 >= 6.5% AND ret_168 >= 8.0% AND close > open AND 1.10 <= RVOL <= 2.30 AND VR >= 1.17)
       │ (If none matched -> REJECTED: Equilibrium or Momentum Mismatch)
       ▼
[Stage 1: Multi-Timeframe 4H Bias Alignment]
   ├── SHORT: 4H Bias can be NEUTRAL or BEARISH (cannot enter if 4H Bias is strongly conflicting)
   ├── Mode 2A LONG: 4H Bias cannot be BEARISH (must be NEUTRAL or BULLISH)
   └── Mode 2B LONG: 4H Bias MUST be BULLISH (strict trend requirement)
       │
       ▼
[Stage 2: 1H Market Structure & Sweeps]
   ├── Swing Point Detection (left=5, right=5)
   ├── Structure State (BULLISH / BEARISH / RANGING)
   └── Liquidity Sweep Detection & Order Blocks
       │
       ▼
[Stage 3: 1H Support & Resistance Interaction]
   ├── Swing Clustering into S&R Zones (sr_tolerance = 0.8%)
   └── Identification of nearest support and resistance levels
       │
       ▼
[Stage 4: Structural Entry, SL, and Opposing Wall 1.2R Filter]
   ├── SHORT: SL set above latest confirmed swing high / candle high + 0.35*ATR
   │   └── Opposing Wall Check: nearest_support must be at least 1.2 * (SL_dist) below entry (gross_rr >= 1.2R)
   ├── LONG: SL set below latest confirmed swing low / candle low - 0.35*ATR
   │   └── Opposing Wall Check: nearest_resistance must be at least 1.2 * (SL_dist) above entry (gross_rr >= 1.2R)
       │
       ▼
[Stage 5: TP/SL Planning & Net R:R Verification]
   ├── TP1 target set at structural SnR or 1.6R default (min gross R:R >= 1.5R)
   ├── Deduct Maker/Taker round-trip fees (0.07%) and slippage (0.10%)
   └── Net R:R must satisfy >= 1.20R (net_reward_to_risk >= 1.20)
       │
       ▼
[Stage 6: Isolated Risk Sizing & Capital Feasibility ($16 Equity)]
   ├── Max leverage: 10x
   ├── Target isolated margin: $6.00 (allowed range: $5.00 - $6.50)
   ├── Hard risk cap: Estimated loss at SL <= $1.50 USDT (inclusive of taker fee and adverse slippage)
   ├── Minimum notional: >= $5.00 USDT
   └── If lot step size quantization violates min_notional or hard risk cap -> REJECTED_RISK_SIZING_INFEASIBLE
       │
       ▼
[ELIGIBLE SETUP CANDIDATE]
```

---

## 4. Execution Model Parity: BacktestEngine vs PaperExecutionEngine

A critical source of past confusion was the divergence between the legacy `BacktestEngine` and the realistic `PaperExecutionEngine`.

| Dimension | BacktestEngine (Legacy/Research) | PaperExecutionEngine (V3 Realistic Paper) |
| :--- | :--- | :--- |
| **Order Type** | Immediate Market-on-Close (Bar $t$) | Pending LIMIT Order placed at Bar $t$, evaluated on Bars $> t$ |
| **Order Expiry (TTL)** | None (Immediate Fill) | 12 Bars (12 Hours) |
| **Fill Condition** | Guaranteed immediate entry at Close | Conservative price penetration buffer: $0.05\%$ past entry |
| **Entry Fee** | Taker fee: $0.05\%$ ($0.0005$) | Maker fee: $0.02\%$ ($0.0002$) |
| **Pre-Fill Invalidation** | Not applicable | Active: order cancelled if price touches SL before entry fill |
| **Exit Strategy** | Single-leg TP1 or SL exit; max 48 bars | 2-Stage Lifecycle: 50% partial close at TP1 + Trailing Stop to TP2 |
| **Post-TP1 Behavior** | Trade closes completely at TP1 | SL moved to fee-adjusted Break-Even; $0.5R$ trailing stop ratchets |
| **Exit Fee** | Taker fee $0.05\%$ on entire quantity | TP1 maker/taker partial fee + Taker fee on trailing exit |
| **Funding Settlement**| Deducted hourly from funding cache | Deducted hourly from funding cache |
| **Baseline 1-Yr Net PnL**| **+$61.30 USDT** (Exp: **+$0.6967**) | **+$77.48 USDT** (Exp: **+$1.0470**) |

### Empirical Parity Audit:
- When tested on the exact same 88 eligible setups:
  - `PaperExecutionEngine` fills `74` orders (`84.09%` fill rate), invalidates `13` orders before fill (`14.77%`), and expires `1` order (`1.14%`).
  - The `13` invalidated setups save the system from taking immediate SL hits that occurred under the naive immediate-fill model.
  - The TP1 partial close + fee-adjusted break-even trailing stop secures profit on `50%` of the position while allowing runners to capture extended trends, boosting Standalone Expectancy from `+$0.6967` to `+$1.0470`.

---

## 5. Baseline Reproducibility Summary

- **Script:** [`scratch/run_phase1_diagnosis.py`](file:///C:/Users/Administrator/.gemini/antigravity/brain/adae8d27-6351-440f-b44a-c1994272e623/scratch/run_phase1_diagnosis.py)
- **Trade-Level CSV Export:** [`baseline_trades.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/baseline_trades.csv) (contains all eligible setups with bar-level timestamps, execution statuses, and PnL).
- **Standalone Paper Metrics:** 
  - Completed Trades: `74`
  - Win Rate: `66.22%` (`49` wins, `25` losses)
  - Net PnL: `+$77.4750 USDT`
  - Expectancy: `+$1.0470 USDT`
  - Profit Factor: `3.20`
  - Max Drawdown: `$7.1506 USDT`
  - 95% Bootstrap Confidence Interval: `[+$0.6232, +$1.4698]`
- **Portfolio Paper Metrics (with Hard Risk Constraints):**
  - Completed Trades: `13`
  - Win Rate: `69.23%` (`9` wins, `4` losses)
  - Net PnL: `+$13.3006 USDT`
  - Expectancy: `+$1.0231 USDT`
  - Profit Factor: `3.38`
  - Max Drawdown: `$4.1483 USDT`

*Task 1 baseline verification is complete, fully reproducible, and verified against production code.*
