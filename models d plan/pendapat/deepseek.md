# INDEPENDENT QUANTITATIVE RESEARCH REVIEW — MODEL D

## 1. Executive Summary

This review independently verifies the five papers identified for Model D and evaluates additional candidates. The central finding is that **only one paper — Deprez and Frömmel (2024) — provides a sufficiently complete, peer-reviewed, cost-aware, out-of-sample protocol that is realistically reconstructable without discretionary parameter invention**. It is, however, a **spot Bitcoin** study. No verified paper provides a directly transferable, net-of-cost, out-of-sample perpetual-futures strategy with a fully specified signal and execution protocol. The basis-factor paper (Chi et al., 2023) uses **dated futures on OKEx**, not perpetuals, and its monthly results are statistically nonsignificant. The CEX–DEX funding paper (Pindza, 2026) is a risk-decomposition framework, not a tradable alpha specification. The pairs-trading paper (Ko et al., 2024) relies on a **three-month sample** and reports implausibly high returns before realistic frictions, creating severe overfitting and multiple-testing concerns.

The defensible conclusion is **RESEARCH PROMISING — READY FOR REPLICATION**, but strictly **for the original spot-market specification of Deprez–Frömmel first**, with Hudson–Urquhart (2021) as a secondary candidate for non-BTC assets. Any perpetual-futures adaptation must receive a separate experiment ID and cannot be described as a faithful replication of any of the five papers.

---

## 2. Papers Verified and Papers Not Verified

| Paper | Verification Status | Critical Verification Notes |
|---|---|---|
| **Paper A — Deprez & Frömmel (2024)** | **VERIFIED** | Title, authors, journal, DOI, and headline methodology confirmed. Journal is *International Review of Economics & Finance*, not *International Review of Financial Analysis*. 75,360 rules across six classes, daily and intraday frequencies, transaction-cost and multiple-testing controls, out-of-sample portfolio evaluation. |
| **Paper B — Chi, Hao, Hu & Ran (2023)** | **VERIFIED WITH TITLE CORRECTION** | The paper is titled **“An empirical investigation on risk factors in cryptocurrency futures”**, not “Cryptocurrency Futures Basis.” The basis factor is one component. Instruments are **dated futures on OKEx**, not perpetuals. The reported 329.21% annualised abnormal return and t-statistic of 7.10 are confirmed, as is robustness to 5 bps transaction costs. Monthly results are nonsignificant. |
| **Paper C — Hudson & Urquhart (2021)** | **VERIFIED** | 14,919 rules across five technical-rule families. Confirmed out-of-sample result: **no predictability for Bitcoin**, but predictability remains in other cryptocurrencies. Breakeven transaction costs are reported as substantially higher than prevailing market costs. |
| **Paper D — Pindza (2026)** | **VERIFIED** | Open-access paper in *Digital Finance*, Volume 8, article 48 (2026). The paper is a **risk-decomposition and stress-testing framework** for CEX–DEX funding-rate arbitrage, not a simple signal-generating trading strategy. It uses Binance Futures funding rates for BTC, ETH, and SOL (Jan 2021–Dec 2024) plus **synthetic DEX funding rates**; dYdX v4 data is a limited robustness window. |
| **Paper E — Ko et al. (2024)** | **VERIFIED** | Six pair-selection methods; 30 Binance cryptocurrencies; **1 January 2022 to 31 March 2022** — a three-month sample. Distance method reports 208.12%–236.31% total return across 1-min, 5-min, and 60-min frequencies. No evidence of realistic transaction-cost or out-of-sample treatment is visible in the accessible sections. |

**Papers not fully verifiable at the required depth:** The exact six rule families in Paper A, the precise fee schedule and asset-by-asset out-of-sample results in Paper C, and the full execution/liquidation assumptions in Papers D and E require institutional access to the full texts and appendices. These gaps are treated as reproducibility risks, not as evidence in favour of the papers.

---

## 3. Literature Review

### 3.1 Paper A — Deprez and Frömmel (2024)

**Verified citation.** Niek Deprez and Michael Frömmel, “Are simple technical trading rules profitable in bitcoin markets?”, *International Review of Economics & Finance*, 93(B), 2024, pp. 858–874. DOI: 10.1016/j.iref.2024.05.003.

