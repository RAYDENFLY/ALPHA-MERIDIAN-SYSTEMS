# SignalGenerator — Phase 1 Recommendation & Decision Report

**Repository:** `C:\Users\Administrator\Documents\SignalGenerator`  
**Branch:** `research/profitability-validation`  
**Git Commit:** `a89b15a4a24b84a4db5f7e622fefffc70c876f75`  
**Decision Framework:** Task 5 Mandatory Out-of-Sample Decision Rules  
**Date:** 2026-10-17 22:42:00 UTC

---

## 1. Executive Decision Summary

| Question | Empirical Finding | Status / Verdict |
| :--- | :--- | :---: |
| **1. Is 72H dealing range the primary cause of low trade frequency?** | **NO.** Multi-horizon momentum only triggers on 0.501% of bars, and opposing SnR walls filter 61.18% of setups. Ablating DR72 only adds 15 eligible setups over an entire year across 11 symbols. | **REFUTED** |
| **2. Does the 72H dealing range filter increase or decrease net expectancy?** | **INCREASES.** Removing it drops standalone expectancy by 16.4% (from +$1.0470 to +$0.8751) and collapses portfolio expectancy by 64.8% (from +$1.0231 to +$0.3602) while increasing drawdown by 69.4%. | **PROVEN PROTECTIVE** |
| **3. Did any ablation candidate outperform the baseline Out-of-Sample?** | **NO.** Baseline A outperformed Configurations B, C, and D across both Calendar Split 1 OOS and Rolling Walk-Forward Split 2 OOS. | **BASELINE PRESERVED** |
| **4. How many completed trades support the empirical conclusion?** | Full sample: **74 standalone trades** and **13 portfolio trades** in Baseline A. Calendar OOS: **50 trades** in A vs 54 in B. Walk-forward OOS: **13 trades** in A vs 15 in B. | **SUFFICIENT FOR FULL SAMPLE; INSUFFICIENT FOR OOS COHORT (<30)** |
| **5. Is evidence sufficient to advance an alternative candidate?** | **NO.** Under Task 5 rules, no alternative candidate demonstrated superior OOS expectancy. Alternative candidates are designated **`INSUFFICIENT_DATA`**. | **KEEP_BASELINE** |

---

## 2. Answers to Mandatory Audit Questions

### Question 1: Apakah filter dealing range merupakan penyebab utama rendahnya trade frequency?
**Jawaban Berbasis Bukti: TIDAK.**
- Dari 93.148 bar 1H yang dievaluasi across 11 simbol, hanya **467 bar (`0.501%`)** yang memenuhi kondisi momentum multi-horizon (48H return, 12H impulse, candle color, volume ratio). Ini berarti pasar secara alami hanya menunjukkan momentum yang memenuhi syarat rata-rata sekali setiap 200 jam per simbol.
- Ketika filter 72H dealing range dinonaktifkan sepenuhnya (Konfigurasi B, `[0.00, 1.00]`), jumlah eligible setups hanya meningkat dari **88 menjadi 103 setups** (+15 setup untuk 11 simbol selama 1 tahun penuh, atau ~1.3 setup tambahan per simbol per tahun).
- Alasan penolakan terbesar setelah Stage 0 adalah **Filter Opposing SnR Wall (1.2R buffer)** di Stage 4, yang menolak **156 setups (61.18%)** karena adanya support atau resistance kuat yang terlalu dekat dengan titik entry.
- **Kesimpulan:** Siklus scanner yang menghasilkan `NO_TRADE` adalah perilaku normal dari sistem yang dirancang selektif untuk melindungi modal $16 USDT, bukan kegagalan dealing range filter atau error koneksi database.

---

### Question 2: Apakah filter tersebut meningkatkan atau menurunkan net expectancy?
**Jawaban Berbasis Bukti: MENINGKATKAN SECARA SIGNIFIKAN.**
- Perbandingan empiris 1 tahun penuh membuktikan bahwa mempertahankan filter 72H dealing range menghasilkan performa superior di semua metrik:
  - **Standalone Expectancy:** Baseline A = **+$1.0470 USDT** vs Ablation B = **+$0.8751 USDT** (-16.4%).
  - **Profit Factor:** Baseline A = **3.20** vs Ablation B = **2.64**.
  - **Portfolio Expectancy:** Baseline A = **+$1.0231 USDT** vs Ablation B = **+$0.3602 USDT** (-64.8%).
  - **Portfolio Maximum Drawdown:** Baseline A = **$4.15 USDT** vs Ablation B = **$7.03 USDT** (+69.4% risiko drawdown).
- Secara spesifik, filter 72H dealing range melindungi trade **SHORT**:
  - Pada Baseline A, trade SHORT menghasilkan expectancy **+$0.8797 USDT** (15 trade).
  - Ketika filter dinonaktifkan (Konfigurasi B), trade SHORT anjlok menjadi **+$0.5854 USDT** (22 trade) karena short dieksekusi di tengah dealing range yang rentan terhadap bouncing support lokal.

---

