本审计确认此前报告的核心方向大体成立，但需做两处实质性更正：**第一，上一轮将Hudson–Urquhart的文献发现标签为“REPRODUCED”不成立**，因为系统尚未进行任何独立复现，应改为“作者报告的结果，系统未复现”。**第二，“Anatomy”并非无依据的预印本——爱丁堡大学研究门户显示其已于2024年4月9日被 *Journal of Futures Markets* 接收/在印**，但SSRN版本晚于接收时间且全文规格仍不完整，因此可升格为“高优先级研究候选”，仍不能称为可直接忠实复现的完整规格。最关键的判断不变：**当前没有任何候选达到“可直接选定Model D策略”的标准，只达到“选定研究候选并获取全文”的标准。**

---

## 1. 自我事实审计表

| 原报告主张 | 审计分类 | 支持证据与具体出处 | 需更正 / 仍未知 |
|---|---|---|---|
| **Deprez & Frömmel (2024)**，*IREF* 93(B), 858–874, DOI 10.1016/j.iref.2024.05.003 | **VERIFIED FROM ABSTRACT OR METADATA ONLY** | RePEc条目确认卷93、期PB、页码858–874、DOI | 全文正文与附录规则分类未获取；“六类规则”的精确网格仍未知 |
| Deprez–Frömmel使用75,360条规则、成本后筛选、多重假设程序、样本外组合评估 | **VERIFIED FROM ABSTRACT OR METADATA ONLY** | 摘要级描述明确包含“select the best performing rules after transaction costs using a multiple hypothesis procedure”和“out-of-sample performance” | 具体多重检验方法（FDR/FWER）、训练/验证/测试切分日期未验证 |
| Bitstamp费率：2015-03-02前至少0.20%，之后0.10% | **VERIFIED FROM FULL TEXT (SCRIBBED EXCERPT)** | 明确引用“Bitstamp charges a uniform trading fee of minimum 0,20% per trade before March 2nd 2015 and 0,10% afterwards” | 该摘录来自Scribd转载，非出版社最终排版；但作为成本假设的证据等级高于摘要 |
| **Hudson & Urquhart (2021)**，*Annals of Operations Research* 297(1–2), 191–220, DOI 10.1007/s10479-019-03357-1 | **VERIFIED FROM ABSTRACT OR METADATA ONLY** | 多家书目源一致确认卷期页码与DOI | 五类规则的精确参数网格、资产列表、费用假设未从全文验证 |
| Hudson–Urquhart：比特币样本外无可预测性，其他加密资产仍具可预测性 | **VERIFIED FROM ABSTRACT OR METADATA ONLY；原报告标签错误** | 多个摘要来源明确陈述“no predictability for Bitcoin in the out-of-sample period, although predictability remains in other cryptocurrency markets” | **原报告将“作者报告的结果”与“系统复现的结果”混同；需显式纠正** |
| **Chi, Hao, Hu & Ran (2023)** 标题应为“An empirical investigation on risk factors in cryptocurrency futures”，*JFM* 43(8), 1161–1180 | **VERIFIED FROM SECONDARY SOURCE / 书目记录** | Cambridge repository与RePEc引用确认标题、期刊、2017–2021年样本、basis/momentum/basis–momentum框架 | 原文中“basis factor”的精确构建公式、1日持有期的实现细节未从全文验证；**原报告关于“标题不是‘Cryptocurrency Futures Basis’”的更正成立** |
| Chi et al. 使用OKEx **dated futures**，非永续 | **VERIFIED FROM SECONDARY SOURCE（基于原文摘要与数据描述）** | RePEc/Cambridge摘要描述的是“futures”的横截面定价，未出现“perpetual”；这与加密期货文献中dated futures的标准用法一致 | 若原文数据附录明确列出合约类型，应以原文为准；当前证据等级不足以将“非永续”称为全文验证事实 |
| **Pindza (2026)**，*Digital Finance* 8, article 48, DOI 10.1007/s42521-026-00213-3 | **VERIFIED FROM ABSTRACT OR METADATA ONLY** | Springer页面与EconPapers均确认2026年、第8卷、文章号48；摘要明确其为**风险框架**，使用Binance Futures BTC/ETH/SOL 2021-01至2024-12，DEX费率为**synthetic** | “非可交易alpha”的判断是基于其摘要自述的框架性质，而非全文信号规格；若其正文包含可执行规则，需以全文修正 |
| **Ko et al. (2024)**，*Investment Analysts Journal* 53(2), 102–119, DOI 10.1080/10293523.2023.2268386 | **VERIFIED FROM ABSTRACT OR METADATA ONLY** | 多个书目源确认卷期页码与DOI | 六种方法的精确阈值、回测规则、成本模型未从全文验证 |
| Ko et al. 样本为2022年1月1日至3月31日，30种Binance加密货币 | **VERIFIED FROM ABSTRACT** | Mendeley/EBSCO摘要明确“three-month period from 1 January 2022 to 31 March 2022” | “无可见交易成本处理”是基于摘要未提及；**不能断言原文未建模成本**，只能说“摘要未披露” |
| **原报告“REPRODUCED — EVIDENCE RETAINED / NOT RETAINED”标签** | **CONTRADICTED BY RESEARCH STATUS** | 系统从未进行任何独立复现；用户明确声明“No strategy has been implemented and independently reproduced by our research system yet” | **必须全部改为作者报告状态，不得使用“REPRODUCED”** |