**Methodology actually stated.** The study employs **75,360 simple technical trading rules divided over six commonly used trading-rule classes and both daily and intraday frequencies**. It selects the best-performing rules **after transaction costs** using a multiple-hypothesis procedure, then forms portfolios of selected rules and analyses their **out-of-sample** performance 【32†L11–L20】. Bitstamp fee assumptions are explicitly modelled: a uniform trading fee of at least **0.20% per trade before 2 March 2015 and 0.10% afterwards** 【17†L48–L49】.

**Reported result.** The paper finds that, especially on a risk–return basis, simple technical trading rules **can outperform a buy-and-hold strategy in the Bitcoin market out-of-sample** 【32†L19–L21】. This is a positive but conditional finding: it does not mean that all rules work, that the result survives every multiple-testing correction, or that the selected rules are stable across regimes.

**Critical limitations.** The exact six rule families are not enumerated in the accessible abstract or metadata. The paper is a **spot Bitcoin** study; it does not model funding payments, perpetual–spot basis, leverage, or liquidation. Its out-of-sample result is portfolio-level, not a single-rule specification, which matters for faithful replication: a replicator must reproduce the **selection and portfolio-formation procedure**, not choose one rule that “looks best.”

### 3.2 Paper B — Chi, Hao, Hu and Ran (2023)

**Verified citation and correction.** The actual title is “An empirical investigation on risk factors in cryptocurrency futures,” *Journal of Futures Markets*, 43(8), 2023, pp. 1161–1180. DOI: 10.1002/fut.22425.

**Data and instruments.** The sample consists of the **12 largest coins with futures contracts on OKEx**, from **November 2017 to March 2021** 【19†L46–L50】. The paper describes a cross-sectional asset-pricing test of **basis, momentum, and basis–momentum**, motivated by the commodity-futures literature. **No reference to perpetual futures appears in the accessible full-text sections.** The instruments are dated futures.

**Reported results.** A long–short portfolio based on basis with a **5-day look-back and 1-day holding period** earns an average abnormal return of **329.21% per year (t = 7.10)**. Momentum earns 88.96% (t = 1.71), and basis–momentum earns 235.75% (t = 5.20). The basis result survives **5 basis points of transaction costs** 【24†L19–L33】. Critically, **monthly factor returns are nonsignificant**, and the premium disappears when the holding period is extended to one week or one month 【24†L31–L33】.

**Relevance to Model D.** The paper is evidence that a basis signal has cross-sectional predictive content in **dated crypto futures**. It is not evidence that the same signal works in perpetual futures, where the basis is mechanically different and funding payments dominate the carry. The extremely short 1-day holding period also implies high turnover and sensitivity to execution frictions that are not fully modelled at the perpetual level.

### 3.3 Paper C — Hudson and Urquhart (2021)

**Verified citation.** Robert Hudson and Andrew Urquhart, “Technical trading and cryptocurrencies,” *Annals of Operations Research*, 297(1), 2021, pp. 191–220. DOI: 10.1007/s10479-019-03357-1.

**Methodology.** The paper tests **14,919 technical trading rules** from **five main classes**, using data from **two Bitcoin markets and three other popular cryptocurrencies**. It implements multiple-hypothesis procedures (FWER and FDR) and reports breakeven transaction costs 【10†L6–L20】.

**Reported results.** In-sample, technical rules show significant predictability and profitability in each rule class and each cryptocurrency. Breakeven transaction costs are substantially higher than typical cryptocurrency market costs. However, **there is no predictability for Bitcoin in the out-of-sample period, although predictability remains in the other cryptocurrency markets** 【10†L17–L20】.

**Critical interpretation.** This paper does **not** support the claim that “technical trading works in crypto” universally. It supports a narrower claim: technical rules have in-sample predictive power and may retain out-of-sample value in some non-BTC markets, but the BTC out-of-sample result is negative. Any replication must therefore preserve the distinction between BTC and non-BTC assets and must not pool them to manufacture a positive result.

### 3.4 Paper D — Pindza (2026)

**Verified citation.** Edson Pindza, “Centralized–decentralized exchange funding rate arbitrage as a basis trade: risk decomposition, stress testing, and portfolio construction under venue uncertainty,” *Digital Finance*, 8, article 48 (2026). DOI: 10.1007/s42521-026-00213-3.

