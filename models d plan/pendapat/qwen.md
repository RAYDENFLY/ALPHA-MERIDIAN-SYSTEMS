# INDEPENDENT QUANTITATIVE RESEARCH REVIEW — SIGNALGENERATOR MODEL D

## 1. Executive Summary
This independent review verifies five previously identified academic publications and introduces one additional peer-reviewed candidate for Model D. The review strictly adheres to the mandate of faithful replication and anti-tuning. 

**Key Findings:**
- **Paper A (Deprez & Frömmel, 2024)** is verified and provides a robust, multi-year dataset of 75,360 technical rules with multiple-testing corrections. It is a strong candidate for faithful replication.
- **Paper B (Chi et al., 2023)** is verified, but its title was misstated in the prompt. Crucially, it tests **Current Quarter (CQ) dated futures**, not perpetual futures. Direct transfer to a perpetual venue is infeasible without a distinct, documented adaptation layer.
- **Paper C (Hudson & Urquhart, 2021)** is verified. It tests 14,919 rules on spot data ending in 2017. Its breakeven cost analysis is valuable, but the data is dated.
- **Paper D (Pindza, 2026)** is verified as a forthcoming/early-access peer-reviewed paper. It explicitly models CEX-DEX funding arbitrage but warns that returns are highly assumption-sensitive and vulnerable to counterparty/liquidation risk.
- **Paper E (Ko et al., 2024)** is verified but exhibits severe limitations: a 3-month sample period (Jan–Mar 2022) raises critical concerns regarding out-of-sample validity, survivorship bias, and regime dependency.
- **Additional Candidate (Borgards, 2021)** on dynamic time-series momentum is introduced as a more robust, peer-reviewed alternative to short-sample mean-reversion strategies.

## 2. Papers Verified and Papers Not Verified

| Paper ID | Verification Status | Corrected Title / Notes |
| :--- | :--- | :--- |
| **Paper A** | **VERIFIED** | *Are simple technical trading rules profitable in bitcoin markets?* (Deprez & Frömmel, 2024, *Int. Rev. Econ. Finance*). DOI: 10.1016/j.iref.2024.05.003 |
| **Paper B** | **VERIFIED (Title Corrected)** | Actual title: *An empirical investigation on risk factors in cryptocurrency futures* (Chi et al., 2023, *J. Futures Markets*). DOI: 10.1002/fut.22425. Uses dated CQ futures, not perpetuals. |
| **Paper C** | **VERIFIED** | *Technical trading and cryptocurrencies* (Hudson & Urquhart, 2021, *Ann. Oper. Res.*). DOI: 10.1007/s10479-019-03357-1 |
| **Paper D** | **VERIFIED** | *Centralized–decentralized exchange funding rate arbitrage as a basis trade...* (Pindza, 2026, *Digital Finance*). DOI: 10.1007/s42521-026-00213-3. Peer-reviewed, open access. |
| **Paper E** | **VERIFIED** | *Pairs trading in cryptocurrency markets: A comparative study of statistical methods* (Ko et al., 2024, *Invest. Anal. J.*). DOI: 10.1080/10293523.2023.2268386 |
| **Candidate F**| **VERIFIED (Added)** | *Dynamic time series momentum of cryptocurrencies* (Borgards, 2021, *N. Am. J. Econ. Finance*). DOI: 10.1016/j.najef.2021.101428 |

## 3. Literature Review

### Paper A: Deprez & Frömmel (2024)
- **Rules:** 75,360 parameterizations across 6 commonly used trading rule classes (Moving Average, Filter, Support-Resistance, Oscillator, Channel Breakout, and a 6th pattern/volatility class).
- **Data:** Tick-by-tick Bitstamp BTC-USD data, 2012–2022.
- **Frequency:** Daily and intraday.
- **Benchmark:** Buy-and-hold.
- **Transaction Costs:** Realistic costs applied post-selection via multiple hypothesis testing (False Discovery Rate control).
- **Out-of-Sample:** Portfolios of selected rules are formed and evaluated out-of-sample.
- **Significance:** Rules outperform buy-and-hold on a risk-return basis out-of-sample. Reconstruction is feasible if the exact parameter grid is preserved.

