# Forward Cohort Integrity & Governance Specification

**Cohort Identifier:** `COHORT_20261017_INSTITUTIONAL_SMC`  
**Anchor Timestamp:** `2026-10-17 15:00:00 UTC`  
**Baseline Git Commit:** `a89b15a4a24b84a4db5f7e622fefffc70c876f75`  
**Active Git Branch:** `research/profitability-validation`  
**Validation Status:** `UNPROVEN_NO_COMPLETED_FORWARD_TRADES`  
**Target Sample Size:** $\ge 30$ completed forward paper trades  

---

## 1. Forward Cohort Invariants

The forward validation cohort is established to measure whether the institutional SMC/SnR strategy possesses a statistically reproducible edge in forward live-market conditions. To ensure scientific integrity, none of the parameters below may be altered, loosened, or retroactively tuned.

### 1.1 Watchlist Universe (11 Perpetual Contracts)
The watchlist is strictly confined to 11 high-liquidity perpetual futures contracts quoted against USDT:
1. `BTCUSDT`
2. `ETHUSDT`
3. `SOLUSDT`
4. `BNBUSDT`
5. `XRPUSDT`
6. `ADAUSDT`
7. `DOGEUSDT`
8. `AVAXUSDT`
9. `LINKUSDT`
10. `SUIUSDT`
11. `NEARUSDT`

### 1.2 Strategy Architecture & Thresholds
- **Timeframes:** 4H for macro market bias and structural trend alignment; 1H for entry execution and candle close evaluation.
- **Dealing Range (72H Anchor):**
  - Extreme Discount Zone: `Range Position <= 0.12` (Required for LONG setups).
  - Extreme Premium Zone: `Range Position >= 0.88` (Required for SHORT setups).
  - Neutral Zone (`0.12 < Range Position < 0.88`): Strict `NO_TRADE`. No setups permitted in equilibrium.
- **Structural Confluence Requirements:**
  - Break of Structure (BOS) / Change of Character (CHoCH) on 1H.
  - Unmitigated Order Block (OB) alignment.
  - Fair Value Gap (FVG) or support/resistance cluster confluence.
- **Trade Direction Filter:** Setup direction must align with 4H higher-timeframe market bias (`BULLISH` -> LONG only; `BEARISH` -> SHORT only).

---

## 2. Execution & Fill Simulation Invariants

Paper trading orders simulate institutional exchange reality by enforcing adverse execution constraints:

| Execution Parameter | Configured Value | Operational Purpose |
|---|---|---|
| **Fill Model Version** | `v1_conservative` | Conservative limit order fill simulation. |
| **Penetration Buffer** | `0.05%` (`0.0005`) | Price must penetrate past the limit price by 5 bps before filling. Mere price touch does not fill. |
| **Maker Entry Fee** | `0.02%` (`0.0002`) | Applied to entry limit orders. |
| **Taker Exit Fee** | `0.05%` (`0.0005`) | Applied to market stop loss and take profit fills. |
| **Slippage Rate** | `0.05%` (`0.0005`) | Deducted on market order exits (SL and TP fills). |
| **Order Expiration** | `12 bars` (12 hours) | Unfilled pending limit orders expire and cancel after 12 1H candles. |
| **Partial Close at TP1** | `50%` | Closes 50% of position quantity at TP1. |
| **Post-TP1 SL Adjustment**| Fee-adjusted Break-Even | SL moved to entry price + estimated round-trip fees/slippage. |
| **Trailing Stop** | Armed post-TP1 | Trails the remaining 50% quantity toward TP2 at $1.0R$ trailing distance. |

---

## 3. Account Capital & Risk Governance Invariants

Risk guardrails enforce mathematical capital preservation and cannot be overridden by ML scoring or manual intervention:

| Risk Guard Dimension | Invariant Bound | Verification Status |
|---|---|---|
| **Initial Forward Equity** | `$16.00 USDT` | Verified |
| **Target Isolated Margin** | `$6.00 USDT` (Range: $6.00–$6.50) | Enforced by PositionSizer |
| **Maximum Leverage** | `10x` | Enforced at contract level |
| **Maximum Open Positions** | `1` | Strictly enforced by RiskManager |
| **Maximum Entries per Week**| `3` | Strictly enforced by RiskManager |
| **Hard Loss Cap per SL** | `$1.50 USDT` (inclusive of fees & slippage) | Sizing rejects any trade where SL loss > $1.50 |
| **Configured Trade Risk** | `$0.09 USDT` (approx 0.56% of equity) | Baseline unit risk budget |
| **Daily Loss Limit** | `$0.18 USDT` | Halted for day if breached |
| **Weekly Loss Limit** | `$0.54 USDT` | Halted for week until Monday 00:00 UTC if breached |
| **Live Trading Gate** | `live_trading_locked = True` | Locked; real mutation disabled |
| **Execution Mode** | `SCANNER_MODE = PAPER` | Paper execution engine active |

---

## 4. Cohort Tracking & Evaluation Criteria

Forward paper validation progress is tracked via `compute_forward_validation_cohort()` in `JournalRepository`:

```python
min_required_trades = 30
if closed_count == 0:
    validation_status = "UNPROVEN_NO_COMPLETED_FORWARD_TRADES"
elif closed_count < min_required_trades:
    validation_status = "NEGATIVE_NET_EXPECTANCY" if net_pnl < 0 else "INSUFFICIENT_FORWARD_EVIDENCE"
else:
    validation_status = "NEGATIVE_NET_EXPECTANCY" if net_pnl <= 0 else "PRELIMINARY_POSITIVE_NOT_VALIDATED"
```

### 4.1 Truthful Reporting Safeguards
- **Zero Synthetic Pollution:** `data/scanner.db` contains 0 synthetic trades.
- **Statistical Significance Threshold:** A minimum of **30 completed forward trades** is mandatory before any claim of profitability or statistical edge can be made.
- **No In-Flight Metric Distortions:** Forward paper metrics evaluate only closed forward trades recorded in `trade_journal`.
- **Anti-Overfitting Discipline:** Dealing range boundaries `[0.12, 0.88]` remain active throughout the forward cohort to evaluate the unmodified institutional baseline.

---

## 5. Audit Certification

- **Cohort Start Integrity:** Confirmed anchored at `2026-10-17 15:00:00 UTC`.
- **Configuration Drift:** Zero drift detected.
- **Database Status:** Healthy; `trade_journal` count = 0; `paper_orders` count = 0; `paper_lifecycle_events` count = 43.
- **Cohort Status:** **AUTHENTIC & VALIDATED**.