**What the paper actually does.** It models CEX–DEX funding-rate arbitrage as a **basis trade** and develops a **five-component risk decomposition**, Brinson-style PnL attribution, a Cox survival model for liquidation risk, historical stress tests, CVaR portfolio optimisation with venue haircuts, and Monte Carlo tail-risk analysis 【27†L8–L23】. It uses **Binance Futures funding rates for BTC, ETH, and SOL from January 2021 to December 2024**, but DEX funding rates are **synthetic**, with dYdX v4 observed data covering only a limited overlapping window 【27†L15–L23】.

**Reported conclusion.** Net annualised returns are driven mainly by **funding carry**, but are **highly assumption-sensitive** and should **not be interpreted as frictionless arbitrage profits**. Funding and residual market risk account for the largest variance shares; leverage and volatility regime dominate liquidation hazard; counterparty-risk episodes are more damaging than pure directional crashes 【27†L24–L29】.

**Relevance to Model D.** This is a **risk and execution framework**, not a signal specification. It does not provide a reproducible entry/exit rule that could be faithfully replicated as a standalone Model D strategy. It is valuable as a stress-testing and position-sizing reference for a separate funding-carry experiment, but it is **not a primary replication candidate**.

### 3.5 Paper E — Ko et al. (2024)

**Verified citation.** Po-Chang Ko, Ping-Chen Lin, Hoang-Thu Do, Yuan-Heng Kuo, Linh My Mai, and You-Fu Huang, “Pairs trading in cryptocurrency markets: A comparative study of statistical methods,” *Investment Analysts Journal*, 53(2), 2024, pp. 102–119. DOI: 10.1080/10293523.2023.2268386.

**Methodology.** The paper compares six pair-selection methods: **Cointegration, Correlation, Distance, Fluctuation Behaviour, Hurst Exponent, and Stochastic Differential Residual**. It uses **intraday data from 30 cryptocurrencies on Binance from 1 January 2022 to 31 March 2022** — a **three-month sample** — at 1-minute, 5-minute, and 60-minute frequencies 【12†L34–L42】.

**Reported results.** The Distance method reports total returns of **208.12% (1-min), 236.31% (5-min), and 210.36% (60-min)**. At the 60-minute frequency, it reportedly exhibits low risk and performs well even when other techniques generate negative returns 【12†L42–L48】.

**Critical limitations.** A three-month sample spanning a single market regime is insufficient for credible out-of-sample inference in a high-turnover pairs strategy. The paper does not, in the accessible sections, report realistic transaction costs, funding, slippage, or a chronological out-of-sample protocol. The reported returns are almost certainly gross and are not credible net-of-cost expectations. This paper is **not suitable as a primary replication candidate**.

### 3.6 Additional verified candidates

| Candidate | Citation and status | Relevance and limitations |
|---|---|---|
| **Liu, Tsyvinski & Wu (2022)** | “Common Risk Factors in Cryptocurrency,” *Journal of Finance*, 77(2), 1133–1177. DOI: 10.1111/jofi.13119. **Peer-reviewed.** | Establishes market, size, and momentum as cross-sectional pricing factors for **spot** cryptocurrencies 【13†L6–L11】. It is an asset-pricing paper, not a complete trading strategy with execution rules. Useful for understanding the momentum factor but not directly replicable as a Model D signal without additional specification. |
| **“Anatomy of cryptocurrency perpetual futures returns”** | Accepted/in press, 9 April 2024; SSRN version 2026. **Working-paper/forthcoming status requires verification.** | Directly relevant: analyses **perpetual futures**, develops a cost-of-carry model, and evaluates 134–170 predictors including basis, momentum, liquidity, size, and volatility. Reports a basis–momentum weekly return of **1.88%** 【16†L5–L22】. This is the most promising perpetual-specific candidate, but it is not yet verified as a peer-reviewed publication with a complete, replicable signal specification. |
| **Han, Kang & Ryu — “Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market”** | Working paper (ACFR, AUT). **Not peer-reviewed.** | Reports that **every long–short portfolio with a holding period of less than a week yields a negative mean return**, a strong negative finding for short-horizon momentum 【14†L39–L43】. This is a useful caution against naive momentum replication, not a candidate. |
| **Fieberg, Liedtke, Metko & Zaremba — “Cryptocurrency Factor Momentum”** | Working paper. **Not peer-reviewed.** | Replicates 34 anomalies across 3,900+ coins (2014–2022) and studies factor momentum 【6†L39–L43】. It does not provide a single ready-to-replicate Model D strategy and is not a primary candidate. |