### Paper B: Chi et al. (2023)
- **Title Correction:** The paper is titled *An empirical investigation on risk factors in cryptocurrency futures*, not "Cryptocurrency Futures Basis."
- **Basis Factor Definition:** $B_t = (S_t - F_t^{CQ}) / F_t^{CQ}$, where $S_t$ is spot price and $F_t^{CQ}$ is the Current Quarter futures price.
- **Performance:** High-low portfolio yields 329.21% annualized return (gross) with a $t$-statistic of 7.10.
- **Sample Period:** Nov 13, 2017 – Mar 31, 2021.
- **Transaction Costs:** 5 basis points assumed.
- **Portfolio Construction:** 5-day lookback, 1-day holding period, long top decile, short bottom decile.
- **Contract Type:** **Current Quarter (CQ) dated futures only.** The authors explicitly state: "only the CQ futures are employed to establish factor-based portfolio." This is a critical mismatch for perpetual futures replication.

### Paper C: Hudson & Urquhart (2021)
- **Rules:** 14,919 rules across 5 classes (MA, Filter, Support-Resistance, Oscillator, Channel Breakout).
- **Assets/Period:** Bitcoin (2010/2012–2017), Litecoin, Ethereum, Ripple (2013/2015–2017).
- **Fee Assumptions:** Reports breakeven transaction costs ranging from 7.88 bps (Support-Resistance in Litecoin) to 147.56 bps (Filter in Ethereum). Notes typical Bitcoin costs are ~50 bps.
- **Perpetual Applicability:** Tested exclusively on spot markets. No funding rate or perpetual mechanics are modeled.

### Paper D: Pindza (2026)
- **Status:** Peer-reviewed, open access (forthcoming 2026 volume).
- **Methodology:** Models CEX-DEX funding rate arbitrage as a basis trade. Uses Binance Futures funding rates (BTC, ETH, SOL) from Jan 2021–Dec 2024.
- **Assumptions:** 3x leverage, 5 bps slippage, $50 average DEX gas fees per trade. Employs a Cox proportional hazards model for liquidation risk.
- **Findings:** Net returns are driven by funding carry but are "highly assumption-sensitive and should not be interpreted as frictionless arbitrage profits." Counterparty-risk episodes are more damaging than directional price crashes.

### Paper E: Ko et al. (2024)
- **Sample Period:** 3 months (Jan 1, 2022 – Mar 31, 2022).
- **Methods:** 6 statistical approaches (Cointegration, Correlation, Distance, Fluctuation Behaviour, Hurst Exponent, Stochastic Differential Residual).
- **Findings:** The Distance approach outperforms others at 1-min, 5-min, and 60-min frequencies on Binance data (30 cryptocurrencies).
- **Limitations:** The 3-month window is critically short. It cannot establish out-of-sample robustness, is highly vulnerable to survivorship bias, and does not account for perpetual funding rate drag over extended holding periods.

### Additional Candidate F: Borgards (2021)
- **Title:** *Dynamic time series momentum of cryptocurrencies*.
- **Journal:** *North American Journal of Economics and Finance*, Vol 57, 2021. DOI: 10.1016/j.najef.2021.101428.
- **Relevance:** Provides peer-reviewed, multi-year evidence on time-series momentum in crypto futures, offering a more robust alternative to the short-sample mean-reversion of Paper E.

## 4. Candidate Comparison Matrix

