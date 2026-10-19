## FOLLOW-UP AUDIT — SIGNALGENERATOR MODEL D

### Task 1: Audit of Factual Claims

| Claim / Paper | Audit Classification | Supporting Source & Specific Evidence |
| :--- | :--- | :--- |
| **Paper A:** Deprez & Frömmel (2024). *Are simple technical trading rules profitable in bitcoin markets?* Int. Rev. Econ. Finance, 93, 858-874. DOI: 10.1016/j.iref.2024.05.003 | **VERIFIED FROM FULL TEXT / ABSTRACT** | ScienceDirect/SSRN abstracts confirm: 75,360 rules, 6 classes, daily/intraday, Bitstamp 2012–2022, False Discovery Rate (FDR) multiple-testing correction, out-of-sample portfolio outperformance. [[1]] |
| **Paper B:** Chi et al. (2023). *An empirical investigation on risk factors in cryptocurrency futures*. J. Futures Markets, 43(8), 1161-1180. DOI: 10.1002/fut.22425 | **VERIFIED FROM FULL TEXT / ABSTRACT** | Wiley abstract confirms: Title correction (not "Cryptocurrency Futures Basis"), basis factor formula, 2017–2021 sample, 329.21% gross annualized return, t-stat 7.10, 5 bps transaction cost, **Current Quarter (CQ) dated futures only**. [[2]] |
| **Paper C:** Hudson & Urquhart (2021). *Technical trading and cryptocurrencies*. Ann. Oper. Res., 297, 191-220. DOI: 10.1007/s10479-019-03357-1 | **VERIFIED FROM FULL TEXT / ABSTRACT** | Springer abstract confirms: 14,919 rules, 5 classes, spot data ending Dec 2017, breakeven transaction cost analysis (e.g., 50 bps for Bitcoin). [[3]] |
| **Paper D:** Pindza (2026). *Centralized–decentralized exchange funding rate arbitrage as a basis trade...* Digital Finance. DOI: 10.1007/s42521-026-00213-3 | **VERIFIED FROM ABSTRACT OR METADATA ONLY** | Springer abstract confirms: CEX-DEX funding arbitrage, Jan 2021–Dec 2024, 3x leverage, 5 bps slippage, $50 DEX gas fee, Cox proportional hazards model for liquidation. Full text access restricted. [[4]] |
| **Paper E:** Ko et al. (2024). *Pairs trading in cryptocurrency markets...* Invest. Anal. J., 53(2), 102-119. DOI: 10.1080/10293523.2023.2268386 | **VERIFIED FROM ABSTRACT OR METADATA ONLY** | Taylor & Francis abstract confirms: 6 statistical methods, 3-month sample (Jan 1–Mar 31, 2022), Binance data, Distance method outperforms at 1m, 5m, 60m frequencies. [[5]] |
| **Candidate F:** Borgards (2021). *Dynamic time series momentum of cryptocurrencies*. N. Am. J. Econ. Finance, 57(C). DOI: 10.1016/j.najef.2021.101428 | **VERIFIED FROM ABSTRACT OR METADATA ONLY** | ScienceDirect abstract confirms: 20 cryptocurrencies, dynamic modeling of momentum periods (interday/intraday), outperforms buy-hold with higher risk-adjusted returns. Full text behind paywall; exact parameter grids and cost models are unknown. [[6]] |
| **Candidate G:** Cao et al. (2026). *Anatomy of Cryptocurrency Perpetual Futures Returns*. SSRN. DOI: 10.2139/ssrn.6365329 | **VERIFIED FROM ABSTRACT OR METADATA ONLY** | SSRN metadata confirms: Preprint (not peer-reviewed), cost-of-carry model for perpetuals, 170 predictors (basis, momentum, liquidity, size, volatility), 63 statistically significant total returns (price + funding fee). Exact signal formulas missing. [[7]] |

---

### Task 2: Correction of Replication-Status Errors

**Formal Correction:** In my previous report, I applied the classification *"REPRODUCED — EVIDENCE RETAINED"* to Papers A, D, and F. This was a mischaracterization of the audit state. That label described the *internal validity of the original authors' published results*, not the actions of our research system. 

Per the non-negotiable research rules: **No strategy has been implemented, coded, or independently reproduced by our research system yet.** 

Therefore, the correct classification for *all* candidates at this stage is strictly **"LITERATURE VERIFIED — AWAITING REPLICATION"** (or DEFER/REJECT). Any claim implying that our system has already backtested or reproduced these strategies is false and is hereby retracted.

---

### Task 3: Audit of Additional Candidates

1. **Borgards (2021) - Dynamic Time Series Momentum:** 
   - *Status:* Peer-reviewed journal article. 
   - *Feasibility:* **INCOMPLETE.** While the abstract confirms a robust dynamic momentum approach outperforming buy-and-hold, the full text is behind a paywall. The exact parameter grids, lookback window formulas, and transaction cost assumptions are not publicly verifiable from the metadata alone. Faithful replication is currently blocked without full text access.
2. **Cao et al. (2026) - Anatomy of Cryptocurrency Perpetual Futures Returns:**
   - *Status:* SSRN Preprint (Not peer-reviewed). 
   - *Feasibility:* **UNVERIFIED / INCOMPLETE.** Although highly relevant to perpetuals (explicitly models funding fee yields and log-basis), it remains a preprint. The abstract mentions 170 predictors and a two-factor model, but the specific mathematical formulas for the "price-volume relevant factor" and the exact sorting methodology are absent. It cannot be recommended as a primary candidate until peer-reviewed and fully detailed.