---

## 4. Candidate Comparison Matrix

The matrix below scores candidates on transparent qualitative criteria. **These scores are not statistically validated facts and must not be treated as precision estimates.** The purpose is to make inclusion, deferral, and rejection reasons explicit.

**Scoring scale:** High / Medium / Low / Negative, with a short justification.

| Criterion | **A — Deprez–Frömmel** | **B — Chi et al.** | **C — Hudson–Urquhart** | **D — Pindza** | **E — Ko et al.** | **Anatomy (perp)** |
|---|---|---|---|---|---|---|
| **Evidence strength** | High: peer-reviewed, large rule universe, costs, multiple testing, OOS | High for dated futures; Medium for target | High: peer-reviewed, large rule universe, costs, OOS | Medium–High: peer-reviewed, but framework not alpha | Low: three-month sample, no visible cost/OOS rigor | Medium pending peer-review verification |
| **Publication status** | Published, *IREF* 2024 | Published, *JFM* 2023 | Published, *AOR* 2021 | Published, *Digital Finance* 2026 | Published, *IAJ* 2024 | Accepted/in press 2024; SSRN 2026 |
| **Original market** | Spot Bitcoin, Bitstamp | Dated futures, OKEx | Spot (two BTC venues + three other cryptos) | CEX–DEX perpetual funding | Spot/intraday Binance | Perpetual futures |
| **Sample period** | Not fully accessible; includes daily and intraday | Nov 2017–Mar 2021 | Not fully accessible in abstract | Jan 2021–Dec 2024 | **Jan–Mar 2022 only** | Not fully accessible in snippet |
| **Out-of-sample protocol** | **Yes**, explicit OOS portfolio evaluation | Not clear from accessible text | **Yes**, OOS results reported | Stress tests and Monte Carlo; not a simple holdout | Not evident in accessible text | Unclear; working paper |
| **Reported net-of-cost performance** | Positive risk-adjusted OOS, costs modelled (Bitstamp 0.10%–0.20%) | 329.21% p.a., t=7.10, robust to 5 bps | Breakeven costs high; BTC OOS no predictability | Assumption-sensitive; not frictionless | Extremely high gross returns; costs not visible | Basis–momentum 1.88% weekly, cost treatment unclear |
| **Applicability to perpetual futures** | **Low–Medium**: spot rules require funding/basis adaptation | **Low**: dated futures, 1-day holding | **Low–Medium**: spot technical rules | **High in principle**: funding is central | **Medium**: pairs can be market-neutral | **High**: directly on perps |
| **Required data / infrastructure** | Daily and intraday BTC OHLC; fee schedule | OKEx dated futures prices, basis | Multi-venue crypto OHLC; rule engine | CEX and DEX funding, liquidation, venue data | Intraday Binance data; pair engine | Perp OHLC, funding, basis |
| **Turnover / holding period** | Daily and intraday; potentially high | **1-day** holding; very high turnover | Rule-dependent; daily likely | Funding-interval driven | 1-min/5-min/60-min; very high | Weekly basis–momentum |
| **Overfitting / selection-bias risk** | **Controlled** via FDR and OOS | Moderate; 1-day holding and extreme return require scrutiny | **Controlled** via multiple testing; BTC OOS negative | Low as a risk framework; not an alpha claim | **Very high**: three-month sample, six methods | Moderate; working paper |
| **Diversification vs. Models A and B** | Technical, spot-BTC; may overlap with defensive SMC | Basis is a carry/cross-sectional factor; distinct | Technical, multi-asset; partly overlaps | Carry/arbitrage; distinct from A and B | High-frequency stat-arb; distinct but infrastructure-heavy | Basis/carry; distinct |
| **Faithful-replication feasibility** | **High** for original spot strategy | **Medium**: dated futures data and 1-day turnover | **High** for original spot strategy | **Low**: synthetic DEX rates, complex risk model | **Low–Medium**: data available, but short sample | **Medium**, pending full specification |
| **Known negative findings** | Result is risk-adjusted, not universal; exact rule families inaccessible | Monthly nonsignificant; premium disappears beyond 1-day holding | **BTC OOS negative** | Returns highly assumption-sensitive; not arbitrage | No OOS; single regime | Not yet peer-reviewed |
| **Reason for inclusion / deferral / rejection** | **Primary replication candidate** | **Defer**: instrument mismatch; could be reproduced only in original dated-futures market | **Secondary replication candidate** for non-BTC assets | **Defer as risk framework**, not primary alpha | **Reject as primary**: sample too short | **Watch**: most relevant perp candidate, but unverified publication status |