---

## 2. 复现状态纠正

严格区分三种状态：

| 状态 | 含义 | 当前各候选所处状态 |
|---|---|---|
| **作者报告的结果** | 原论文发表的结果 | Deprez–Frömmel、Hudson–Urquhart、Chi et al.、Pindza、Ko et al. 的全部结果均仅处于此状态 |
| **本评审独立复现的结果** | 本评审亲自从原始数据重跑并获得的结果 | **无。本评审未进行任何数据级复现。** |
| **贵方研究系统实际复现的结果** | 贵方系统独立重跑并验证的结果 | **无。** |

因此，上一轮报告中任何“REPRODUCED — EVIDENCE RETAINED”或“REPRODUCED — EVIDENCE NOT RETAINED”的表述**均为分类错误**，应统一替换为：

- **“作者报告：正面/负面/混合结果；系统未复现”**
- 只有在贵方系统实际完成数据级复现后，才可使用“REPRODUCED”标签。

---

## 3. 新增候选审计

### 3.1 Borgards (2021) — Dynamic Time Series Momentum of Cryptocurrencies

| 审计项 | 结果 |
|---|---|
| **已验证引用** | Oliver Borgards, “Dynamic time series momentum of cryptocurrencies,” *North American Journal of Economics and Finance*, 57 (2021), 101428. DOI: 10.1016/j.najef.2021.101428 |
| **发表状态** | **已发表，同行评审期刊**。证据等级：书目记录 + 摘要。 |
| **原始工具** | 二十种加密货币与美国股票市场（S&P 500）对比；**现货**，非永续、非dated futures。 |
| **样本期** | 二次来源称 **2014–2019**；该日期来自第三方描述，**非出版社元数据或全文验证**。 |
| **信号规格是否完整** | **不完整。** 摘要称“dynamic modeling approach to define and test momentum periods that follow a formation period for interday and various intraday price levels”，使用**移动平均平滑滤波算法**，动量周期从**5分钟到数月**；但**形成期长度、持有期、阈值、组合权重、再平衡规则均未在可及摘要中给出**。 |
| **成本模型** | 摘要未披露交易成本、费用或样本外成本调整。**未知。** |
| **样本外验证** | 摘要提及“策略优于买入持有”，但**未说明是否采用时间序列样本外切分或滚动验证**。 |
| **忠实复现可行性** | **低–中。** 缺乏形成期/持有期/成本规格，无法在不引入新参数的情况下忠实重建。需获取全文附录后才可能升级。 |
| **与永续的相关性** | **低。** 现货跨资产时间序列动量，不涉及资金费率、基差或清算。 |
| **建议动作** | **OBTAIN FULL TEXT**，暂不升格为主要候选。 |

### 3.2 “Anatomy of Cryptocurrency Perpetual Futures Returns”

