I'll verify each of the five papers against its original source first, then search for additional candidates, and write the review once the evidence is in hand.

Evidence gathering is done; I'm now writing up the review.

# Independent Quantitative Research Review: SignalGenerator Model D

Review date: 19 Oct 2026. Four of the five papers are verified at original or near-original sources. Paper A is only partly verified because the publisher blocked automated access.

---

## 1. Executive summary

- **Two entries in the brief were wrong.** Paper B's published title is not "Cryptocurrency Futures Basis". Paper D's author and dates were missing. Details are in §2.
- **No paper supplies a ready-to-run perpetual-futures strategy.** Every strategy-defining paper uses spot prices, dated futures, or a very short sample. The faithful Model D candidates are therefore replications in the original market, and any perpetual version is a separate, unproven adaptation.
- **Paper C (Hudson & Urquhart) is the only candidate I could reconstruct almost completely.** It has full rule grids, an explicit out-of-sample test and multiple-testing control. Its evidence is mixed: there was no Bitcoin out-of-sample predictability, the out-of-sample window was six months, and the rule counts do not add up (see §3).
- **Paper B (Chi et al.) has the strongest headline numbers but a fragile base.** It covers 3.4 years, a rolling universe of 5–12 coins, dated quarterly contracts, and daily rebalancing. The profit disappears at monthly holding. The signal does not map onto perpetuals without a new hypothesis.
- **Paper E (pairs trading) should be treated as weak evidence.** Its sample is three months. The only peer-reviewed perpetual-futures pairs study I found (Pindza & Mba, 2026) reports negative net returns after costs and funding.
- **Paper D is a risk-framework paper, not a strategy to replicate.** Full text was not retrievable, so what I know of its methods comes from the abstract and metadata only.

**Conclusion: INSUFFICIENT EVIDENCE — MORE RESEARCH REQUIRED** (§9).

---

## 2. Papers verified and not verified

| Paper | Status | What I could access | Discrepancy with the brief |
|---|---|---|---|
| **A** Deprez & Frömmel, *Are Simple Technical Trading Rules Profitable in Bitcoin Markets?* | Partly verified | SSRN abstract (4401552), RePEc record, DOI confirmed. ScienceDirect full text blocked. | Citation is *Int. Rev. Econ. & Finance* 93(B): 858–874 (June 2024). Six rule classes and daily plus intraday frequencies are confirmed. **The six families themselves, the data period, the cost level, and the out-of-sample split are not verified.** |
| **B** Chi, Hao, Hu, Ran | Verified (working-paper full text plus published abstract) | Working paper "Factor Structure of Cryptocurrencies" with all tables, and the published abstract | **Published title is "An empirical investigation on risk factors in cryptocurrency futures"**, *J. Futures Markets* 43(8): 1161–1180 (2023), DOI 10.1002/fut.22425. My numbers come from the working paper, not the final version. |
| **C** Hudson & Urquhart | Verified (full open-access text with appendix) | Complete paper | Citation is *Ann. Oper. Res.* 297(1): 191–220 (2021). It is peer-reviewed, and it is not evidence that every rule works. |
| **D** CEX/DEX funding | Partly verified | Springer metadata, abstract, RePEc. Full text returned HTTP 429 twice. | Author is **Edson Pindza**. Title is "Centralized–decentralized exchange funding rate arbitrage as a basis trade: risk decomposition, stress testing, and portfolio construction under venue uncertainty", *Digital Finance* 8, 48 (2026). Received 15 Feb, accepted 3 Aug, published 19 Aug 2026. Peer-reviewed. The sample appears to cover three assets. **Fees, margin model, and results are unread.** |
| **E** Pairs trading | Partly verified (abstract only) | Publisher abstract | *Investment Analysts Journal* 53(2): 102–119 (2024). Six methods, 30 coins on Binance, **three months (1 Jan–31 Mar 2022)**, 11 criteria. **Cost treatment unread.** |

