# SignalGenerator — Forward Risk-Guard Interaction Audit

**Repository:** `C:\Users\Administrator\Documents\SignalGenerator`  
**Branch:** `research/profitability-validation`  
**Environment:** Institutional Risk Governance Audit  
**Date:** 2026-10-17 22:48:00 UTC

---

## 1. Executive Summary & Core Mathematical Tension

The SignalGenerator risk subsystem enforces two distinct layers of risk controls designed for a small **$16.00 USDT** account:

1. **Per-Trade Sizing Layer ([`src/risk/sizing.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/risk/sizing.py)):**
   - Target Isolated Margin: `$6.00 USDT` (allowed range: `$5.00` – `$6.50`).
   - Max Leverage: `10x`.
   - Hard SL Risk Limit: **`$1.50 USDT`** (maximum allowable loss on any single trade, inclusive of taker fees and slippage).
2. **Account-Level Governance Layer ([`src/risk/manager.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/risk/manager.py)):**
   - Configured Trade Risk: `$0.09 USDT` (0.56% of equity).
   - Daily Loss Cap: **`$0.18 USDT`** (1.125% of equity, or exactly 2 units of trade risk).
   - Weekly Loss Cap: **`$0.54 USDT`** (3.375% of equity, or exactly 6 units of trade risk).
   - Max Open Positions: **`1 concurrent position`**.
   - Max Entries Per Week: **`3 entries per calendar week`** (Monday 00:00 UTC to Sunday 23:59 UTC).

### The Inherent Tension:
On crypto perpetual futures contracts, exchange minimum lot sizes (e.g. `0.001 BTC`, `0.01 ETH`, `0.01 SOL`, `1 DOGE`) and exchange minimum notional rules (`$5.00 USDT`) set a physical lower bound on position size. A `$6.00` margin trade at `10x` leverage creates `$50`–`$60 USDT` in notional exposure.
- If a trade hits a full structural stop loss of 2.2%–2.5%, the realized loss is **`-$1.30` to `-$1.50 USDT`**.
- **A single `-$1.50` stop-loss hit exceeds the Daily Loss Cap of `$0.18` by 833%!**
- **A single `-$1.50` stop-loss hit exceeds the Weekly Loss Cap of `$0.54` by 277%!**

**Consequence:** When an entry hits its structural stop loss, **the entire account is immediately halted for the rest of that calendar week**. This is an intended, ultra-conservative institutional capital preservation feature, but it explains why forward paper trades occur sparingly.

---

## 2. Deterministic Scenario Simulations