---

## 5. Faithful Replication Plan

### 5.1 Primary candidate: Deprez–Frömmel (2024)

**Stage 1 — Full-text acquisition and rule taxonomy.** Obtain the published version and appendices. Extract the **six rule classes**, the exact parameter grids, the daily and intraday frequencies, the multiple-hypothesis procedure, the portfolio-formation rule, and the precise out-of-sample split. **Do not substitute a “reasonable” rule family** if the paper’s taxonomy cannot be recovered. If the taxonomy is inaccessible, the replication is **incomplete**.

**Stage 2 — Original-market reproduction.** Reconstruct the strategy on **Bitstamp spot Bitcoin** using the paper’s stated fee schedule (0.20% before 2 March 2015, 0.10% after) and the original data sources. Reproduce the **selection procedure**, not a single hand-picked rule. The output must include the number of rules surviving the multiple-testing filter, the portfolio composition over time, and the out-of-sample equity curve.

**Stage 3 — Reproducibility audit.** Compare reproduced gross and net returns, Sharpe ratios, drawdowns, and turnover with the published tables. Explain discrepancies in terms of data vendor, timestamp alignment, fee treatment, or selection procedure. **A reproduced result that differs from the published result is not a failure of the replication; it is a finding.**

**Stage 4 — Perpetual-futures adaptation (separate experiment ID).** Only after Stage 3. The adaptation must explicitly model:
- Funding payments at the venue’s actual interval (typically 8 hours on major CEXs).
- Perpetual–spot basis.
- Taker/maker fees and slippage.
- Leverage, margin, and liquidation rules.
- Minimum order sizes and contract specifications.

The adaptation is **not** a faithful replication and must be labelled as a new, unproven hypothesis.

### 5.2 Secondary candidate: Hudson–Urquhart (2021)

**Stage 1 — Full-text extraction.** Recover the **five rule families**, the exact 14,919-rule grid, the asset list, the in-sample/out-of-sample split, and the fee/breakeven-cost methodology. Confirm which non-BTC assets retain OOS predictability.

**Stage 2 — Original-market reproduction.** Reproduce the in-sample and OOS results separately for **BTC and each non-BTC asset**. Do not pool BTC with other assets to produce a positive result. If BTC OOS is negative, report it as negative.

**Stage 3 — Audit and classification.** If the non-BTC OOS result is reproducible, classify the strategy as **“REPRODUCED — EVIDENCE RETAINED” for non-BTC assets only**, and **“REPRODUCED — EVIDENCE NOT RETAINED” for BTC**. This is a nuanced but honest outcome.

### 5.3 What not to replicate as a primary candidate

- **Paper B (Chi et al.)** cannot be faithfully replicated as a perpetual strategy because the original instruments are **dated futures**. A separate “perpetual basis” experiment may be designed, but it must be justified by a different verified source (e.g., the Anatomy paper or the Digital Finance funding paper), not by mislabelling Chi et al.
- **Paper D (Pindza)** is a risk framework. A replication would mean reproducing the risk decomposition and stress-test outputs, not extracting a trading signal.
- **Paper E (Ko et al.)** should not be replicated as a primary strategy because its three-month sample cannot support credible out-of-sample inference.

---

## 6. Perpetual-Futures Adaptation Plan

No adaptation should be implemented until the original-market reproduction is complete and frozen. Each adaptation requires a **separate experiment ID**, a **documented justification**, and an **independent evaluation**.

