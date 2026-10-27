# Cross-Model Research Lessons & Empirical Truths

**Archive Document:** `data_penting/CROSS_MODEL_LESSONS.md`  
**Synthesized From:** `MODEL_A`, `MODEL_B`, `MODE_2C`, and `15M_SWEEP_RECLAIM_SMC`  
**Scope:** Institutional principles governing all quantitative strategy design in SignalGenerator  

---

## 1. Lesson 1: The Mid-Range Dealing Range Trap (Chop Fallacy)

### Empirical Evidence:
- **`MODE_2C` Backtest (`docs/mode2c_backtest_report.md`):** Mode 2C attempted to trade liquidity sweeps inside the middle 60% of the 72H dealing range ($0.20 \le \text{pos}_{72} \le 0.80$). It produced **911 setups**, a **35.49% win rate**, and **-$102.41 USDT Net Loss** (expectancy -$0.1382/trade).
- **`MODEL_B_V1`:** Chasing 15m momentum inside mid-range chop produced 11,594 trades and -$1,577 Net Loss.

### The Underlying Market Physics:
In dealing-range equilibrium, price lacks directional institutional flow. Orders cluster densely on both sides of the book. Candle wicks in mid-range routinely form **multi-wick liquidity cascades**, where each subsequent bar sweeps the previous high/low before oscillating back, repeatedly hitting stop losses before any significant target is reached.

### Production Rule (The Dealing Range Law):
> **Never enter Longs in Equilibrium ($38\%-65\%$) or Premium ($\ge 65\%$). Never enter Shorts in Equilibrium or Discount ($\le 38\%$). Only enter Longs in deep discount ($\le 38\%$) and Shorts in premium ($\ge 65\%$).**

---

## 2. Lesson 2: The Fee Friction Floor on Crypto Perpetuals

### Empirical Evidence:
- **`MODEL_B_V1` Backtest (`backtest_results/model_a_vs_model_b_comparison/REPORT.md`):** Model B paid **$1,118.41 in exchange fees and slippage** on an account starting with $16.00 capital! Fees consumed more than **244%** of the gross trading loss.

### The Underlying Market Physics:
On AsterDEX / Binance perpetual futures, the standard round-trip friction is:
$$\text{Friction} = 0.02\% \text{ (Maker Entry)} + 0.05\% \text{ (Taker SL)} + 0.05\% \text{ (Adverse Slippage)} = 0.12\% \text{ of notional}$$
At 10x leverage, a 0.12% move represents **1.2% of account margin** per trade. If a scalping strategy targets small moves ($0.8\%-1.2\%$), fees consume **$30\%-60\%$ of all gross profits**, turning even a 50% win-rate system into guaranteed negative expectancy.

### Production Rule (The Net R:R Law):
> **Every live trade setup must have a structural take-profit target that yields at least 1.50R Net after round-trip fees. Never run high-turnover scalping strategies on crypto perpetuals without wide payoff ratios.**

---

## 3. Lesson 3: Structural Invalidation vs. Price-Clamping

### Empirical Evidence:
- **`MODEL_A` Defect (`docs/profitability_failure_attribution.md`):** In `src/strategy/planner.py`, stop losses were artificially clamped to $2.2\%$ so that a $\$60$ notional position wouldn't exceed the $\$1.50$ hard dollar risk cap. This pulled stops *inside* the Order Block or Swing Low, resulting in premature stop-outs during routine noise.

### The Underlying Market Physics:
The market does not know or care about an individual account's dollar risk cap. Support and resistance levels are established by aggregate order flow. Placing a stop loss inside market structure because your position size is too large guarantees that normal noise sweeps your stop before the trade thesis is invalidated.

### Production Rule (The Sizing Invariant):
> **Never clamp the stop-loss price to meet risk limits. Stop-loss placement is dictated strictly by market structure. Risk limits must be satisfied by shrinking position size (quantity), never by pulling the stop price closer to entry.**

---

## 4. Lesson 4: Adverse Selection in Limit-Order Breakouts

### Empirical Evidence:
- **`MODEL_A` Limit Fill Model:** In `PaperExecutionEngine`, limit breakout orders were placed $0.025\%$ below close. Over out-of-sample testing, win rate collapsed from $56\%$ to $38.5\%$.

### The Underlying Market Physics:
When a genuine strong breakout occurs, price drives forward with aggressive market buying and rarely retraces $0.025\%$ to fill a resting limit order; the best winning trades expire unfilled (`EXPIRED_UNFILLED`). Conversely, when a breakout is false, price reverses aggressively, easily filling the limit order on the way down to the stop loss.

### Production Rule (The Sweep-Reclaim Invariant):
> **Do not trade breakouts. Trade the liquidity sweep of a failed breakout. Wait for price to pierce prior swing highs/lows, sweep stop orders, and body-reclaim back inside the range before entering.**

---

## 5. Lesson 5: Temporal Clustering & The 27-Day Signal Drought

### Empirical Evidence:
- **`MODEL_A` Clustering:** 50 of 92 historical setups occurred during a single 3-day market surge (`2026-08-20..22`). Outside that surge, the model produced 0 setups for 27 consecutive days.

### The Underlying Market Physics:
Strategies whose conditions require 10+ simultaneous hardcoded threshold matches (variance ratio, multi-day returns, boundary touches) become overfitted to specific volatility bursts and completely blind to normal market cycles.

### Production Rule (The Multi-Pair Independent Screening Law):
> **Screening logic must be decoupled from fixed volatility regimes. The Groq AI Auto-Hunter scans all pairs dynamically, uses zero-token Python pre-filtering to reject dead pairs cleanly, and maintains a diversified top-10 candidate pool.**

---

## 6. Lesson 6: Backtest Engine Fidelity vs. Live Production Code

### Empirical Evidence:
- **`BACKTEST_ENGINE_VALIDATION.md`:** Historical backtests used `src/backtest/engine.py`, which executed Model A on 1H candles. It did not model 15m execution, sweep reclaims, dynamic sweet-spot TP, or EIP-712 order brackets.

### Production Rule (The Engine Parity Law):
> **Never cite a backtest from a different engine, timeframe, or strategy version as evidence for the live production code. The live strategy (`15M_SWEEP_RECLAIM_SMC`) must be evaluated with its own dedicated tests, exact fee assumptions, and exchange reconciliation logs.**