| 审计项 | 结果 |
|---|---|
| **已验证引用** | Yi Cao, Pengfei Luo, Yuhan Cheng, Yizhe Dong, “Anatomy of Cryptocurrency Perpetual Futures Returns.” SSRN: 6365329 / DOI 10.2139/ssrn.6365329。爱丁堡大学研究门户显示 **Accepted/In press — 9 Apr 2024，*Journal of Futures Markets***。 |
| **发表状态** | **已被同行评审期刊接收/在印**，但SSRN版本（2026年3月7日发布）晚于接收时间，且与接收版本的关系未说明。**不能将SSRN版本直接等同于最终期刊版本。** |
| **原始工具** | **永续期货**（这是五篇原有论文中唯一直接以永续为研究对象的候选）。 |
| **样本期** | 可及摘要**未给出明确起止日期**。未知。 |
| **信号规格是否完整** | **部分可及，但不完整。** 摘要明确了：cost-of-carry模型、log-linear近似、**170个收益预测因子**（basis、momentum、liquidity、size、volatility），以及**63个在5%水平上显著的总收益排序策略**。但**单个因子的具体排序方法、组合构建规则、持有期、再平衡频率、做多/做空腿定义均未在摘要中给出**。 |
| **成本模型** | 摘要提及总收益包含“price movement plus funding fee yields”，但**未说明是否扣除交易手续费、滑点或做市/吃单费用**。 |
| **样本外验证** | 摘要**未说明**是否有chronological out-of-sample或walk-forward设计；“63个显著策略”是样本内显著性还是经过多重检验后的样本外结果，**未知**。 |
| **忠实复现可行性** | **中。** 因子类别和统计显著性框架清晰，但完整信号规则不在公开摘要中。需获取**接收版全文与在线附录**后才能判断是否可在不调参的情况下复现。 |
| **与永续的相关性** | **最高。** 直接研究永续收益的横截面驱动因子，且明确纳入funding fee yields。 |
| **建议动作** | **优先获取 *Journal of Futures Markets* 接收版全文**；在获得完整信号规格前，分类为 **INCOMPLETE — HIGH PRIORITY**，不列为可复现候选。 |

---

## 4. 可辩护决策表

| 候选 | 已验证引用 | 实际可用证据 | 缺失信息 | 忠实复现可行性 | 永续相关性 | 建议动作 | 精确理由 |
|---|---|---|---|---|---|---|---|
| **Deprez–Frömmel (2024)** | *IREF* 93(B), 858–874, DOI 10.1016/j.iref.2024.05.003 | 75,360条规则、成本后多重检验筛选、样本外组合评估、Bitstamp费用 | 六类规则精确网格、多重检验具体方法、OOS切分日期、完整表格 | **高**（现货层面） | **低–中**（需独立永续适配） | **REPLICATE ORIGINAL（现货优先）** | 五篇原有论文中方法最完整、成本与样本外处理最透明；但仍需全文附录才能完成忠实重建 |
| **Hudson–Urquhart (2021)** | *AOR* 297(1–2), 191–220, DOI 10.1007/s10479-019-03357-1 | 14,919条规则、五类规则、比特币OOS无可预测性、非BTC OOS仍可预测 | 五类规则精确参数、资产列表、费用/盈亏平衡成本细节 | **中**（需全文附录） | **低–中** | **OBTAIN FULL TEXT**；作为次级候选，且必须分资产报告 | 方法强，但比特币OOS结果为负，不能合并报告为正面证据 |
| **Chi et al. (2023)** | *JFM* 43(8), 1161–1180, DOI 10.1002/fut.22425 | 2017–2021年数据、basis/momentum/basis–momentum因子、5日形成/1日持有、5 bp成本稳健性 | 精确basis公式、dated futures合约细节、月度不显著的具体表 | **低–中**（仅限dated futures原市场） | **低**（工具不匹配） | **DEFER** | 不能作为永续忠实复现；可作为“基差信号在dated futures中存在”的背景证据 |
| **Pindza (2026)** | *Digital Finance* 8, article 48, DOI 10.1007/s42521-026-00213-3 | 风险分解框架、Binance 2021–2024 funding、synthetic DEX rates、Cox liquidation模型 | 可执行入场/出场规则（若有）、观测DEX数据的有限窗口 | **低**（作为alpha）；**中**（作为风险框架） | **高**（funding是核心） | **DEFER**（不作主要alpha候选） | 摘要把自身定位为风险与仓位管理框架，而非信号规格 |
| **Ko et al. (2024)** | *IAJ* 53(2), 102–119, DOI 10.1080/10293523.2023.2268386 | 六种配对方法、30种Binance资产、2022-01-01至2022-03-31 | 成本模型、OOS设计、精确阈值、抽样偏差处理 | **低** | **中**（市场中性但非永续特定） | **REJECT AS PRIMARY CANDIDATE** | 三个月单 regime 样本无法支撑可信OOS；摘要未披露成本处理，不能把极高总收益当作净收益证据 |
| **Borgards (2021)** | *NAJEF* 57, 101428, DOI 10.1016/j.najef.2021.101428 | 20种加密资产、现货、动态动量框架、MA平滑滤波、5分钟至数月周期 | 形成期/持有期/阈值/成本/OOS切分 | **低–中** | **低** | **OBTAIN FULL TEXT** | 有同行评审，但可及摘要不足以重建信号；现货动量与永续执行的距离较远 |
| **Anatomy (Cao et al.)** | *JFM* accepted/in press 2024-04-09；SSRN 6365329 | 永续、170因子、63个显著总收益策略、cost-of-carry + funding yield框架 | 精确排序/组合规则、持有期、成本扣除方式、OOS设计、最终接收版全文 | **中**（待全文） | **最高** | **OBTAIN FULL TEXT — HIGH PRIORITY** | 目前**最直接相关**的永续候选，但**公开摘要不足以忠实复现**；在获得接收版全文前不得称为可复现策略 |