| Original strategy | Required adaptations | Justification | Risk of misrepresentation |
|---|---|---|---|
| **Deprez–Frömmel spot BTC technical rules** | Funding P&L; perp basis; leverage/margin; liquidation; taker fees and slippage; position sizing under contract minimums | Perpetuals introduce a funding cash flow and liquidation risk absent from spot. The signal may survive, but the return distribution changes materially. | **High** if labelled a “faithful replication.” Must be labelled **“spot-original / perp-adapted.”** |
| **Hudson–Urquhart non-BTC technical rules** | Same as above, plus asset-specific funding and liquidity differences | Non-BTC perps have wider funding dispersion and thinner books. The original spot OOS result may not survive. | **High**; must not pool BTC and non-BTC in the adapted version without separate reporting. |
| **Chi et al. basis factor** | Replace dated-futures basis with **perpetual–spot basis**; replace 1-day holding with a funding-aware holding period; model funding explicitly | The original basis is a calendar-spread concept. Perpetual basis is mechanically different. | **Severe**; this is not an adaptation of the same signal. It is a **new hypothesis** that must be justified by perpetual-specific literature. |
| **Pindza CEX–DEX funding framework** | Replace synthetic DEX rates with observed venue rates; specify a tradable entry/exit rule if one is intended | The paper is a risk framework. A tradable adaptation would require a signal that the paper does not provide. | **Severe**; do not claim the paper specifies a strategy. |
| **Anatomy perpetual basis–momentum** | Directly applicable in principle; requires verification of peer-review status and full signal specification | This is the only candidate that is **natively perpetual**. If verified, it should be treated as a **separate primary candidate**, not as an adaptation of a spot paper. | **Moderate**, pending publication verification. |

---

## 7. Backtesting and Validation Protocol

### 7.1 Common data and capital assumptions

- Use a **single chronological data window** for all candidates where possible. If data availability forces different windows, report the mismatch explicitly and do not compare Sharpe ratios naively.
- Use **venue-specific fee schedules**, funding histories, and contract specifications. Do not assume a universal fee.
- Model **slippage as a function of order size and volatility**, not as a constant. If the paper specifies a constant cost, report both the paper-consistent and the venue-realistic results.

### 7.2 Chronological walk-forward and purging

- Freeze strategy specifications **before** opening the final test period.
- Use **walk-forward evaluation** with a training window, a validation window, and a test window. The test window must never influence rule selection.
- For overlapping labels or multi-day holding periods, apply **purging and embargo** to prevent leakage.
- Report results across **chronological folds**, not only the full-sample aggregate.

### 7.3 Multiple-testing control

- Apply the **same multiple-testing procedure as the original paper** where one is specified. If the original paper uses FDR or FWER, reproduce it rather than substituting a different correction.
- Record **all attempted variants**, including those that failed. A research registry must include negative and abandoned experiments.

### 7.4 Performance reporting

At minimum report, for each candidate and each fold:
- Gross and net PnL.
- Net expectancy per trade.
- Profit factor.
- Maximum drawdown and drawdown duration.
- Sharpe and Sortino ratios, with a clear statement of the return frequency and risk-free assumption.
- Turnover, holding duration, and number of trades.
- Decomposition of fees, funding, spread, and slippage.
- Exposure and leverage.
- Performance by asset and by volatility regime.
- Benchmark-relative performance (buy-and-hold, equal-weight crypto portfolio, or the paper’s original benchmark).
- Confidence intervals where the trade count permits; if the trade count is too low for meaningful inference, say so rather than reporting a Sharpe ratio as if it were precise.

### 7.5 Sample-size caution

Do not invent a universal minimum trade count. The appropriate sample size depends on the strategy’s turnover, the signal-to-noise ratio, and the number of independent bets. For a 1-day holding strategy, a multi-year sample may contain hundreds of overlapping bets but far fewer independent bets. Report the **effective number of independent observations**, not merely the raw trade count.

---

## 8. Main Criticisms and Unresolved Questions

**Criticism 1 — Instrument mismatch is pervasive.** Four of the five identified papers are not perpetual-futures studies. Paper A and Paper C are spot studies; Paper B is dated futures; Paper E is spot intraday. Only Paper D is explicitly about perpetual funding, and it is a risk framework. The premise that these papers supply a ready-made perpetual-futures strategy is therefore **not supported**.

**Criticism 2 — Paper B’s headline result is fragile.** The 329.21% annualised basis return is based on a **1-day holding period**, and the premium is nonsignificant at monthly horizons. This is not a stable, low-turnover carry strategy; it is a very short-horizon cross-sectional signal whose net profitability at perpetual-futures execution costs is unknown.

**Criticism 3 — Paper C’s BTC out-of-sample result is negative.** The paper is often cited as evidence that technical trading works in crypto, but the BTC out-of-sample result does not support that reading. Any proposal that uses Paper C to justify a BTC technical strategy is contradicted by the paper’s own OOS finding.