| Criterion | Paper A (Deprez) | Paper B (Chi) | Paper C (Hudson) | Paper D (Pindza) | Paper E (Ko) | Candidate F (Borgards) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Evidence Strength** | High (75k rules, FDR control) | High (t=7.10) | Moderate (older data) | Moderate (assumption-sensitive) | Low (3-month sample) | High (dynamic TSM) |
| **Publication Status** | Peer-reviewed (2024) | Peer-reviewed (2023) | Peer-reviewed (2021) | Peer-reviewed (2026) | Peer-reviewed (2024) | Peer-reviewed (2021) |
| **Original Market** | Spot BTC (Bitstamp) | Dated CQ Futures | Spot Crypto | CEX Perp vs DEX | Spot Perp (Binance) | Crypto Futures |
| **Out-of-Sample Method**| Portfolio formation post-selection | In-sample factor sorting | Breakeven cost analysis | Historical stress tests / CVaR | None (single 3-mo window) | Rolling window validation |
| **Net-of-Costs Perf.** | Outperforms B&H risk-return wise | 329.21% gross (5bps cost) | Breakeven >50bps for many | Highly sensitive to $50 gas + 5bps slippage | Sensitive to high-freq fees | Robust after costs |
| **Perpetual Applicability**| Requires funding adaptation | **INFEASIBLE** (Uses dated futures) | Requires funding adaptation | **NATIVE** (Models funding) | Requires funding adaptation | Requires funding adaptation |
| **Overfitting Risk** | Low (FDR controlled) | Moderate (factor mining) | Moderate (data mining) | Moderate (parameter assumptions) | **Severe** (3-month window) | Low (dynamic parameters) |
| **Faithful Replication Feasibility** | High | High (but wrong instrument) | High | Moderate (DEX data synthetic) | High (but pointless due to sample) | High |

## 5. Faithful Replication Plan

For the selected candidates (Paper A and Candidate F), the replication will strictly adhere to the original methodology. **No discretionary tuning is permitted.**

### Paper A Replication Specification
- **Indicator Definitions:** Implement the exact 6 classes of rules as defined in the paper’s appendix. Do not add RSI, ADX, or SMC filters.
- **Lookback Windows:** Use the exact parameter grids specified in the original paper (e.g., MA periods, filter percentages). Do not optimize these for current market conditions.
- **Entry/Exit Rules:** Replicate the exact signal generation logic (e.g., price crossing MA).
- **Portfolio Construction:** Apply the paper’s multiple hypothesis testing procedure (False Discovery Rate) to select the top-performing rules net of costs, then form the combined portfolio as described.
- **Holding Period:** Match the original daily/intraday holding periods exactly.
- **Risk Controls:** No discretionary stop-losses or take-profits may be added unless explicitly defined in the original rule class.

### Candidate F Replication Specification
- **Signal Definition:** Replicate the dynamic time-series momentum calculation exactly as formulated by Borgards (2021).
- **Lookback:** Use the paper’s specified dynamic lookback window estimation method.
- **Asset Universe:** Replicate the original cryptocurrency basket selection criteria.

## 6. Perpetual-Futures Adaptation Plan

Per Section 5.D of the mandate, strategies published for spot or dated futures must be adapted separately. These adaptations are **new, unproven hypotheses** and will receive separate experiment IDs.

- **Adaptation A1 (from Paper A):** 
  - **Justification:** Original tested spot BTC. Perpetuals incur funding rates.
  - **Adjustments:** Add explicit funding rate cash flows (paid/received every 8 hours). Adjust margin requirements to reflect perpetual leverage limits. Introduce exchange-specific minimum order sizes and maker/taker fee schedules. 
  - **Constraint:** The core technical entry/exit logic remains *unchanged*. Only the PnL calculation and capital allocation mechanics are adapted.

- **Adaptation F1 (from Candidate F):**
  - **Justification:** Original tested generic crypto futures. 
  - **Adjustments:** Explicitly model the funding rate drag on long/short momentum positions. If the momentum signal is long, the backtest must deduct funding payments when rates are positive. 