I could not confirm Paper B's "t = 7.10". The working paper reports t = 7.07 gross and 6.60 net for the daily basis long-short portfolio. A cross-table matrix gives 7.08. The 7.10 figure may come from the published version, which I could not read.

---

## 3. Literature review

### Paper A: Deprez & Frömmel (2024)

**Verified:**
- 75,360 rules across six classes at daily and intraday frequencies.
- Selection uses a multiple-hypothesis procedure after transaction costs.
- Selected rules are combined into portfolios and tested out of sample.
- The authors say rules can beat buy-and-hold out of sample, "especially risk-return wise".

**Not verified:** the rule families, parameter grids, sample dates, cost level, and split. The paper cannot be reconstructed without the full text. A secondary source describes the multiple-testing method as FDR, but I treat that as unconfirmed.

### Paper B: Chi et al. (2023)

**Design (working paper):**
- 12 coins on OKEx, from 13 Nov 2017 to 31 Mar 2021.
- Current-quarter dated futures only. The intro says 13 coins and the data section lists 12, an inconsistency.
- Basis is defined as (S − F)/F. The sort is a daily ranking on a 5-day look-back.
- The portfolio is equal-weight top, middle and bottom groups (3/3/3 when 9 or more coins are available, smaller groups earlier). The long-short position is $0.5 long and $0.5 short.

**Results:**
- The daily high-low basis portfolio earns 326.58% annualized gross and 304.03% net, with t = 7.07 gross and 6.60 net. Maximum drawdown is about 56–57%.
- My arithmetic: a 0.89% mean daily return × 365 ≈ 325%, so the annualization looks like simple scaling rather than compounding. This is my inference.
- Weekly basis returns stay significant (t ≈ 3.35). Monthly returns are not significant (t ≈ 0.40).
- Momentum high-low is not significant (t = 1.69 gross). Basis-momentum is subsumed by basis.
- The returns come mostly from the long leg: 262% annualized for the high-basis portfolio versus −64.5% for the low-basis portfolio.

**Gaps:** the cost rate used for the net column is not in the text I could retrieve. There is no out-of-sample or purged validation. The equal-weight BTC-futures benchmark returned about 101–107% annualized over the same period.

### Paper C: Hudson & Urquhart (2021)

**Verified design:**
- Daily spot data. BTC (CoinDesk from 18 Jul 2010, Bitstamp from 1 Dec 2012), LTC, XRP and ETH, all ending 31 Dec 2017.
- Five rule classes: moving average, filter, support-resistance, oscillator, channel breakout.
- Multiple-testing procedures: Bonferroni, Holm, BH and BY, applied to stationary-bootstrap p-values.
- Out-of-sample test: only the best in-sample rule per market, over January–June 2018.

**Results:**
- After correction, 20–34% of rules survive Bonferroni (CoinDesk 33.61%, Bitstamp 27.11%, LTC 23.26%, XRP 20.35%, ETH 23.37%).
- Out of sample, BTC was negative on both price series. LTC (+7.75% annualized), XRP (+5.46%) and ETH (+6.31%) were positive.
- Costs: the main results assume zero cost. The paper reports break-even costs instead, around 55–66 bp for the best rule classes in CoinDesk. Only 0–36% of rules exceed 50 bp, and almost none of the support-resistance rules do.
- Only 6–16% of rules beat buy-and-hold on raw return. More beat it on Sortino and Calmar, mainly through drawdown avoidance.

**Reproducibility defect (my arithmetic):** the appendix total of 14,919 rules requires the support-resistance count to be 630, but the text gives 945 (315 + 630). With 945 the total would be 15,234. The oscillator grid also lists "d" as 5 values but "#d = 4". The exact rule set therefore cannot be pinned down from the paper alone.

**Execution caveat:** the rules allow short signals on spot crypto. The paper itself finds the profit comes mainly from the buy signals, not the sell signals.

### Paper D: Pindza (2026)

Verified only from metadata: it treats CEX–DEX funding arbitrage as a basis trade and decomposes the risks. Its keywords include survival analysis, CVaR optimization, stress testing and liquidation risk. The full-text details I would need (cost model, margin model, sample window, reported returns) were not retrieved.