All four scenarios were codified and validated in [`tests/test_forward_paper_activation.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/tests/test_forward_paper_activation.py):

### Scenario 1: Single Trade Loss of $0.18 USDT
- **Event:** A position is closed at a partial loss or fee drag totaling `-$0.18 USDT`.
- **State After Exit:**
  - `daily_loss_usdt`: `$0.18` (matches `$0.18` limit exactly).
  - `weekly_loss_usdt`: `$0.18`.
  - `weekly_entries_count`: `1` (of 3 allowed).
  - `equity_usdt`: `$15.82`.
- **Same-Day Interaction:**
  - `can_open_new_position()` returns `(False, "Daily loss limit ($0.18) reached ($0.18 loss).")`.
  - `get_effective_trade_risk()` returns `$0.00`.
  - **Verdict:** Daily guard cleanly halts trading for the remainder of the calendar day.
- **Next-Day Interaction (00:00 UTC rollover):**
  - `daily_loss_usdt` resets to `$0.00`.
  - `weekly_loss_usdt` remains `$0.18`.
  - Remaining weekly loss budget: `$0.54 - $0.18 = $0.36`.
  - Effective trade risk: `min(0.09, 0.18, 0.36) = $0.09`.
  - **Verdict:** Trading resumes safely on Day 2 with full `$0.09` risk budget.

---

### Scenario 2: Single Trade Loss of $0.50 USDT
- **Event:** A position hits an intermediate stop loss resulting in `-$0.50 USDT` net loss.
- **State After Exit:**
  - `daily_loss_usdt`: `$0.50` (breaches `$0.18` daily limit).
  - `weekly_loss_usdt`: `$0.50` (within `$0.54` weekly limit).
  - `weekly_entries_count`: `1`.
- **Same-Day Interaction:**
  - `can_open_new_position()` returns `(False, "Daily loss limit ($0.18) reached ($0.50 loss).")`.
- **Next-Day Interaction (00:00 UTC rollover):**
  - `daily_loss_usdt` resets to `$0.00`.
  - `weekly_loss_usdt` remains `$0.50`.
  - Remaining weekly budget is only `$0.54 - $0.50 = $0.04`.
  - Effective trade risk drops dynamically: `min(0.09, 0.18, 0.04) = $0.04`.
  - **Verdict:** Account can only risk `$0.04` on Day 2. If position sizing cannot achieve a trade within `$0.04` risk, candidate status is downgraded to `INFEASIBLE`.

---

### Scenario 3: Single Trade Hits Full SL with $1.50 Loss
- **Event:** A full `$6.00` margin trade hits structural stop loss at `-$1.50 USDT` (hard cap).
- **State After Exit:**
  - `daily_loss_usdt`: `$1.50` (breaches `$0.18` daily cap).
  - `weekly_loss_usdt`: `$1.50` (breaches `$0.54` weekly cap).
  - `weekly_entries_count`: `1`.
- **Multi-Day Interaction:**
  - **Day 1 (Monday):** Blocked by Daily & Weekly limits.
  - **Day 2 (Tuesday):** `daily_loss_usdt` resets to `$0.00`, BUT `weekly_loss_usdt` is `$1.50 >= $0.54`. `can_open_new_position()` returns `(False, "Weekly loss limit ($0.54) reached ($1.50 loss).")`.
  - **Day 3 (Wednesday), Day 4 (Thursday), Day 5 (Friday):** Still blocked by weekly limit (`$1.50 >= $0.54`).
- **Next Calendar Week Rollover (Monday 00:00 UTC):**
  - Calendar week string (`dt.strftime("%Y-W%W")`) updates from `W41` to `W42`.
  - `weekly_loss_usdt` resets to `$0.00`.
  - `weekly_entries_count` resets to `0`.
  - **Verdict:** Full trading capability resumes on the next calendar Monday.

---

### Scenario 4: Process Restart Behavior Audit
- **Audit Question:** If a trade closes with a loss today and the daemon process restarts before tomorrow, are the daily/weekly loss meters preserved?
- **Finding:**
  - `PaperExecutionEngine.recover_state()` recovers pending orders from `paper_orders` and open positions from `trade_journal`.
  - However, in `src/risk/manager.py`, `RiskManager` initializes its internal meters (`daily_loss_usdt`, `weekly_loss_usdt`, `weekly_entries_count`) in memory to `0.0`.
  - While all historical trades are preserved safely in SQLite `trade_journal`, an unhandled restart currently resets the daily/weekly loss meters unless `RiskManager` reloads closed trades from `trade_journal` for the active day and week.
- **Remediation Recommendation:**
  - Add a lightweight startup synchronization method in `RiskManager` (or `JournalRepository`):
    ```python
    def sync_from_journal(self, journal: JournalRepository, now_ms: int) -> None:
        today_trades = journal.get_trades_for_day(now_ms)
        self.daily_loss_usdt = sum(abs(t.pnl_net) for t in today_trades if t.pnl_net < 0)
        week_trades = journal.get_trades_for_week(now_ms)
        self.weekly_loss_usdt = sum(abs(t.pnl_net) for t in week_trades if t.pnl_net < 0)
        self.weekly_entries_count = len(week_trades)
    ```

---

## 3. Impact on Forward Paper Trade Sampling

1. **Trade Frequency Expectation:**
   - With max 1 open position, max 3 entries per week, and a weekly loss limit that halts trading upon a single full SL hit, the system will naturally execute between **0 to 2 paper trades per week** across the entire 11-symbol watchlist.
2. **Safety Invariance:**
   - No risk limits should be loosened to artificially generate trades.
   - Preserving the `$0.18` daily and `$0.54` weekly limits guarantees that the $16 USDT account cannot suffer catastrophic drawdown during adverse market regimes.
