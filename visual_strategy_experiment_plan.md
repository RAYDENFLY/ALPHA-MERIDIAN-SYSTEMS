# Visual Strategy Dataset Audit & Experimental Plan — 31 TradingView Charts

**Repository:** `C:\Users\Administrator\Documents\SignalGenerator`  
**Image Folder:** `C:\Users\Administrator\Documents\SignalGenerator\chart_analyzeimg`  
**Audit Timestamp:** `2026-10-17T19:56:00+07:00`  
**Safety & Governance Status:** `SCANNER_MODE=PAPER` | `ExecutionMode.RESEARCH` | `live_trading_locked=True` | **Zero production strategy rules modified, zero orders submitted**

---

## Executive Summary & Exploratory Caveat

Sesuai protokol **Phase 1–6**, seluruh 31 file gambar (`1.png` s.d. `31.png`) pada direktori [`chart_analyzeimg`](file:///C:/Users/Administrator/Documents/SignalGenerator/chart_analyzeimg) telah diverifikasi integritas byte/SHA-256-nya dan diinspeksi secara visual satu per satu.

> [!IMPORTANT]
> **Peringatan Ukuran Sampel & Sifat Eksploratif (`N = 31` file / `30` unik):**  
> Dataset ini terdiri dari **31 file** (**30 gambar unik** + **1 duplikat identik** `24.png` = `23.png`), dan hanya **9 gambar** yang memiliki rencana trade lengkap (`entry_price`, `stop_loss`, dan `tp1`/`tp2`/`tp3` eksplisit melalui *TradingView Long/Short Position Tool*). Sebanyak **22 gambar lainnya** (`21` unik) hanya menampilkan zona struktural (`Demand`, `supply`, `Area Beli`, `fvg`, atau garis tren) beserta proyeksi panah dan target tanpa level *Stop Loss* (`stop_loss = UNKNOWN`). Oleh karena itu, temuan dari dataset ini **wajib diperlakukan sebagai analisis eksploratif (*exploratory hypothesis generation*)**—bukan bukti peningkatan performa yang signifikan secara statistik dan belum cukup untuk melatih model prediksi otomatis yang andal.

Empat artefak terstruktur telah dibuat di root repositori:
1. [`chart_dataset_manifest.csv`](file:///C:/Users/Administrator/Documents/SignalGenerator/chart_dataset_manifest.csv) — Inventaris lengkap 31 sampel (`sample_id`, `filename`, ukuran byte, prefix SHA-256, status keterbacaan, flag duplikat, `setup_cluster_id`, simbol, timeframe, timestamp chart, arah, kelengkapan level, dan `outcome_label`).
2. [`chart_visual_annotations.jsonl`](file:///C:/Users/Administrator/Documents/SignalGenerator/chart_visual_annotations.jsonl) — Ekstraksi 21 atribut visual per gambar dalam format JSON Lines, memisahkan secara tegas `FACT` (fakta visual yang tertulis/tergambar pada chart) dari `INTERPRETATION` (interpretasi struktur SMC/SnR), serta menggunakan `UNKNOWN` untuk seluruh level yang tidak tertera.
3. [`chart_outcome_labels.csv`](file:///C:/Users/Administrator/Documents/SignalGenerator/chart_outcome_labels.csv) — Klasifikasi hasil berbasis bukti (`TP_HIT`, `SL_HIT`, `INVALIDATED`, `NO_FILL`, `UNRESOLVED`, `UNKNOWN_OUTCOME`) beserta bukti verifikasinya.
4. [`visual_strategy_experiment_plan.md`](file:///C:/Users/Administrator/Documents/SignalGenerator/visual_strategy_experiment_plan.md) — Dokumen laporan audit dan rencana eksperimen komparatif **Model A (Baseline)** vs **Model B (Visual-Enhanced)** ini.

---

## PHASE 1 — Audit Akses, Inventaris, Duplikat, dan Ambiguitas

### 1.1 Ringkasan Kuantitatif Inventaris

| Kategori Audit | Jumlah File | Detail Sampel |
| :--- | :---: | :--- |
| **Total File di `chart_analyzeimg`** | **31** | `1.png` s.d. `31.png` (`SAMPLE_001` s.d. `SAMPLE_031`) |
| **Berhasil Diakses & Dibaca (`readable=True`)** | **31 / 31** | `100%` gambar valid (PNG utuh, resolusi & label axis TradingView terbaca jelas) |
| **Gagal Dibaca / Korup (`readable=False`)** | **0** | Tidak ada file rusak |
| **Duplikat Byte-Identik (`is_duplicate=True`)** | **1 pasang** (`2` file) | **`24.png` (`SAMPLE_024`) adalah salinan byte-for-byte identik dari `23.png` (`SAMPLE_023`)** (`279,178 bytes`, `sha256=e203e1e8f0c25914`, simbol `HBARUSDT.P`, `240M`, `2026-10-12 11:26:31 UTC+7`) |
| **Sampel Unik (`is_duplicate=False`)** | **30** | `31` total dikurangi `1` duplikat (`SAMPLE_024`) |
| **Setup Lengkap (`has_complete_levels=True`)** | **9** | Memiliki `Entry`, `SL`, `TP1`, `TP2`, `TP3/MAX` eksplisit: `SAMPLE_001` (`1.png`), `SAMPLE_004` (`4.png`), `SAMPLE_006` (`6.png`), `SAMPLE_011` (`11.png`), `SAMPLE_015` (`15.png`), `SAMPLE_019` (`19.png`), `SAMPLE_020` (`20.png`), `SAMPLE_022` (`22.png`), `SAMPLE_029` (`29.png`) |
| **Setup Tidak Lengkap / Ambigu (`is_ambiguous=True`)** | **29** (`28` unik) | Mencakup: (a) **22 file** (`21` unik + `1` duplikat) tanpa `stop_loss` / `entry_price` eksplisit (hanya kotak zona + panah), serta (b) **7 dari 9 file ber-Position-Box** yang memiliki ambiguitas *timestamp anchoring* (kotak posisi digambar di sisi kanan/masa depan atau di atas candle breakout yang sedang berjalan). Hanya `SAMPLE_001` (`1.png`) dan `SAMPLE_021` (`21.png`, yang terverifikasi `INVALIDATED` oleh `15.png`) yang tidak ambigu status akhirnya. |

### 1.2 Kelompok Simbol Beririsan (*Multi-Snapshot / Same-Setup Clusters*)

Untuk mencegah *data leakage* pada saat pemisahan data pengembangan dan pengujian (Phase 4), seluruh 31 gambar telah dikelompokkan berdasarkan `setup_cluster_id`. Terdapat **5 kelompok simbol yang muncul lebih dari satu kali**:

1. **`CLUSTER_HBARUSDT_20261012` (`HBARUSDT.P`)**:
   - `SAMPLE_023` (`23.png`, `240M`, `12/10/2026 11:26:31`) & `SAMPLE_024` (`24.png`, duplikat byte-identik).
2. **`CLUSTER_USELESSUSDT_20261012_13` (`USELESSUSDT.P`)**:
   - `SAMPLE_021` (`21.png`, `240M`, `12/10/2026 11:49:57`, ide `LONG` pada zona Demand `0.18251–0.18955`) & `SAMPLE_015` (`15.png`, `240M`, `13/10/2026 21:16:06`, setup `SHORT` dari zona Supply `0.19493` setelah zona Demand pada `21.png` jebol ke `0.161`).
3. **`CLUSTER_INJUSDT_20261010_11` (`INJUSDT.P`)**:
   - `SAMPLE_027` (`27.png`, `240M`, `10/10/2026 16:53:32`, pantulan `+9.75%` dari Demand `6.397–6.772`) & `SAMPLE_026` (`26.png`, `D`, `11/10/2026 16:56:03`, tampilan *Daily* pada periode harga yang sama `7.236` dengan tambahan zona bawah `Area Beli 4.636–5.096`).
4. **`CLUSTER_BTWUSDT_20261014_17` (`BTWUSDT.P`)**:
   - `SAMPLE_006` (`6.png`, `15M`, `14/10/2026 13:57:13`, `LONG` limit di `2.3332` saat harga `2.6250`) & `SAMPLE_002` (`2.png`, `240M`, `17/10/2026 13:44:02`, `SHORT` dari Supply `2.1706–2.4842` setelah tren 4H berbalik turun ke `2.0690`).
5. **`BTCUSDT.P` (2 Periode Berbeda — 6 Hari Terpisah)**:
   - `SAMPLE_019` (`19.png`, `60M`, `09/10/2026 13:38:42`, `LONG` di `81,386.2`) & `SAMPLE_010` (`10.png`, `240M`, `15/10/2026 20:30:46`, `SHORT` breakdown di `80,142.1`).

---

## PHASE 2 — Ringkasan Ekstraksi Visual Tanpa Mengarang Data

Seluruh detail per gambar disimpan secara lengkap di [`chart_visual_annotations.jsonl`](file:///C:/Users/Administrator/Documents/SignalGenerator/chart_visual_annotations.jsonl). Tabel berikut merangkum hasil ekstraksi faktual untuk ke-31 sampel:

| `sample_id` | File | Simbol | TF | Timestamp (`UTC+7`) | Arah | Fitur Visual Utama (`FACT`) | `entry_price` | `stop_loss` | `tp1` | `tp2` | `tp3` | Kualitas Setup |
| :--- | :--- | :--- | :---: | :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `SAMPLE_001` | `1.png` | `BATUSDT.P` | `240M` | `2026-10-17 14:25:51` | `LONG` | 3x garis BOS naik + kotak Demand abu-abu (`0.165–0.17834`) | `0.17834` | `0.16166` | `0.19502` | `0.21170` | `0.22838` | `COMPLETE_EXECUTABLE_PLAN` |
| `SAMPLE_002` | `2.png` | `BTWUSDT.P` | `240M` | `2026-10-17 13:44:02` | `SHORT` | Breakdown struktur + kotak `supply` (`2.1706–2.4842`) + panah | `UNKNOWN` | `UNKNOWN` | `1.6085` | `1.2525` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_003` | `3.png` | `AAVEUSDT.P` | `240M` | `2026-10-17 10:20:41` | `LONG` | Pantulan `Demand` (`144–149.5`) + Trendline Breakout + `fvg` (`151.40–154.81`) | `UNKNOWN` | `UNKNOWN` | `165.43` | `172.92` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_004` | `4.png` | `SUPERUSDT.P` | `240M` | `2026-10-16 17:46:52` | `LONG` | Descending Trendline Breakout + kotak Demand (`0.191–0.19833`) | `0.19833` | `0.18449` | `0.21217` | `0.22601` | `0.26753` | `COMPLETE_EXECUTABLE_PLAN` |
| `SAMPLE_005` | `5.png` | `PEOPLEUSDT.P` | `240M` | `2026-10-16 13:04:44` | `LONG` | Dashed Trendline Breakout + kotak pullback (`0.007466–0.007647`) | `UNKNOWN` | `UNKNOWN` | `0.008153` | `0.008658` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_006` | `6.png` | `BTWUSDT.P` | `15M` | `2026-10-14 13:57:13` | `LONG` | Likuiditas internal `$$$` (`~2.37`) di atas kotak Demand (`2.24–2.3332`) | `2.3332` | `2.2201` | `2.4463` | `2.5594` | `2.7856` | `COMPLETE_EXECUTABLE_PLAN` |
| `SAMPLE_007` | `7.png` | `NEARUSDT.P` | `240M` | `2026-10-15 21:28:21` | `LONG` | Sentuhan Ascending Trendline (`4.723`) + panah `↑` + garis SnR | `UNKNOWN` | `UNKNOWN` | `5.111` | `5.187` | `5.632` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_008` | `8.png` | `ONDOUSDT.P` | `D` | `2026-10-16 21:16:01` | `LONG` | Dashed Trendline Breakout + kotak `Area Beli` (`0.3240–0.3650`) | `UNKNOWN` | `UNKNOWN` | `0.4414` | `0.5091` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_009` | `9.png` | `FETUSDT.P` | `D` | `2026-10-16 20:58:41` | `LONG` | Equal lows `$$$` (`~0.195`) + S/R flip + `Demand` (`0.1678–0.1867`) | `UNKNOWN` | `UNKNOWN` | `0.2124` | `0.2436` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_010` | `10.png` | `BTCUSDT.P` | `240M` | `2026-10-15 20:30:46` | `SHORT` | Breakdown Ascending Support Trendline (`~81,200`) + panah turun | `UNKNOWN` | `UNKNOWN` | `77177.4` | `75167.0` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_011` | `11.png` | `AVAUSDT.P` | `30M` | `2026-10-15 10:33:39` | `LONG` | 2x garis BOS naik + kotak Demand (`~0.246–0.2552`) | `0.2552` | `0.2382` | `0.2722` | `0.2892` | `0.3232` | `COMPLETE_EXECUTABLE_PLAN` |
| `SAMPLE_012` | `12.png` | `DUSKUSDT.P` | `240M` | `2026-10-14 22:10:36` | `LONG` | Range Breakout / S/R flip (`~0.0870`) + `Demand` (`0.08241–0.08914`) | `UNKNOWN` | `UNKNOWN` | `0.10174` | `0.10980` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_013` | `13.png` | `QUSDT.P` | `D` | `2026-10-15 17:20:20` | `LONG` | Breakout resistensi (`~0.0308`) + `Area Beli` (`0.027819–0.031770`) | `UNKNOWN` | `UNKNOWN` | `0.041605` | `0.057410` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_014` | `14.png` | `ZROUSDT.P` | `60M` | `2026-10-14 11:26:58` | `SHORT` | Sweep likuiditas `$$$` (`~2.385`) + 2 kotak `supply` (`2.1583–2.2550` & `2.3365–2.4339`) | `UNKNOWN` | `UNKNOWN` | `1.9720` | `1.8881` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_015` | `15.png` | `USELESSUSDT.P` | `240M` | `2026-10-13 21:16:06` | `SHORT` | CHoCH internal ke kotak Supply (`0.19493–0.209`) + 2x BOS turun | `0.19493` | `0.21300` | `0.17686` | `0.15879` | `0.14072` | `COMPLETE_EXECUTABLE_PLAN` |
| `SAMPLE_016` | `16.png` | `TAOUSDT.P` | `D` | `2026-10-14 17:48:38` | `LONG` | S/R flip (`~260`) + sentuhan `Demand` (`248.00–263.81`, low `262.00`) | `UNKNOWN` | `UNKNOWN` | `301.75` | `320.50` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_017` | `17.png` | `ONTUSDT.P` | `D` | `2026-10-14 15:38:46` | `LONG` | S/R flip + kotak `fvg` (`0.05026–0.05224`) + proyeksi double-bottom | `UNKNOWN` | `UNKNOWN` | `0.05658` | `0.06186` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_018` | `18.png` | `RENDERUSDT.P` | `240M` | `2026-10-13 15:31:56` | `LONG` | 3x garis BOS naik + kotak `Demand` (`1.825–1.874`) | `UNKNOWN` | `UNKNOWN` | `2.020` | `2.171` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_019` | `19.png` | `BTCUSDT.P` | `60M` | `2026-10-09 13:38:42` | `LONG` | Pantulan Demand HTF (`80.2k–80.6k`) + BOS 1H (`81,650`) + OB (`81,386.2`) | `81386.2` | `80314.2` | `82458.2` | `83530.2` | `84602.2` | `COMPLETE_EXECUTABLE_PLAN` |
| `SAMPLE_020` | `20.png` | `ENAUSDT.P` | `240M` | `2026-10-12 16:32:40` | `LONG` | Sweep `$$$` (`~0.200`) + Dashed Trendline Breakout + `Demand` (`0.219–0.22527`) | `0.22527` | `0.20826` | `0.24228` | `0.25928` | `0.29329` | `COMPLETE_EXECUTABLE_PLAN` |
| `SAMPLE_021` | `21.png` | `USELESSUSDT.P` | `240M` | `2026-10-12 11:49:57` | `LONG` | Break struktur internal (`~0.200`) + kotak `Demand` (`0.18251–0.18955`) | `UNKNOWN` | `UNKNOWN` | `0.21165` | `0.22290` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_022` | `22.png` | `GMTUSDT.P` | `240M` | `2026-10-12 11:28:51` | `LONG` | Sweep support (`~0.00813` ke `0.007502`) + V-reclaim + OB (`0.008154`) | `0.008154` | `0.007502` | `0.008806` | `0.009458` | `0.010110` | `COMPLETE_EXECUTABLE_PLAN` |
| `SAMPLE_023` | `23.png` | `HBARUSDT.P` | `240M` | `2026-10-12 11:26:31` | `LONG` | S/R flip (`~0.0865`) + kotak `Demand` (`0.08435–0.08649`) | `UNKNOWN` | `UNKNOWN` | `0.09055` | `0.09438` | `0.10029` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_024` | `24.png` | `HBARUSDT.P` | `240M` | `2026-10-12 11:26:31` | `LONG` | **Duplikat identik dari `23.png` (`SAMPLE_023`)** | `UNKNOWN` | `UNKNOWN` | `0.09055` | `0.09438` | `0.10029` | `DUPLICATE_AND_INCOMPLETE` |
| `SAMPLE_025` | `25.png` | `COTIUSDT.P` | `60M` | `2026-10-11 10:46:51` | `LONG` | V-reversal + BOS (`~0.01325`) + kotak `Demand` (`0.012652–0.012772`) | `UNKNOWN` | `UNKNOWN` | `0.012950` | `0.013354` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_026` | `26.png` | `INJUSDT.P` | `D` | `2026-10-11 16:56:03` | `LONG` | BOS (`~6.10`) + pantulan zona atas (`6.4–6.8`) + `Area Beli` (`4.636–5.096`) | `UNKNOWN` | `UNKNOWN` | `5.695` | `6.426` | `8.278` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_027` | `27.png` | `INJUSDT.P` | `240M` | `2026-10-10 16:53:32` | `LONG` | BOS (`~6.10`) + pantulan ke-2 `Demand` (`6.397–6.772`) terukur `+9.75%` | `UNKNOWN` | `UNKNOWN` | `7.430` | `7.903` | `8.337` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_028` | `28.png` | `CTSIUSDT.P` | `240M` | `2026-10-10 14:53:31` | `LONG` | Breakout (`~0.0355`) + likuiditas `$$$` (`~0.0310`) + `Demand` (`0.02912–0.03037`) | `UNKNOWN` | `UNKNOWN` | `0.03242` | `0.03373` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_029` | `29.png` | `ORCAUSDT.P` | `15M` | `2026-10-10 12:38:51` | `LONG` | Sweep support (`~2.255`) ke Demand bawah + OB internal (`2.278`) | `2.278` | `2.210` | `2.346` | `2.414` | `2.618` | `COMPLETE_EXECUTABLE_PLAN` |
| `SAMPLE_030` | `30.png` | `USUSDT.P` | `D` | `2026-10-10 18:39:46` | `LONG` | Sweep support (`~0.0142`) + candle `+82.84%` + `Area Beli` (`0.012169–0.016424`) | `UNKNOWN` | `UNKNOWN` | `0.021128` | `0.034220` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |
| `SAMPLE_031` | `31.png` | `STRKUSDT.P` | `240M` | `2026-10-09 15:07:01` | `LONG` | 3x garis BOS naik + `$$$` (`~0.0545`) + `Demand` (`0.04785–0.05363`) | `UNKNOWN` | `UNKNOWN` | `0.06224` | `0.07028` | `UNKNOWN` | `INCOMPLETE_NON_EXECUTABLE` |

---

## PHASE 3 — Outcome Labeling & Analisis Bukti Hasil

Sesuai aturan Phase 3:
- Kami **tidak** menganggap panah proyeksi sebagai bukti harga mencapai target.
- Kami **tidak** mengubah `INVALIDATED` menjadi `SL_HIT` apabila tidak ada posisi yang terisi dan tidak ada level *Stop Loss* eksplisit.
- Jika candle masa depan setelah *timestamp screenshot* tidak tersedia pada chart (atau setup belum terisi saat screenshot diambil), sampel diklasifikasikan sebagai `NO_FILL` atau `UNRESOLVED`.

### 3.1 Distribusi Label Hasil (`chart_outcome_labels.csv`)

| `outcome_label` | Jumlah File (Total `31`) | Jumlah Unik (`30`) | Sampel (`sample_id` / `filename`) | Penjelasan Bukti Faktual |
| :--- | :---: | :---: | :--- | :--- |
| **`TP_HIT`** | **2** | **2** | `SAMPLE_001` (`1.png`), `SAMPLE_004` (`4.png`) | • **`1.png` (`BATUSDT.P` 4H):** Candle 15 Okt menyentuh zona Demand (`~0.167`, di atas `SL 0.16166`), memicu `entry_price=0.17834`, lalu reli menembus `TP1 (0.19502)` dan `TP2 (0.21170)` hingga `~0.223` pada 16 Okt.<br>• **`4.png` (`SUPERUSDT.P` 4H):** Candle breakout 16 Okt menembus dari `entry_price=0.19833` melewati `TP1 (0.21217)` dan `TP2 (0.22601, wick ~0.230)`. *(Dicatat memiliki ambiguitas waktu penggambaran kotak posisi pada candle breakout).* |
| **`SL_HIT`** | **0** | **0** | — | Tidak ada satu pun dari 9 chart ber-`stop_loss` eksplisit yang menampilkan posisi terisi lalu menyentuh garis merah SL pada candle yang terlihat. |
| **`INVALIDATED`** | **1** | **1** | `SAMPLE_021` (`21.png`) | • **`21.png` (`USELESSUSDT.P` 4H, 12 Okt 11:49):** Menampilkan ide `LONG` pada zona `Demand` `0.18251–0.18955` tanpa `stop_loss` eksplisit. Bukti candle 4H lanjutan tersedia secara langsung pada **`15.png` (`USELESSUSDT.P` 4H, 13 Okt 21:16)**: harga gagal mencapai `tp1=0.21165`, menjebol lurus menembus zona Demand `0.18251–0.18955` dan menembus *swing low* `0.172` hingga `0.161`. Karena `21.png` tidak memiliki order/SL eksplisit, diklasifikasikan secara ketat sebagai `INVALIDATED` (bukan `SL_HIT`). |
| **`NO_FILL`** | **21** | **20** (+1 dup) | `2, 5, 8, 9, 11, 12, 13, 14, 17, 18, 19, 20, 22, 23, (24), 25, 26, 28, 29, 30, 31.png` | Pada saat screenshot diambil, harga pasar sedang berada jauh di atas zona `Demand`/`Area Beli`/`fvg` (untuk `LONG`) atau di bawah zona `supply` (untuk `SHORT`) dan **belum melakukan *pullback* ke level entry**. Termasuk 5 setup lengkap (`11.png`, `19.png`, `20.png`, `22.png`, `29.png`) di mana harga langsung lari ke atas atau masih dalam perjalanan turun menuju *limit entry*. |
| **`UNRESOLVED`** | **7** | **7** | `3, 6, 7, 10, 15, 16, 27.png` | • **`3.png` (`AAVEUSDT` 4H), `16.png` (`TAOUSDT` D), `27.png` (`INJUSDT` 4H):** Harga sedang menyentuh/memantul dari zona `fvg`/`Demand`, namun belum mencapai `tp1` dan tidak memiliki `stop_loss` eksplisit.<br>• **`7.png` (`NEARUSDT` 4H) & `10.png` (`BTCUSDT` 4H):** Diambil tepat saat *trendline touch/breakdown* tanpa SL.<br>• **`6.png` (`BTWUSDT` 15m) & `15.png` (`USELESSUSDT` 4H):** Memiliki *Position Box* lengkap yang diletakkan di sisi kanan candle berjalan sehingga status pasca-candle terakhir tidak dapat diverifikasi dari screenshot semata. |
| **`UNKNOWN_OUTCOME`** | **0** | **0** | — | Seluruh 31 gambar dapat dipetakan secara spesifik ke dalam `TP_HIT`, `INVALIDATED`, `NO_FILL`, atau `UNRESOLVED`. |

### 3.2 Temuan Kritis dari Analisis Visual terhadap Strategi SignalGenerator Saat Ini

Dari bedah visual 31 chart tersebut, teridentifikasi **4 perbedaan struktural utama** antara cara anotator manusia memilih setup di TradingView vs cara [`SetupGenerator`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/strategy/generator.py) pada SignalGenerator bekerja saat ini:

1. **Dominasi Setup *Pullback-Limit* (25 dari 30 Chart Unik) vs Eksekusi *Immediate-Bar*:**
   - Sebagian besar chart (`83.3%`) menggambar skenario **menunggu *pullback* harga ke zona `Demand`/`Supply`/`FVG` yang berada cukup jauh dari harga saat ini** (sering kali setelah terjadi *Break of Structure* / `BOS`). Akibatnya, mayoritas screenshot berakhir sebagai `NO_FILL` pada saat diambil, atau harga sudah telanjur mencapai `TP1` sebelum menyentuh *limit order* (contoh nyata: `19.png` `BTCUSDT 1H`, `11.png` `AVAUSDT 30m`, `23.png` `HBARUSDT 4H`, `29.png` `ORCAUSDT 15m`).
2. **Fitur Visual yang Belum Dimiliki Mesin `SMC/SnR` Saat Ini:**
   - **Diagonal Trendline Breakout (`5 chart`: `3.png`, `4.png`, `5.png`, `8.png`, `10.png`, `20.png`):** Mesin [`src/smc/`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/smc/structure.py) dan [`src/snr/`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/snr/zones.py) saat ini hanya mendeteksi *swing high/low* horizontal (`BOS`/`CHoCH`, *Order Block*, `FVG`, dan klaster SnR horizontal), tetapi **belum memiliki pendeteksi garis tren diagonal (*diagonal trendline breakout / retest*)**.
   - **Inducement / Internal Liquidity Pool (`$$$` di atas Demand / di bawah Supply — `6 chart`: `6.png`, `9.png`, `14.png`, `20.png`, `28.png`, `31.png`):** Anotasi manusia secara konsisten mencari kumpulan *equal lows/highs* (`$$$`) yang berada **tepat di depan** zona `Demand`/`Supply` sebagai "umpan likuiditas" (*inducement*) sebelum masuk di blok order di belakangnya. Sementara itu, [`LiquiditySweepDetector`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/smc/liquidity.py) saat ini baru mendeteksi *sweep* yang **sudah terjadi pada candle terakhir**, bukan keberadaan *inducement pool* di depan zona *Order Block*.
3. **Kegagalan Setup *Counter-Trend* Internal (`21.png` $\rightarrow$ `15.png` `USELESSUSDT.P`):**
   - Pasangan `21.png` dan `15.png` memberikan bukti empiris yang sangat berharga: pada `21.png`, anotator melihat *internal bullish break* pada timeframe 4H di tengah tren turun tajam dan menggambar zona `Demand` `0.18251–0.18955` untuk `LONG`. Tiga puluh tiga jam kemudian (`15.png`), terbukti bahwa *internal break* tersebut hanyalah *pullback* menuju `Supply` 4H (`0.19493–0.209`) sebelum harga ambruk menembus `Demand` dan mencetak *lower low* di `0.161`. Ini memvalidasi pentingnya filter tren makro (`bias_4h` dan `ret_168` 7D macro cap) yang sudah diterapkan pada [`SetupGenerator`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/strategy/generator.py#L137-L155).
4. **Ketidaksesuaian Lebar Stop-Loss Visual dengan Batas Risiko `$6` Isolated Margin (`$1.50` Max Loss):**
   - Pada 9 chart yang memiliki `Entry` dan `SL` eksplisit, jarak *Stop Loss* (`|Entry - SL| / Entry`) berkisar antara **`1.32%` (`19.png` `BTCUSDT 1H`) hingga `9.35%` (`1.png` `BATUSDT 4H`)**, dengan median **`6.98%`**:
     - `19.png` (`BTCUSDT 1H`): `1.32%` $\rightarrow$ **Layak penuh (`$6.00` margin, `10x` leverage, est. loss `~$0.91 <= $1.50`)**
     - `29.png` (`ORCAUSDT 15m`): `2.99%` $\rightarrow$ **Memerlukan penurunan ukuran posisi (*risk-capped down-scaling*) agar loss $\le \$1.50$**
     - `6.png` (`4.85%`), `11.png` (`6.66%`), `4.png` (`6.98%`), `20.png` (`7.55%`), `22.png` (`8.00%`), `15.png` (`9.27%`), `1.png` (`9.35%`) $\rightarrow$ Pada stop distance `5%–9.35%`, agar estimasi kerugian saat SL tidak melewati `$1.50`, notional maksimal yang diizinkan hanyalah `~$15–$28 USDT` (margin `~$1.50–$2.80` pada `10x`), atau pada beberapa simbol akan ditolak (`INFEASIBLE`) jika melanggar `min_notional` bursa.

---

## PHASE 4 — Desain Eksperimen: Model A (Baseline) vs Model B (Visual-Enhanced)

### 4.1 Definisi Model yang Dibandingkan

| Komponen | **Model A — Baseline (Tanpa Fitur Visual Chart)** | **Model B — Visual-Enhanced (Dengan Fitur Struktur Visual)** |
| :--- | :--- | :--- |
| **Sumber Data Dasar** | OHLCV historis (`15m`, `30m`, `1h`, `4h`, `1d`) + *Funding Rate* dari AsterDEX / SQLite `raw_candles`. | Identik dengan Model A (OHLCV + *Funding Rate* pada simbol dan periode yang sama persis). |
| **Fitur Input & Konfluensi** | Fitur numerik & aturan [`SetupGenerator`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/strategy/generator.py) saat ini:<br>1. `MultiTimeframeBiasEngine` (EMA/struktur 4H)<br>2. Dealing Range 72H (`pos_in_range_72`) & Variance Ratio `VR(24h/12h)`<br>3. `MarketStructureEngine` (`BOS`/`CHoCH` horizontal)<br>4. `OrderBlockDetector`, `FVGDetector`, `LiquiditySweepDetector`, `SnRInteractionEngine`<br>5. 10 fitur numerik `FeaturePipeline` (`sl_dist`, `tp_dist`, `body`, `upper_wick`, `lower_wick`, `rvol`, dll.) | Seluruh input **Model A** ditambah **4 fitur struktur visual terkuantifikasi** yang diekstrak secara *point-in-time* pada candle keputusan $t_0$:<br>1. `has_diagonal_trendline_breakout` $\in \{0, 1\}$ (apakah candle $t_0$ atau $\le 3$ bar sebelumnya menembus garis regresi/pivot *swing high/low* diagonal dengan $\ge 2$ titik sentuh)<br>2. `has_inducement_pool_ahead_of_zone` $\in \{0, 1\}$ (keberadaan *equal highs/lows* `$$$` dalam jarak $0.2\text{--}1.5 \times \text{ATR}$ di depan zona `OrderBlock`/`Demand`/`Supply`)<br>3. `zone_confluence_score` $\in [0, 4]$ (jumlah konfluensi struktural pada zona entry: `OB/Demand/Supply` + `S/R Flip` + `FVG` + `Inducement $$$`)<br>4. `htf_counter_trend_warning` $\in \{0, 1\}$ (penalti jika `BOS`/`CHoCH` hanya bersifat internal melawan tren 4H/Daily seperti kasus `21.png`). |
| **Aturan Entry, Exit, Biaya, & Risiko** | **Setara penuh:**<br>• `target_isolated_margin = $6.00` (`max $6.50`), `leverage = 10x`<br>• `max_loss_at_sl <= $1.50 USDT` (termasuk `maker_fee=0.02%`, `taker_fee=0.05%`, `slippage=0.05%` per sisi)<br>• `max_open_positions = 1`, `max_entries_per_week = 3`<br>• Resolusi intrabar konservatif: `SL_FIRST` (`SL_COLLISION` dihitung sebagai Loss). | **Setara penuh dengan Model A** (menggunakan [`PositionSizer`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/risk/sizing.py), [`RiskManager`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/risk/manager.py), dan [`BacktestEngine`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/backtest/engine.py) yang identik). |

### 4.2 Pencegahan *Lookahead Bias* & *Data Leakage*

1. **Aturan *Point-in-Time* Ketat ($t \le t_{\text{decision}}$):**
   - Fitur visual pada **Model B** hanya boleh dihitung dari candle yang **sudah tutup pada atau sebelum waktu pembuatan keputusan ($t_{\text{decision}}$)**.
   - Untuk chart yang kotaknya digambar pada atau setelah candle *breakout* (`4.png`, `15.png`), titik keputusan $t_{\text{decision}}$ harus ditetapkan pada **penutupan candle konfirmasi struktur** sebelum target tersentuh, dan simulasi *fill* hanya boleh menggunakan bar $t > t_{\text{decision}}$.
   - Label hasil (`outcome_label` dari Phase 3) **dilarang keras** digunakan sebagai fitur input, filter seleksi sampel, maupun bobot pada waktu entry.
2. **Deduplikasi Wajib Pra-Split:**
   - `SAMPLE_024` (`24.png`) **wajib dihapus** dari himpunan evaluasi karena identik byte-for-byte dengan `SAMPLE_023` (`23.png`).
3. **Pemisahan *Group-Aware Chronological / Walk-Forward Split*:**
   - Seluruh sampel yang berbagi `setup_cluster_id` atau simbol pada periode berdekatan **wajib ditempatkan dalam *fold* yang sama** (tidak boleh satu di *train/dev* dan satu di *test*):
     - `CLUSTER_USELESSUSDT_20261012_13` (`21.png` & `15.png`) $\rightarrow$ wajib dalam satu grup.
     - `CLUSTER_INJUSDT_20261010_11` (`27.png` & `26.png`) $\rightarrow$ wajib dalam satu grup.
     - `CLUSTER_BTWUSDT_20261014_17` (`6.png` & `2.png`) $\rightarrow$ wajib dalam satu grup.
   - Urutan walk-forward dibagi secara kronologis berdasarkan `chart_timestamp`:
     - **Window 1 (09 Okt – 12 Okt 2026):** `19.png, 31.png, 29.png, 28.png, 27.png, 26.png, 30.png, 25.png, 23.png (dedup 24.png), 22.png, 21.png, 15.png (grouped), 20.png`
     - **Window 2 (13 Okt – 15 Okt 2026):** `18.png, 14.png, 6.png, 2.png (grouped), 17.png, 16.png, 12.png, 11.png, 13.png, 10.png, 7.png`
     - **Window 3 (16 Okt – 17 Okt 2026):** `5.png, 4.png, 9.png, 8.png, 3.png, 1.png`

---

## PHASE 5 — Metrik Evaluasi & Kriteria Pengambilan Keputusan

Setiap eksperimen perbandingan **Model A vs Model B** (baik pada subset *chart-aligned* maupun pada *backtest* historis multi-bulan) wajib melaporkan 8 metrik berikut secara berdampingan:

| No | Metrik Evaluasi | Definisi & Formula | Mengapa Penting (Anti-Bias) |
| :---: | :--- | :--- | :--- |
| **1** | **Net Expectancy per Trade ($E_{\text{net}}$)** | $\frac{1}{N}\sum_{i=1}^{N} \text{PnL}_{\text{net}, i}$ (dalam USDT dan dalam satuan $R$) | Mengukur nilai harapan bersih per transaksi setelah seluruh biaya; metrik utama kelayakan strategi. |
| **2** | **Profit Factor ($\text{PF}$)** | $\frac{\sum \text{PnL}_{\text{net}}^+}{\left|\sum \text{PnL}_{\text{net}}^-\right|}$ | Rasio total keuntungan bersih terhadap total kerugian bersih (target minimal $\text{PF} > 1.25$ di luar sampel). |
| **3** | **Maximum Drawdown ($\text{MaxDD}$)** | Penurunan puncak-ke-lembah terbesar pada kurva ekuitas bersih (dalam USDT dan `%` terhadap `$16.00` ekuitas awal) | Memastikan peningkatan *win rate* tidak dibayar dengan *tail risk* atau *drawdown* yang melanggar batas mingguan (`$0.54`). |
| **4** | **Net PnL (Setelah Fee, Slippage, & Funding)** | $\sum (\text{PnL}_{\text{gross}} - \text{Fee}_{\text{entry}} - \text{Fee}_{\text{exit}} - \text{Slippage} - \text{Funding})$ | Mencegah ilusi profit dari setup ber-target sempit yang habis tergerus *taker fee* (`0.05%`) + *slippage* (`0.05%`). |
| **5** | **Jumlah Trade ($N_{\text{filled}}$) & Frekuensi Entry** | Total order terisi (`FILLED`), rasio `FILLED / SUBMITTED`, dan rata-rata trade per minggu | Mendeteksi masalah utama dataset visual: filter yang terlalu sempit atau *limit order* terlalu dalam yang menghasilkan `NO_FILL` tinggi dan sampel terlalu kecil. |
| **6** | **Win Rate beserta Jumlah Sampel & CI 95%** | $\hat{p} = \frac{N_{\text{win}}}{N_{\text{filled}}}$ beserta interval kepercayaan *Wilson Binomial 95%* $[p_{\text{low}}, p_{\text{high}}]$ | Pada $N=9$ setup lengkap (atau $N=30$ unik), interval kepercayaan 95% sangat lebar ($\pm 25\%\text{--}33\%$), sehingga kenaikan *win rate* tanpa ukuran sampel memadai tidak boleh diklaim signifikan. |
| **7** | **Kualitas Kalibrasi Probabilitas** | `Brier Score`, `Log Loss`, `ROC-AUC`, dan `Expected Calibration Error (ECE)` (menggunakan [`ModelCalibrator`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/ml/calibration.py)) | Menguji apakah skor probabilitas ML + visual benar-benar mencerminkan peluang empiris `TP_HIT`. |
| **8** | **Jumlah Sinyal Ditolak & Distribusi Alasan Penolakan** | Rincian jumlah sinyal yang ditolak karena: `MISSING_SL_TP`, `SL_EXCEEDS_1.50_CAP`, `MIN_NOTIONAL_VIOLATION`, `COUNTER_TREND_4H`, `LOW_RR`, atau `NO_FILL_TIMEOUT` | Memberikan visibilitas penuh apakah Model B meningkatkan kualitas dengan menyaring *false breakout* (seperti `21.png`) atau justru menolak hampir semua peluang. |

---

## PHASE 6 — Pemisahan Hasil Terukur Aktual vs Rencana Eksperimen Selanjutnya

### 6.1 Hasil Aktual yang Benar-Benar Terukur Saat Ini (*Measured Facts*)

1. **Kualitas & Kelengkapan Dataset (`N = 31` file):**
   - **31/31 file terbaca sempurna**, **1 duplikat identik** (`24.png` = `23.png`), menyisakan **30 chart unik** dari **26 simbol perpetual futures berbeda**.
   - Hanya **9 dari 30 chart unik (`30.0%`)** yang memiliki rencana trade lengkap (`entry_price`, `stop_loss`, `tp1`, `tp2`, `tp3`).
   - Sebanyak **21 dari 30 chart unik (`70.0%`)** tidak memiliki `stop_loss` eksplisit (`stop_loss = UNKNOWN`) dan tidak memenuhi syarat sebagai trade yang valid tanpa aturan penempatan SL tambahan.
2. **Distribusi Outcome Aktual Berdasarkan Bukti Visual & Lintas-Chart:**
   - Dari **9 chart lengkap**: **2 `TP_HIT`** (`1.png` `BATUSDT 4H`, `4.png` `SUPERUSDT 4H`), **0 `SL_HIT`**, **5 `NO_FILL`** pada saat screenshot diambil (`11.png`, `19.png`, `20.png`, `22.png`, `29.png`), dan **2 `UNRESOLVED`** (`6.png`, `15.png`).
   - Dari **21 chart unik tidak lengkap**: **1 `INVALIDATED`** (`21.png` `USELESSUSDT 4H`, dibuktikan gagal oleh `15.png`), **15 `NO_FILL`** (harga belum *pullback* ke zona anotasi), dan **5 `UNRESOLVED`** (`3.png`, `7.png`, `10.png`, `16.png`, `27.png`).
3. **Kesesuaian dengan Database Lokal `scanner.db`:**
   - Dari 26 simbol unik di folder `chart_analyzeimg`, hanya **1 simbol (`BTCUSDT` — `10.png` & `19.png`)** yang termasuk dalam 11 simbol *watchlist* utama yang tersimpan di `data/scanner.db` (`ADA, AVAX, BNB, BTC, DOGE, ETH, LINK, LTC, SOL, XMR, XRP`). Sebanyak 25 simbol lainnya adalah *altcoin/mid-cap* (`BAT, BTW, AAVE, SUPER, PEOPLE, NEAR, ONDO, FET, AVA, DUSK, Q, ZRO, USELESS, TAO, ONT, RENDER, ENA, GMT, HBAR, COTI, INJ, CTSI, ORCA, US, STRK`).

### 6.2 Rencana Eksperimen Selanjutnya (*Unmeasured / Planned Next Steps — Menunggu Persetujuan User*)

Sebelum mengubah anotasi visual apa pun menjadi aturan strategi otomatis di SignalGenerator, tahapan riset terukur yang direkomendasikan adalah:

1. **Tahap 1 — Backfill OHLCV Historis untuk 26 Simbol Dataset Visual (Read-Only Research Dataset):**
   - Mengunduh data historis `15m`, `30m`, `1h`, `4h`, dan `1d` (Oktober 2026) untuk ke-26 simbol pada `chart_dataset_manifest.csv` ke dalam tabel/database riset terpisah agar ke-20 sampel `NO_FILL` dan ke-7 sampel `UNRESOLVED` dapat diverifikasi nasib candle lanjutannya secara deterministik menggunakan [`SetupLabeler`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/ml/labeling.py).
2. **Tahap 2 — Formalisasi Aturan SL Deterministik untuk 21 Sampel Zona-Saja (Hanya untuk Eksperimen Model B):**
   - Menguji apakah penempatan SL berbasis aturan (`zone_bottom - 0.5 * ATR` untuk `LONG`, `zone_top + 0.5 * ATR` untuk `SHORT`) pada 21 sampel zona menghasilkan rencana yang lolos gerbang risiko [`PositionSizer`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/risk/sizing.py) (`max_loss <= $1.50`).
3. **Tahap 3 — Pengujian A/B Walk-Forward pada 11 Simbol Utama (365 Hari Historis):**
   - Mengkodekan ke-4 kandidat fitur visual (`has_diagonal_trendline_breakout`, `has_inducement_pool_ahead_of_zone`, `zone_confluence_score`, `htf_counter_trend_warning`) secara matematis di cabang riset (*offline benchmark*) dan membandingkan **Model A vs Model B** menggunakan ke-8 metrik Phase 5.

> [!NOTE]
> Sesuai instruksi Anda, **tidak ada aturan produksi SignalGenerator yang diubah**, tidak ada order yang dikirim, dan `live_trading_locked=True` serta `SCANNER_MODE=PAPER` tetap terjaga. Silakan tinjau ke-4 file laporan yang telah dibuat, dan beri tahu saya jika Anda menyetujui pelaksanaan salah satu tahapan eksperimen di atas.