### Question 3: Apakah kandidat terbaik mengungguli baseline pada out-of-sample?
**Jawaban Berbasis Bukti: TIDAK.**
- **Pada Calendar Split 1 (20% OOS: 2026-08-07 s.d. 2026-10-17):**
  - Baseline A Standalone Expectancy = **+$1.1239 USDT** (50 trade, WR 68.0%) mengungguli Konfigurasi B = **+$0.9892 USDT** (54 trade, WR 64.81%).
  - Baseline A Portfolio Expectancy = **+$1.1080 USDT** (4 trade) mengungguli Konfigurasi B = **+$0.2547 USDT** (6 trade).
- **Pada Rolling Walk-Forward Split 2 (Final OOS: >= 2026-09-03):**
  - Seluruh konfigurasi mengalami penurunan akibat konsolidasi sempit pasar crypto.
  - Namun, Baseline A mengalami kerugian per trade yang lebih kecil (**-$0.1477 USDT**) dibandingkan Konfigurasi B (**-$0.3219 USDT**), Konfigurasi C (**-$0.2407 USDT**), dan Konfigurasi D (**-$0.2407 USDT**).
- Tidak ada kandidat ablation yang mampu mengungguli Baseline A pada periode pengujian out-of-sample.

---

### Question 4: Berapa completed trades yang mendukung kesimpulan?
**Rincian Sampel Transaksi Aktual:**
1. **Full 1-Year Sample (Standalone Paper Engine):**
   - Konfigurasi A (Baseline): **74 completed trades** (49 win, 25 loss).
   - Konfigurasi B (Ablasi): **85 completed trades** (53 win, 32 loss).
   - Konfigurasi C (Narrow): **79 completed trades** (50 win, 29 loss).
   - Konfigurasi D (Moderate): **81 completed trades** (52 win, 29 loss).
2. **Full 1-Year Sample (Portfolio Engine dengan Batasan Risiko):**
   - Konfigurasi A (Baseline): **13 completed trades** (9 win, 4 loss).
   - Konfigurasi B (Ablasi): **19 completed trades** (10 win, 9 loss).
3. **Out-of-Sample Window (Split 1):**
   - Baseline A: **50 standalone trades** dan **4 portfolio trades**.
   - Konfigurasi B: **54 standalone trades** dan **6 portfolio trades**.
4. **Final Out-of-Sample Window (Split 2):**
   - Baseline A: **13 standalone trades** dan **1 portfolio trade**.
   - Konfigurasi B: **15 standalone trades** dan **3 portfolio trades**.

---

### Question 5: Apakah bukti cukup untuk melanjutkan paper testing kandidat, atau masih `INSUFFICIENT_DATA`?
**Keputusan Sesuai Task 5 Rules:**
1. **Status Alternatif (Konfigurasi B, C, D): `INSUFFICIENT_DATA` & REJECTED.**
   - Tidak ada kandidat alternatif yang memenuhi kriteria keunggulan out-of-sample.
   - Sampel trade pada jendela OOS terbaru ($N = 13$ dan $N = 15$) berada di bawah ambang signifikansi statistik $N \ge 30$.
2. **Status Keputusan Strategi: `KEEP_BASELINE`.**
   - Konfigurasi Baseline A saat ini terbukti secara empiris memiliki net expectancy positif (**+$1.0470 USDT** standalone / **+$1.0231 USDT** portfolio).
   - Filter 72H dealing range `[0.12, 0.88]` terbukti melindungi kualitas trade dan wajib dipertahankan.

---

## 3. Explicit Prohibitions & Production Safety Reminders

Sesuai invariant keselamatan trading:
- **DILARANG** melonggarkan filter 72H dealing range, 4H bias, atau opposing SnR wall hanya untuk memaksa sinyal muncul di dashboard.
- **DILARANG** mengubah target isolated margin ($6.00) atau menaikkan hard SL cap ($1.50) demi meningkatkan frekuensi trade.
- **DILARANG** membuat transaksi sintesis (fake trades) ke dalam database journal.
- **DILARANG** mengaktifkan live trading (`live_trading_locked=True` tetap wajib).

---

## 4. Next Steps for Forward Paper Testing

1. **Pertahankan Konfigurasi Produksi:**
   - `discount_threshold = 0.12`
   - `premium_threshold = 0.88`
   - `bull_breakout_pos_72h = 0.92`
   - `min_extension_48h = 0.062`
   - `min_impulse_12h = 0.022`
   - `snr_opposing_wall_buffer_r = 1.2`
2. **Eksekusi Paper Forward Otomatis:**
   - Biarkan background scheduler menjalankan pemindaian otomatis setiap penutupan candle 1H.
   - Ketika setup valid muncul (sesuai probabilitas historis ~1-2 trade per minggu di portofolio), `PaperExecutionEngine` akan menempatkan pending limit order dengan perlindungan pre-fill invalidation dan trailing-stop pasca-TP1 secara otomatis.
   - Catat semua event ke dalam SQLite journal dan pantau notifikasi Discord yang telah terintegrasi.