### Paper E: pairs trading (2024)

**Verified from the abstract:** six selection methods (cointegration, correlation, distance, fluctuation behavior, Hurst exponent, stochastic differential residual). 30 Binance coins in a three-month 2022 window. The Distance method returned 208.12%, 236.31% and 210.36% at 1-minute, 5-minute and 60-minute frequencies. A Student t-test is reported, with no conflict of interest declared.

**Flags:**
- A 200%+ return in three months is an extreme claim, and it comes from a single bear-market window.
- Cost treatment is unknown.
- Related evidence contradicts it. Fil & Kristoufek (IEEE Access 2020, 26 Binance coins) found underperformance versus classic benchmarks, and extreme sensitivity to costs. A Turku thesis (not peer-reviewed) found all hourly methods loss-making after costs.

### Additional verified candidates

- **Pindza & Mba (2026), *Quantitative Finance and Economics* 10(2): 378–404** is the most relevant perpetual-futures result. It uses 10 Binance USDT perpetuals (hourly, Jan 2021–Dec 2023), a 0.04% taker fee per side plus a 0.02% spread cost per leg, and 8-hour funding. The market-neutral copula variants lose 14–16% in total. The average gross return per trade is +0.15%, trading cost −0.12% and funding −0.18%, for a net −0.15%. Raising the entry threshold does not make it profitable. The Alpha Overlay variant roughly tracks buy-and-hold (+41.1% vs +46.1% in the main table). The authors note the paper lacks a simple-baseline comparison, and one appendix table is labeled "illustrative, replace with actual values", so I treat its numbers cautiously. The paper has other internal inconsistencies, such as a stated 603.2% Overlay total return in the text against 41.1% in Table 3.
- **Liu, Tsyvinski & Wu, "Common Risk Factors in Cryptocurrency", *J. Finance* (2022)**, DOI 10.1111/jofi.13119. Cross-section of about 1,700 coins from 2014 to 2018, with weekly sorts. Momentum, size and market capture the cross-section. Returns are gross, spot only, and span many illiquid coins.
- **Grobys et al. (2025), "Cryptocurrency momentum has (not) its moments", Financial Markets and Portfolio Management**, DOI 10.1007/s11408-025-00474-9. Large-cap momentum suffers severe crashes. Volatility-managed versions do not significantly change the tail-risk exponent.
- **Schmeling, Schrimpf & Todorov, "Crypto Carry", BIS Working Paper 1087 (2023)**, working paper only. Carry averages above 10% annually, with spikes beyond 40%. The authors attribute it to leveraged-demand pressure and limited arbitrage capital. A later survey describes the profitability as compressing sharply since 2024. I have not read that survey's primary data.
- **Ackerer, Hugonnier & Jermann, "Perpetual Futures Pricing", *Mathematical Finance* 36(3): 481–499 (2026)**, DOI 10.1111/mafi.70018. Pure theory on no-arbitrage perpetual pricing. It is a framework for the adaptation, not a strategy.
- **He, Manela, Ross & von Wachter, "Fundamentals of Perpetual Futures", arXiv:2212.06888 (v6, Aug 2024)**, working paper. It derives no-arbitrage prices and bounds for perpetuals, reports that deviations are larger than in traditional currency markets, and finds that an implied arbitrage strategy yields high Sharpe ratios. I did not verify its journal publication status.
- **Gerritsen, Bouri, Ramezanifar & Roubaud, "The profitability of technical trading rules in the Bitcoin market", *Finance Research Letters* 34 (2020), art. 101263.** Daily BTC from July 2010 to January 2019. Trading-range breakout beats buy-and-hold on a bootstrapped Sharpe ratio, mainly in strongly trending markets.

**Not independently verified:** Detzel et al. (cited by Hudson & Urquhart as an SSRN working paper), a ResearchGate preprint claiming funding-arbitrage returns up to 115.9% over six months, and the Eurekalert-reported hybrid pairs study. I have not relied on them.

---