---

### Task 4: Defensible Decision Table

| 1. Candidate & Verified Citation | 2. Evidence Actually Available | 3. Missing Information | 4. Faithful-Replication Feasibility | 5. Relevance to Perpetual Futures | 6. Recommended Next Action | 7. Exact Justification |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Paper A:** Deprez & Frömmel (2024). DOI: 10.1016/j.iref.2024.05.003 | Full abstract/metadata: 75,360 rules, 6 classes, FDR control, 2012–2022 Bitstamp data. | Exact parameter bounds for the 6 rule classes; precise FDR threshold values. | **HIGH** (Pending full text acquisition). | **MODERATE.** Requires explicit adaptation (Adaptation A1) to add funding rates and perpetual margin mechanics. | **OBTAIN FULL TEXT**, then REPLICATE ORIGINAL. | Strongest multiple-testing correction in crypto TA literature. Transparent OOS design. |
| **Paper B:** Chi et al. (2023). DOI: 10.1002/fut.22425 | Full abstract/metadata: Basis factor, 2017–2021, 5 bps cost, t=7.10. | N/A (Abstract is highly detailed). | **ZERO.** | **NONE.** Explicitly uses Current Quarter (CQ) *dated* futures, which rely on expiry convergence. | **REJECT AS PRIMARY CANDIDATE.** | Adapting a dated-futures expiry strategy to perpetuals fundamentally alters the economic mechanism, violating the anti-tuning policy. |
| **Paper D:** Pindza (2026). DOI: 10.1007/s42521-026-00213-3 | Abstract: CEX-DEX arb, 2021–2024, 3x leverage, 5bps slippage, $50 gas, Cox model. | Full text (restricted access); exact DEX oracle lag modeling. | **MODERATE.** | **HIGH.** Native to perpetual funding rate mechanics. | **OBTAIN FULL TEXT**, then REPLICATE ORIGINAL. | Only peer-reviewed paper explicitly modeling perpetual funding arbitrage with stress testing, though highly assumption-sensitive. |
| **Paper E:** Ko et al. (2024). DOI: 10.1080/10293523.2023.2268386 | Abstract: 6 methods, Jan–Mar 2022, Binance, Distance method best. | N/A. | **HIGH** (but pointless). | **MODERATE.** | **DEFER.** | A 3-month sample period is scientifically inadequate for out-of-sample validation and is highly vulnerable to survivorship bias and regime overfitting. |
| **Candidate F:** Borgards (2021). DOI: 10.1016/j.najef.2021.101428 | Abstract: 20 cryptos, dynamic momentum periods, outperforms B&H. | Full text (paywalled); exact dynamic lookback formulas and cost models. | **LOW** (until full text is acquired). | **MODERATE.** Requires funding rate adaptation. | **OBTAIN FULL TEXT.** | Promising peer-reviewed alternative to short-sample mean-reversion, but exact replication is currently blocked. |
| **Candidate G:** Cao et al. (2026). SSRN: 6365329 | Abstract: Perpetual cost-of-carry, 170 predictors, 63 significant returns. | Full paper details; peer-review status; exact factor formulas. | **LOW.** | **HIGH.** | **DEFER.** | It is an unreviewed preprint. Lacks the rigorous validation required for Model D's primary candidate selection. |

---

### Final Audit Answers

**1. Which candidate has the strongest verifiable methodology, and why?**  
**Paper A (Deprez & Frömmel, 2024)** has the strongest verifiable methodology. It tests a massive parameter space (75,360 rules), applies rigorous False Discovery Rate (FDR) multiple-testing corrections to prevent data mining, and explicitly validates out-of-sample portfolio performance. Its methodology is transparent and designed specifically to avoid the overfitting that plagues most technical trading literature.

**2. Which candidate is most directly relevant to perpetual futures, and what evidence is still missing?**  
**Paper D (Pindza, 2026)** is the most directly relevant *peer-reviewed* candidate, as it explicitly models CEX-DEX perpetual funding rate arbitrage, including leverage, slippage, gas fees, and liquidation hazard (Cox model). However, **full text access is currently restricted**, meaning the exact mathematical formulation of the "synthetic DEX funding rates" and the precise CVaR optimization constraints are missing and must be obtained before replication can begin.

**3. What is the most important claim in your previous report that requires correction?**  
The most critical correction is the retraction of the label *"REPRODUCED — EVIDENCE RETAINED"*. I must explicitly clarify that **zero independent replication has been performed by our research system**. The previous report evaluated the *internal validity of the published papers*, not the output of our own backtesting engine. All candidates remain in the "Literature Verified" stage only.

**4. Is there sufficient evidence to select a Model D strategy now, or only to select a research candidate?**  
There is **only sufficient evidence to select a research candidate** for Stage 2 (Original-Strategy Reproduction). There is insufficient evidence to deploy, code, or finalize any Model D strategy. The next mandatory step is to acquire the full texts of Paper A and Paper D to extract the exact, unambiguous mathematical formulas required for faithful, untuned replication. No strategy should be advanced to perpetual adaptation until its original spot/dated formulation is successfully reproduced.