**Criticism 4 — Paper E’s sample is too short.** A three-month sample in a single regime cannot establish a reproducible edge, especially at 1-minute and 5-minute frequencies where microstructure noise, fees, and queue position dominate. The reported 200%+ returns are not credible net-of-cost expectations.

**Criticism 5 — Paper D’s DEX data are synthetic.** The paper is transparent about this limitation, but it means the claimed risk decomposition is partly a scenario analysis rather than an empirical out-of-sample validation on observed DEX data. A replication using only synthetic DEX rates would not establish real-world profitability.

**Criticism 6 — The exact rule families and parameter grids are not fully accessible in the public metadata for Papers A and C.** A faithful replication cannot be completed until the full texts and appendices are obtained. If the taxonomy cannot be recovered, the replication must be classified as **INCOMPLETE**, not as a negative result.

**Unresolved questions:**
1. What are the exact six rule classes and parameter grids in Deprez–Frömmel?
2. What are the exact five rule classes, asset list, and fee assumptions in Hudson–Urquhart?
3. Does the Anatomy perpetual-futures paper have a peer-reviewed publication with a complete signal specification, or is it still a working paper?
4. Can the Chi et al. basis factor be reconstructed on perpetual futures without changing the signal’s economic meaning?
5. What is the realistic all-in cost (fees + spread + slippage + funding) for the high-turnover strategies on the intended venue?
6. Are the user’s intended perpetual contracts linear USDT-margined or inverse coin-margined? This changes position sizing, liquidation, and funding mechanics.
7. What is the minimum order size and maximum leverage on the target venue, and do they bind for the strategy’s position sizes?

---

## 9. Final Evidence-Based Classification

| Candidate | Classification | Reasoning |
|---|---|---|
| **A — Deprez–Frömmel (2024)** | **RESEARCH PROMISING — READY FOR REPLICATION** (original spot BTC first) | Peer-reviewed, large rule universe, transaction costs, multiple-testing control, explicit OOS. The strongest reconstructable methodology. Not yet a perpetual strategy. |
| **C — Hudson–Urquhart (2021)** | **REPLICATION INCOMPLETE**; secondary candidate for non-BTC assets | Peer-reviewed and methodologically strong, but BTC OOS is negative and exact rule taxonomy requires full-text recovery. |
| **B — Chi et al. (2023)** | **INFEASIBLE FOR TARGET MARKET AS A FAITHFUL REPLICATION** | Dated futures, not perpetuals. The basis signal is economically different in perps. A separate perpetual-basis experiment is possible but must not be labelled a replication of this paper. |
| **D — Pindza (2026)** | **INSUFFICIENT EVIDENCE AS A TRADABLE ALPHA**; valuable as a risk framework | The paper does not specify a complete entry/exit rule; DEX data are synthetic; returns are assumption-sensitive. |
| **E — Ko et al. (2024)** | **INSUFFICIENT EVIDENCE** | Three-month sample, no visible OOS or realistic cost treatment, extreme gross returns. Not suitable for capital allocation or primary replication. |
| **Anatomy of perpetual futures returns** | **PROMISING BUT UNVERIFIED** | Natively perpetual, basis–momentum signal, but publication status and full specification require verification before inclusion. |

### Final recommendation

**RESEARCH PROMISING — READY FOR REPLICATION**, conditional on the following sequence:

1. **Complete the full-text extraction** for Deprez–Frömmel and Hudson–Urquhart, including rule taxonomies and parameter grids. If the taxonomy cannot be recovered, the replication is **INCOMPLETE** and no strategy should be implemented.
2. **Reproduce Deprez–Frömmel on spot Bitcoin first**, with the original fee schedule and out-of-sample protocol. Do not open the perpetual-futures test period until this is complete.
3. **Classify the spot reproduction honestly**: if the net-of-cost OOS result is negative, report it as negative. A negative reproduction is a valid research outcome and does not authorize discretionary repair.
4. **Only then design a separate perpetual-futures adaptation**, with a new experiment ID, explicit funding/liquidation modelling, and a clear label that it is **not** a faithful replication.
5. **Do not implement Model D for live trading** on the basis of this review. The evidence supports a research programme, not a production deployment.

**No defensible edge has been identified that is directly transferable to perpetual futures without a separate, documented adaptation and out-of-sample validation.** The correct next step is replication, not implementation.