---

## 5. 最终回答

**哪个候选具有最强可验证方法论，为什么？**
**Deprez–Frömmel (2024)** 仍是方法论最可辩护的候选。它是五篇原有论文中唯一在可及范围内同时明确具备以下要素的现货研究：大规则全集（75,360条）、成本后多重假设筛选、组合层面的样本外评估，以及可验证的Bitstamp费率假设。但“最强”不等于“完整”——六类规则的具体参数网格和多重检验的精确方法仍需全文附录才能完成忠实重建。**Anatomy** 在永续相关性上更强，但其公开摘要不具备同等的方法透明度，因此不能仅凭摘要将其排在 Deprez–Frömmel 之前。

**哪个候选与永续期货最直接相关，仍缺什么证据？**
**“Anatomy of Cryptocurrency Perpetual Futures Returns”（Cao et al.）** 是唯一以永续期货为原生研究对象、且明确将 funding fee yields 纳入总收益的候选。仍缺失的关键证据包括：**（i）接收版全文与在线附录中的精确因子排序与组合构建规则；（ii）是否扣除手续费/滑点/吃单成本；（iii）是否采用时间序列样本外或walk-forward设计，以及63个显著策略是否经过多重检验控制；（iv）63个显著策略中哪些在扣费后仍显著。** 在这些信息获得之前，它只能被列为**高优先级研究候选**，不能被描述为“可复现策略”。

**上一轮报告中最需要更正的主张是什么？**
最需要更正的是**将 Hudson–Urquhart 的文献发现标签为“REPRODUCED — EVIDENCE RETAINED / NOT RETAINED”**。该标签错误地将**作者报告的结果**呈现为**系统复现的结果**。正确表述应为：**“作者报告：比特币样本外无可预测性；非比特币资产样本外仍可预测。系统尚未进行任何独立数据级复现。”** 这一更正直接关系到Model D的研究诚信：在未实际复现之前，任何“REPRODUCED”标签都会误导后续实验优先级和资本配置决策。

**是否有足够证据现在选定Model D策略，还是只能选定研究候选？**
**只能选定研究候选，不能选定可部署或可称为“已复现”的Model D策略。** 最可辩护的研究序列是：

1. **获取 Deprez–Frömmel 与 Hudson–Urquhart 的出版社最终版全文与附录**，完成规则分类与参数网格提取。若无法提取，Deprez–Frömmel 的复现必须归类为 **REPLICATION INCOMPLETE**，而非自动降级为负面结果。
2. **优先获取 Anatomy 的 *Journal of Futures Markets* 接收版全文**，评估其是否具备不调参即可实现的信号规格。若具备，它应作为**独立的永续原生候选**，而不是任何现货或dated futures论文的“适配版本”。
3. **在任何数据级复现开始之前，冻结所有候选的规格**，并明确记录：作者报告的结果 ≠ 系统复现的结果。
4. **不将 Pindza (2026) 或 Ko et al. (2024) 选为主要复现候选**：前者是风险框架而非信号规格，后者是三个月单 regime 样本，均不足以支撑永续部署决策。

**最终分类：INSUFFICIENT EVIDENCE — MORE RESEARCH REQUIRED。** 证据足以启动 **Deprez–Frömmel 的现货忠实复现**和 **Anatomy 的全文获取**，但不足以选定Model D策略，更不允许任何形式的参数调优或实盘实施。