## 4. Candidate comparison matrix

Scores are qualitative judgments. They are not statistical results.

| Candidate | Status | Market and sample | OOS evidence | Net-of-cost evidence | Perp. transfer | Replication feasibility | Main risks |
|---|---|---|---|---|---|---|---|
| **C** Hudson & Urquhart rules | Peer-reviewed | Spot daily, 5 markets, to Dec 2017 | 6 months, best rule per market, BTC negative | Zero-cost main results, break-even only | Low–moderate (spot signals) | **High**, apart from the 14,919 vs 15,234 count | Old sample, tiny OOS, short-selling assumption |
| **A** Deprez & Frömmel | Peer-reviewed | Bitcoin, daily plus intraday | Reported, protocol unverified | "Realistic costs" claimed, level unverified | Unknown | **Unknown** (full text not accessed) | Cannot audit |
| **B** Chi et al. basis | Peer-reviewed (published version unread) | OKEx quarterly futures, 12 coins, 2017–21 | None documented | Net column exists, rate unverified | **Low** (dated futures, daily rebalance) | Moderate | Short sample, 56% drawdown, long-leg concentration |
| **E** Pairs trading | Peer-reviewed (abstract only) | 30 Binance coins, 3 months | Not evident | Unknown | Moderate | Low (methods unread) | Three-month window, contradicted elsewhere |
| **D** Funding arbitrage (Pindza) | Peer-reviewed | Perps, 3 assets | Unknown | Unknown | High (perp-native) | Unknown (full text not read) | Execution, liquidation and venue risk |
| Pindza & Mba copula pairs | Peer-reviewed | 10 Binance perps, 2021–23 | Rolling formation windows | **Negative** | Direct | Moderate–high | Negative result (not a candidate to implement) |
| Liu–Tsyvinski–Wu factors | Peer-reviewed | ~1,700 coins, 2014–18 | Not applicable (factor study) | Gross only | Low (illiquid coins, spot) | Moderate | Data snooping, early-sample behavior |
| Crypto carry (BIS) | Working paper | OKX and CME, BTC/ETH | Not applicable | Not a strategy paper | Moderate (carry on perps) | Low | Compression since 2024 |

**Diversification versus Models A and B:** I do not know the exact rules of A and B, so I cannot assess overlap. C's trend rules and B's basis rule are both directional rather than SMC or liquidity-sweep logic, and the risk of overlap should be tested empirically.

**Ranking** (evidence and reproducibility only, not return):
1. Paper C as a faithful reproduction target.
2. Paper B as a reproduction target for the original dated-futures data only.
3. Paper A, deferred until the full text is read.
4. Paper D, deferred to the same condition.
5. Paper E, deferred or rejected as a primary candidate (reasons in §8).

---

## 5. Faithful replication plan

**Paper C (primary):**
- Rule set: the five classes and parameter grids exactly as the appendix gives them (MA 7,980, filter 963, oscillator 810, channel breakout 4,536, support-resistance 630 or 945, to be resolved).
- Data: CoinDesk and Bitstamp BTC, LTC, XRP and ETH, daily, to 31 Dec 2017. Hold out 1 Jan–30 Jun 2018 as the original out-of-sample window.
- Selection: stationary bootstrap (block length 0.1, 1,000 resamples), then Bonferroni, Holm, BH and BY. Reproduce the best-in-sample-rule selection.
- Check against the published results: the survivor percentages in Table 8 and the out-of-sample returns in Table 9.
- The unresolved ambiguity (630 vs 945) must be reported as a replication defect. Run both versions only as separately labeled variants; do not pick the one that performs better.

**Paper B:**
- Universe: the 12 coins in the paper's Table 1, current-quarter OKEx futures, 13 Nov 2017 to 31 Mar 2021.
- Signal: basis = (S − F)/F, 5-day look-back, daily ranking, high-minus-low with the group sizes in Table 2.
- Report all lookbacks (1, 3, 5, 7), all holding periods (daily, weekly, monthly), and gross and net.
- The cost rate is unknown to me. Obtain it from the published version before running.
- Use the published JFM version as the specification and treat the working paper only as a cross-check, since the two may differ.