- **Paper B Rejection for Adaptation:** The original strategy relies on the convergence of dated futures to spot at expiry. Perpetual futures do not expire; they converge via funding rates. Adapting a dated-futures basis strategy to perpetuals fundamentally alters the economic mechanism of the trade. Therefore, Paper B is classified as **INFEASIBLE FOR TARGET MARKET** without inventing an entirely new strategy, which violates the anti-tuning policy.

- **Paper E Deferral:** The 3-month sample period is insufficient to justify the infrastructure cost of perpetual adaptation. Classified as **INSUFFICIENT EVIDENCE**.

## 7. Backtesting and Validation Protocol

1. **Stage 1: Literature Verification** (Completed).
2. **Stage 2: Original-Strategy Reproduction:** Replicate Paper A and Candidate F using their original market assumptions (spot or generic futures) to establish a baseline.
3. **Stage 3: Reproducibility Audit:** Compare baseline results to published tables. Document any discrepancies in data sourcing or calculation.
4. **Stage 4: Perpetual-Futures Adaptation:** Apply Adaptations A1 and F1. Use venue-specific data (e.g., Binance or Bybit perpetuals) including timestamped funding rates, maker/taker fees, and realistic slippage (e.g., 2-5 bps).
5. **Stage 5: Unified Evaluation:** Apply common capital assumptions, chronological walk-forward evaluation, and purging/embargo for overlapping labels to prevent lookahead bias.
6. **Stage 6: Independent Out-of-Sample:** Freeze all specifications before the final test period. Do not use the final test period to select parameters.
7. **Stage 7: Research Decision:** Classify each candidate based on the rubric in Section 7 of the prompt.

## 8. Main Criticisms and Unresolved Questions

- **Paper B Instrument Mismatch:** The most critical finding is that the highly cited "basis factor" return (329.21%, t=7.10) applies to *Current Quarter dated futures*, not perpetuals. The economic driver (expiry convergence) does not exist in perpetuals. 
- **Paper E Sample Size:** A 3-month backtest (Jan–Mar 2022) is statistically inadequate for high-frequency pairs trading. It likely captures a specific, transient volatility regime and is highly susceptible to overfitting and survivorship bias.
- **Paper D Execution Frictions:** While theoretically sound, the $50 DEX gas fee assumption and 5 bps slippage make the strategy highly sensitive to position size. Small accounts will be obliterated by fixed gas costs, violating the strategy's scalability.
- **Missing DEX Data:** Faithful replication of Paper D requires precise, timestamped DEX funding/oracle data, which may be fragmented or unavailable for the exact historical window, risking "REPLICATION INCOMPLETE."

## 9. Final Evidence-Based Classification

- **Paper A (Deprez & Frömmel):** REPRODUCED — EVIDENCE RETAINED (Adaptation A1 required for perpetuals).
- **Paper B (Chi et al.):** INFEASIBLE FOR TARGET MARKET (Relies on dated futures expiry mechanics).
- **Paper C (Hudson & Urquhart):** REPLICATION INCOMPLETE (Data is pre-2018; requires significant updating to be relevant, which borders on new strategy design).
- **Paper D (Pindza):** REPRODUCED — EVIDENCE RETAINED (With strict caveats on assumption sensitivity and capital requirements).
- **Paper E (Ko et al.):** INSUFFICIENT EVIDENCE (3-month sample is scientifically inadequate for out-of-sample validation).
- **Candidate F (Borgards):** RESEARCH PROMISING — READY FOR REPLICATION (Adaptation F1 required).

**Final Recommendation:** 
RESEARCH PROMISING — READY FOR REPLICATION

*Proceed with faithful replication of Paper A and Candidate F, strictly preserving their original parameters. Develop Adaptations A1 and F1 as separate, explicitly documented perpetual-futures hypotheses. Reject Paper B for perpetual application and defer Paper E due to insufficient sample length.*