**Paper A (conditional):** obtain the full text through institutional access or the authors. Do not proceed without the six rule families, the grids and the split. Mark replication INCOMPLETE until then.

**Paper D (conditional):** read the full text, then decide whether it is replicable. Likely mark as framework evidence only.

**Paper E (deprioritized):** obtain the full text and cost model first.

---

## 6. Perpetual-futures adaptation plan

Each item below is a **separate experiment ID**, not a replication.

| Adaptation | Reason | Support |
|---|---|---|
| Run Paper C's rules on perpetual-futures price series | Instrument mismatch (spot to perp) | No direct paper. New hypothesis. |
| Add funding, fees, spread and slippage | Perps pay funding every 8 hours on Binance. Pindza & Mba's decomposition shows funding as the largest per-trade drag. | Ackerer et al. (2026); Pindza & Mba (2026) |
| Replace dated-futures basis in Paper B's signal with a perp-index premium or funding rate | The dated-futures basis does not exist for perps. | He et al. (2022 working paper) supports the theory only. New, unproven hypothesis. |
| Constrain shorts, leverage and margin | Spot rules assumed frictionless shorting and no liquidation. | None specific to the papers. |

**Venue:** the brief does not name the target venue. I did not verify fees, funding intervals or minimum notionals from official exchange documentation. The search produced third-party mirrors, not official pages. Before any perpetual experiment, pull those parameters from the venue's own documentation and record the date.

---

## 7. Backtesting and validation protocol

- **Registry:** one experiment ID per variant. Log every variant run, including failures.
- **Freeze before testing:** rule definitions, universes, costs and the final test window are frozen and hashed before any final-window run.
- **Split:** the original paper's split for reproduction. Then a chronological walk-forward on newer data with an embargo at least as long as the longest holding period or lookback.
- **Multiple testing:** keep the paper's own procedure for reproduction. For the new data window, apply the same family of corrections across the full rule set, not just the winners.
- **Costs:** report gross, then net under a stated fee, spread and funding model, plus a sensitivity grid.
- **Metrics:** gross and net PnL, net expectancy per trade, profit factor, maximum drawdown, Sharpe and Sortino with serial-correlation caveats, turnover, holding time, cost and funding contribution, exposure, results by asset, by regime and by fold, benchmark-relative performance, and confidence intervals.
- **Sample size:** no universal minimum trade count. Judge adequacy from the confidence interval width on net expectancy and from the effective number of independent observations after overlap.
- **Out-of-sample window:** keep any final window untouched until all specifications are frozen.

---

## 8. Main criticisms and unresolved questions

1. **Data age.** Papers C and B use data ending in 2018 and 2021. The crypto market and its fee and funding structure have changed since. The BIS-related survey I found reports carry compression since 2024, but I have not seen that survey's primary data.
2. **Rule-count inconsistency in Paper C** (14,919 vs 15,234) means exact reconstruction is impossible without author contact.
3. **Paper C's out-of-sample test is thin.** One best rule per market, six months, a single bear window, and the one largest market (BTC) failed.
4. **Paper B's net numbers are unaudited.** The cost rate is not in the text I read, the sample is short, there is no out-of-sample validation, and the profit sits in the long leg. A daily-rebalanced basis sort on dated futures says little about perpetual funding behavior.
5. **Paper E's three-month window** is the weakest evidence in the set, and independent studies disagree.
6. **Paper D and Paper A are unread at full text.** I cannot judge their reproducibility.
7. **Survivorship and universe selection** are unaddressed in Papers B and C: B's rolling universe and C's choice of the largest markets by 2017.
8. **Multiple-testing dependence.** Hudson & Urquhart note that their corrections are conservative under rule correlation, which is real, but it also means the surviving-percentage figures are not a clean "true discovery" rate.
9. **Conflicts of interest:** Chi et al. thank a market-neutral crypto-derivatives fund for data support. This is not itself a defect, but it should be recorded.
10. **Missing datasets:** official venue fee and funding schedules; the published JFM text for B; the full texts for A, D and E.

---

## 9. Final evidence-based classification

| Candidate | Classification (provisional, pre-reproduction) |
|---|---|
| Paper C rules | **REPLICATION INCOMPLETE** until the 630-vs-945 ambiguity is resolved, then eligible for reproduction. Evidence is mixed. |
| Paper B basis | **INSUFFICIENT EVIDENCE** for the target market. Reproducible only in its original dated-futures setting. |
| Paper A | **REPLICATION INCOMPLETE** (full text not accessed) |
| Paper D | **INSUFFICIENT EVIDENCE** (full text not accessed) |
| Paper E | **INSUFFICIENT EVIDENCE** (three-month window, abstract only) |

These are provisional labels. None of the five has been reproduced yet. Per §7 of your brief, the REPRODUCED labels can only be assigned after Stage 2.

**INSUFFICIENT EVIDENCE — MORE RESEARCH REQUIRED**

Reason: the evidence supports further replication work on Papers C and B in their original markets, but no paper here establishes a perpetual-futures edge net of costs, and the one perpetual-futures study I could read in full (Pindza & Mba, 2026) is negative net of costs. The next concrete steps are to obtain the full texts of Papers A, B (published version), D and E, and to resolve Paper C's rule-count discrepancy by contacting the authors.

**Sources:**
- [Hudson & Urquhart, Technical trading and cryptocurrencies (full text)](https://hull-repository.worktribe.com/OutputFile/2639834)
- [Chi et al., Factor Structure of Cryptocurrencies (working paper)](https://acfr.aut.ac.nz/__data/assets/pdf_file/0010/686755/6b-Yeguang-Chi.pdf)
- [Chi et al. published abstract, J. Futures Markets](https://www.infona.pl/resource/bwmeta1.element.wiley-fut-v-43-i-8-fut22425)
- [RePEc record, Chi et al. 2023](https://ideas.repec.org/a/wly/jfutmk/v43y2023i8p1161-1180.html)
- [Deprez & Frömmel, SSRN abstract](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4401552)
- [RePEc record, Deprez & Frömmel 2024](https://ideas.repec.org/a/eee/reveco/v93y2024ipbp858-874.html)
- [Pindza, CEX–DEX funding arbitrage, Digital Finance](https://link.springer.com/article/10.1007/s42521-026-00213-3)
- [RePEc record, Pindza 2026](https://ideas.repec.org/a/spr/digfin/v8y2026i3d10.1007_s42521-026-00213-3.html)
- [Pairs trading in cryptocurrency markets, Investment Analysts Journal abstract](https://www.informahealthcare.com/doi/abs/10.1080/10293523.2023.2268386)
- [Pindza & Mba, adaptive copula pairs trading (full text)](https://www.aimspress.com/aimspress-data/qfe/2026/2/PDF/QFE-10-02-016.pdf)
- [Fil & Kristoufek, Pairs Trading in Cryptocurrency Markets, IEEE Access](https://ieeexplore.ieee.org/document/9200323)
- [Liu, Tsyvinski & Wu, Common Risk Factors in Cryptocurrency (SSRN)](https://papers.ssrn.com/abstract=3379131)
- [Grobys et al., Cryptocurrency momentum has (not) its moments](https://link.springer.com/article/10.1007/s11408-025-00474-9)
- [Schmeling, Schrimpf & Todorov, Crypto Carry (BIS WP 1087)](https://www.bis.org/publ/work1087.pdf)
- [Ackerer, Hugonnier & Jermann, Perpetual Futures Pricing (NBER)](https://www.nber.org/papers/w32936)
- [He, Manela, Ross & von Wachter, Fundamentals of Perpetual Futures (arXiv)](https://arxiv.org/abs/2212.06888v6)
- [Gerritsen et al., profitability of technical trading rules in the Bitcoin market](https://research-portal.uu.nl/en/publications/the-profitability-of-technical-trading-rules-in-the-bitcoin-marke/)