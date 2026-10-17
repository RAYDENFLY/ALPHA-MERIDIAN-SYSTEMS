# SignalGenerator — P0/P1 Remediation & Final Verification Audit Report

**Repository:** `C:\Users\Administrator\Documents\SignalGenerator`  
**Audit Timestamp:** `2026-10-17T19:30:00+07:00` (`12:30:00 UTC`)  
**Mode & Safety Invariants:** `SCANNER_MODE=PAPER` | `ExecutionMode.RESEARCH` (default) / `ExecutionMode.PAPER` (`.env`) | `live_trading_locked=True` | `max_leverage=10` | `target_isolated_margin_usdt=6.0` (`6.5` max) | `hard_risk_limit_usdt=1.50`

---

## A. Verifikasi Market Data

### 1. Regression Test `DatabaseManager.get_candles()`
- **Kode Aktual:** [`src/data/database.py`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/data/database.py#L281-L329) menggunakan parameterized subquery `SELECT ... FROM raw_candles WHERE ... ORDER BY timestamp_ms DESC LIMIT ?` yang dibungkus dengan outer query `ORDER BY timestamp_ms ASC`.
- **Command & Hasil Test:**
  ```powershell
  python -m pytest tests/test_p0_p1_remediation.py -k "test_get_candles_returns_newest_limit_in_chronological_order" -v
  ```
  - **Hasil:** `PASSED` — Membuktikan bahwa dari 10 candle berurutan (`indices 0..9`), pemanggilan `get_candles("BTCUSDT", "1h", limit=5)` mengembalikan tepat 5 candle **terbaru** (`indices 5, 6, 7, 8, 9`) dalam urutan kronologis (`oldest -> newest`), terisolasi penuh dari simbol (`ETHUSDT`) dan timeframe (`4h`) lain.

### 2. Hasil Eksekusi 1 Siklus Penuh Scanner (`ScannerRunner.run_scan`)
- **Command:** `python C:\Users\Administrator\.gemini\antigravity\brain\adae8d27-6351-440f-b44a-c1994272e623\scratch\run_final_audit_checks.py`
- **Ringkasan Siklus:**
  - `last_scan_status`: `SUCCESS`
  - `has_completed_scan`: `True`
  - `scan_count`: `1`
  - **Jumlah Simbol Berhasil / Gagal:** **11 berhasil / 0 gagal** (`ADAUSDT, XRPUSDT, BTCUSDT, BNBUSDT, ETHUSDT, SOLUSDT, AVAXUSDT, LINKUSDT, XMRUSDT, DOGEUSDT, LTCUSDT`)
  - **Kandidat Aktif Dihasilkan:** `0` (`NO_TRADE`)
- **Tabel Telemetri Candle Terakhir & Alasan Penolakan per Simbol (Tanpa Mengubah Filter Strategi):**

| Simbol | Status Sync & Urutan | Candle 1H Terakhir (Open UTC) | Candle 1H Terakhir (Close UTC) | Usia Data Saat Scan | Posisi Dealing Range 72H & Return (`12H / 48H / 7D`) | Alasan Setup Ditolak (`SetupGenerator`) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `ADAUSDT` | `OK` (Chrono) | `2026-10-17 11:00 UTC` | `2026-10-17 11:59 UTC` | `29.3m` sejak close (`89.3m` sejak open) | `pos72=0.33`, `r12=-1.15%`, `r48=+0.81%`, `r168=-12.63%` | Berada di mid-range 72H (`0.33` di antara batas discount `0.12` dan premium `0.88`). |
| `XRPUSDT` | `OK` (Chrono) | `2026-10-17 11:00 UTC` | `2026-10-17 11:59 UTC` | `29.3m` sejak close (`89.3m` sejak open) | `pos72=0.24`, `r12=-0.33%`, `r48=+0.07%`, `r168=-7.02%` | Berada di mid-range 72H (`0.24` di dalam `[0.12, 0.88]`). |
| `BTCUSDT` | `OK` (Chrono) | `2026-10-17 11:00 UTC` | `2026-10-17 11:59 UTC` | `29.3m` sejak close (`89.3m` sejak open) | `pos72=0.43`, `r12=-0.04%`, `r48=+0.75%`, `r168=-1.55%` | Berada di mid-range 72H (`0.43` di dalam `[0.12, 0.88]`). |
| `BNBUSDT` | `OK` (Chrono) | `2026-10-17 11:00 UTC` | `2026-10-17 11:59 UTC` | `29.3m` sejak close (`89.3m` sejak open) | `pos72=0.33`, `r12=-0.06%`, `r48=+0.03%`, `r168=-3.80%` | Berada di mid-range 72H (`0.33` di dalam `[0.12, 0.88]`). |
| `ETHUSDT` | `OK` (Chrono) | `2026-10-17 11:00 UTC` | `2026-10-17 11:59 UTC` | `29.3m` sejak close (`89.3m` sejak open) | `pos72=0.58`, `r12=+0.44%`, `r48=+3.01%`, `r168=-2.46%` | Berada di mid-range 72H (`0.58` di dalam `[0.12, 0.88]`). |
| `SOLUSDT` | `OK` (Chrono) | `2026-10-17 11:00 UTC` | `2026-10-17 11:59 UTC` | `29.3m` sejak close (`89.3m` sejak open) | `pos72=0.56`, `r12=+0.24%`, `r48=+2.46%`, `r168=-4.56%` | Berada di mid-range 72H (`0.56` di dalam `[0.12, 0.88]`). |
| `AVAXUSDT` | `OK` (Chrono) | `2026-10-17 11:00 UTC` | `2026-10-17 11:59 UTC` | `29.3m` sejak close (`89.3m` sejak open) | `pos72=0.52`, `r12=+0.05%`, `r48=+4.21%`, `r168=-5.84%` | Berada di mid-range 72H (`0.52` di dalam `[0.12, 0.88]`). |
| `LINKUSDT` | `OK` (Chrono) | `2026-10-17 11:00 UTC` | `2026-10-17 11:59 UTC` | `29.3m` sejak close (`89.3m` sejak open) | `pos72=0.28`, `r12=-1.43%`, `r48=+1.02%`, `r168=-5.91%` | Berada di mid-range 72H (`0.28` di dalam `[0.12, 0.88]`). |
| `XMRUSDT` | `OK` (Chrono) | `2026-10-17 11:00 UTC` | `2026-10-17 11:59 UTC` | `29.3m` sejak close (`89.3m` sejak open) | `pos72=0.69`, `r12=+0.01%`, `r48=+2.79%`, `r168=+6.13%` | Berada di mid-range 72H (`0.69` di dalam `[0.12, 0.88]`). |
| `DOGEUSDT` | `OK` (Chrono) | `2026-10-17 11:00 UTC` | `2026-10-17 11:59 UTC` | `29.3m` sejak close (`89.3m` sejak open) | `pos72=0.37`, `r12=-0.69%`, `r48=+0.10%`, `r168=-6.41%` | Berada di mid-range 72H (`0.37` di dalam `[0.12, 0.88]`). |
| `LTCUSDT` | `OK` (Chrono) | `2026-10-17 11:00 UTC` | `2026-10-17 11:59 UTC` | `29.3m` sejak close (`89.3m` sejak open) | `pos72=0.55`, `r12=-0.25%`, `r48=+2.09%`, `r168=-6.39%` | Berada di mid-range 72H (`0.55` di dalam `[0.12, 0.88]`). |

### 3. Verifikasi Scan Kosong Tidak Mengembalikan Kandidat Historis
- Di [`src/api/app.py`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/api/app.py#L469-L478), ketika `runner.has_completed_scan == True` dan `runner.last_results == []`, `GET /api/setups` menggunakan `raw_candidates = list(runner.last_results)` (`[]`) dan mengembalikan `{"status": "NO_TRADE", "count": 0, "setups": [], "has_completed_scan": True}`.
- Diverifikasi oleh `test_api_setups_never_scanned_vs_completed_empty_scan` (`PASSED`): meskipun kandidat aktif di-seed ke SQLite (`CAND_HIST_FALLBACK_TEST`), `GET /api/setups` tetap mengembalikan `count: 0` setelah scan selesai dengan hasil kosong.

---

## B. Verifikasi Sizing dan Risk

### 1. Hasil Aktual `PositionSizer.calculate_sizing()` pada Stop Distance `1.5%`, `2.3%`, `2.45%`, dan `3.5%` (LONG & SHORT)
Parameter uji: `Entry = $100.00`, `Target Isolated Margin = $6.00` (`Max $6.50`), `Max Leverage = 10x`, `Hard SL Risk Limit = $1.50`, `Fee Rate = 0.05%/side`, `Slippage Rate = 0.05%/side`, `step_size = 0.01`, `min_qty = 0.01`, `min_notional = $5.00`.

| Arah | Stop Distance | Entry Price | Stop Loss | Qty (Rounded) | Margin Aktual (USDT) | Notional (USDT) | Estimasi Fee (USDT) | Estimasi Slippage (USDT) | Estimasi Loss saat SL (USDT) | Keputusan & Alasan |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **LONG** | **1.50%** | `$100.00` | `$98.5000` | `0.60` | **`$6.00`** | `$60.00` | `$0.0595` | `$0.0595` | **`$1.0191`** ($\le \$1.50$) | **FEASIBLE** (Full `$6.00` margin) |
| **LONG** | **2.30%** | `$100.00` | `$97.7000` | `0.60` | **`$6.00`** | `$60.00` | `$0.0593` | `$0.0593` | **`$1.4986`** ($\le \$1.50$) | **FEASIBLE** (Full `$6.00` margin; exit notional `$58.62` membuat fee+slip sedikit lebih rendah dari `$0.12`) |
| **LONG** | **2.45%** | `$100.00` | `$97.5500` | `0.56` | **`$5.60`** | `$56.00` | `$0.0553` | `$0.0553` | **`$1.4826`** ($\le \$1.50$) | **FEASIBLE** (Step-down otomatis dari `0.60` ke `0.56` lot agar loss tidak melewati `$1.50`) |
| **LONG** | **3.50%** | `$100.00` | `$96.5000` | `0.60` | `$6.00` | `$60.00` | `$0.0590` | `$0.0590` | **`$2.2179`** ($> \$1.50$) | **INFEASIBLE** (`Order produces estimated loss at SL of $2.22 (13.9% of equity), which exceeds hard risk limit of $1.50.`) |
| **SHORT** | **1.50%** | `$100.00` | `$101.5000` | `0.60` | **`$6.00`** | `$60.00` | `$0.0605` | `$0.0605` | **`$1.0209`** ($\le \$1.50$) | **FEASIBLE** (Full `$6.00` margin) |
| **SHORT** | **2.30%** | `$100.00` | `$102.3000` | `0.59` | **`$5.90`** | `$59.00` | `$0.0597` | `$0.0597` | **`$1.4764`** ($\le \$1.50$) | **FEASIBLE** (Pada SHORT 2.30%, exit notional di `$102.30` lebih besar sehingga pada `$60` notional total loss `$1.5014 > $1.50`; sistem otomatis step-down 1 lot ke `0.59` / `$5.90` margin sehingga loss `$1.4764 \le \$1.50`) |
| **SHORT** | **2.45%** | `$100.00` | `$102.4500` | `0.56` | **`$5.60`** | `$56.00` | `$0.0567` | `$0.0567` | **`$1.4854`** ($\le \$1.50$) | **FEASIBLE** (Step-down otomatis ke `0.56` lot / `$5.60` margin agar loss $\le \$1.50$) |
| **SHORT** | **3.50%** | `$100.00` | `$103.5000` | `0.60` | `$6.00` | `$60.00` | `$0.0610` | `$0.0610` | **`$2.2221`** ($> \$1.50$) | **INFEASIBLE** (`Order produces estimated loss at SL of $2.22 (13.9% of equity), which exceeds hard risk limit of $1.50.`) |

### 2. Verifikasi Daily (`$0.18`) & Weekly (`$0.54`) Risk Guard
- [`RiskManager`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/risk/manager.py#L60-L90) mempertahankan `daily_loss_limit_usdt = 0.18`, `weekly_loss_limit_usdt = 0.54`, `max_open_positions = 1`, dan `max_entries_per_week = 3`.
- Ketika akumulasi loss harian mencapai `$0.18` (atau mingguan `$0.54`):
  1. `RiskManager.get_effective_trade_risk()` mengembalikan `0.0`.
  2. `PositionSizer.calculate_sizing(..., risk_budget_usdt=0.0)` langsung menolak sizing sebagai **`INFEASIBLE`** (`Effective risk budget exhausted ($0.00 remaining). Trade is INFEASIBLE.`).
  3. `RiskManager.can_open_new_position()` mengembalikan `(False, "Daily loss limit ($0.18) reached...")`, yang mencegah promosi ke `READY` di [`ScannerRunner.run_scan()`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/scheduler/runner.py#L388-L396) dan memblokir eksekusi di [`PaperExecutionEngine.submit_candidate()`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/execution/paper_engine.py#L162-L176).

---

## C. Verifikasi Security

### 1. Audit Seluruh Endpoint Mutasi & Cara Menjalankan Aplikasi
- **Daftar Seluruh Endpoint Mutasi (`POST`) di [`src/api/app.py`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/api/app.py):**
  1. `POST /api/discord/test` ([line 370](file:///C:/Users/Administrator/Documents/SignalGenerator/src/api/app.py#L370)) — `dependencies=[Depends(require_mutating_api_access)]`
  2. `POST /api/scanner/start` ([line 399](file:///C:/Users/Administrator/Documents/SignalGenerator/src/api/app.py#L399)) — `dependencies=[Depends(require_mutating_api_access)]`
  3. `POST /api/scanner/stop` ([line 413](file:///C:/Users/Administrator/Documents/SignalGenerator/src/api/app.py#L413)) — `dependencies=[Depends(require_mutating_api_access)]`
  4. `POST /api/symbols/{symbol}/toggle` ([line 461](file:///C:/Users/Administrator/Documents/SignalGenerator/src/api/app.py#L461)) — `dependencies=[Depends(require_mutating_api_access)]`
  5. `POST /api/scan` ([line 725](file:///C:/Users/Administrator/Documents/SignalGenerator/src/api/app.py#L725)) — `dependencies=[Depends(require_mutating_api_access)]`
  - Tidak ada endpoint `PUT`, `DELETE`, atau `PATCH` di aplikasi.
- **Semua Cara Menjalankan Aplikasi & Default Binding:**
  - [`config/settings.py`](file:///C:/Users/Administrator/Documents/SignalGenerator/config/settings.py#L132): `host: str = Field(default="127.0.0.1")`
  - `.env`: `HOST=127.0.0.1`, `PORT=8000`
  - [`.env.example`](file:///C:/Users/Administrator/Documents/SignalGenerator/.env.example#L30): `HOST=127.0.0.1`, `PORT=8000`
  - [`src/api/app.py`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/api/app.py#L902-L905): `if __name__ == "__main__": uvicorn.run("src.api.app:app", host=cfg.host, port=cfg.port, reload=False)` -> bind ke `127.0.0.1:8000`.

### 2. Bukti Penolakan Request Tidak Berwenang (`test_security_remediation_controls`)
Diverifikasi di [`tests/test_p0_p1_remediation.py`](file:///C:/Users/Administrator/Documents/SignalGenerator/tests/test_p0_p1_remediation.py#L314-L390) pada kelima endpoint mutasi:
- **Origin Palsu (`Origin: https://evil.example.com`)**: Ditolak dengan **HTTP 403 Forbidden** pada semua 5 endpoint mutasi.
- **Referer Palsu (`Referer: https://evil.example.com/attack.html`)**: Ditolak dengan **HTTP 403 Forbidden** pada semua 5 endpoint mutasi.
- **Akses dari Non-Loopback (`client.host = "192.168.1.99"`) tanpa Token**: Ditolak dengan **HTTP 403 Forbidden**.
- **Tanpa Origin & Tanpa Token (saat `SCANNER_API_TOKEN` aktif)**: Ditolak dengan **HTTP 401 Unauthorized** pada semua 5 endpoint mutasi.
- **Token Salah (`X-Scanner-Token: wrong-token`)**: Ditolak dengan **HTTP 401 Unauthorized** pada semua 5 endpoint mutasi.
- **Token Valid (`X-Scanner-Token` atau `Authorization: Bearer`) dari Loopback**: Diterima dengan **HTTP 200 OK**.

### 3. Proteksi `.env`, Kredensial, Log, dan API Response
- [`.gitignore`](file:///C:/Users/Administrator/Documents/SignalGenerator/.gitignore) melindungi `.env`, `.env.*` (kecuali `!.env.example`), `*.db`, `*.db-wal`, `*.db-shm`, dan `logs/`.
- [`AsterDexAdapter`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/exchange/aster.py#L31-L44) memiliki `load_env_credentials: bool = False` secara default dan diinisialisasi di [`src/api/app.py`](file:///C:/Users/Administrator/Documents/SignalGenerator/src/api/app.py#L36-L44) dengan `api_key=None, api_secret=None, load_env_credentials=False`, sehingga kredensial `.env` tidak dimuat ke memori proses scanner.
- Pemeriksaan seluruh endpoint GET (`/api/status`, `/api/setups`, `/api/symbols`, `/api/journal`, `/api/trades`, `/api/ai/status`) membuktikan **`API_SECRET_LEAKS: 0`** (URL webhook Discord di-masking melalui `mask_webhook_url()`, dan tidak ada API key/private key yang muncul di respons API maupun log).

---

## D. Verifikasi PAPER / Live Isolation

Telusur call graph lengkap membuktikan tidak ada jalur eksekusi order nyata dari aplikasi:
1. **Dashboard (`src/api/dashboard.html`) -> FastAPI (`src/api/app.py`)**:
   - Hanya memanggil `GET /api/status`, `GET /api/setups`, `GET /api/trades`, `GET /api/journal`, `GET /api/backtest`, serta `POST /api/scan`, `POST /api/symbols/{symbol}/toggle`, dan `POST /api/discord/test`. Tidak ada endpoint API untuk mengirim order ke exchange.
2. **Scheduler (`ScannerRunner.run_scan` di `src/scheduler/runner.py`)**:
   - Hanya memanggil `synchronizer.sync_symbol()` (`GET /fapi/v1/klines`, publik) dan `db.get_funding_rates()`.
   - Jika `self.mode == ExecutionMode.PAPER`, kandidat `READY` hanya dikirim ke `self.paper_engine.submit_candidate(cand, now_ms)`.
3. **Paper Execution Engine (`PaperExecutionEngine` di `src/execution/paper_engine.py`)**:
   - Tidak memiliki referensi atau dependency ke `AsterDexAdapter` maupun `AsterDexV3Adapter`. Seluruh pembuatan order, fill, SL/TP, dan pencatatan PnL dilakukan murni pada state lokal dan tabel SQLite `paper_orders` & `trade_journal`.
4. **Discord Dispatcher (`PaperEventDispatcher` di `src/execution/events.py`)**:
   - Murni outbound observer (`POST` ke webhook Discord); tidak menerima perintah dan tidak memiliki akses ke adapter exchange.
5. **Lock Konfigurasi & Adapter Exchange (`config/settings.py`, `src/exchange/aster_v3.py`, `src/execution/v3_lifecycle.py`)**:
   - `AppConfig.guard_live_mode` melempar `ValueError("LIVE mode is permanently locked in this phase. Use RESEARCH or PAPER.")` jika `ExecutionMode.LIVE` dipilih.
   - `AsterDexV3Adapter.create_order()`, `cancel_order()`, dan `cancel_all_open_orders()` menolak eksekusi mainnet secara permanen (`if not self.is_testnet: raise ExchangeAPIError("Live trading is permanently locked...")`) dan memblokir penggunaan alamat wallet mainnet pada profil testnet.

---

## E. Keputusan Kesiapan Sistem (Readiness Decisions)

| # | Kategori Kesiapan | Keputusan | Bukti Test & Runtime | Blocker Tersisa |
| :-: | :--- | :---: | :--- | :--- |
| **1** | **Unit / Deterministic Simulation Readiness** | **PASS** | `python -m pytest -q --tb=short` -> **`251 passed, 0 failed`** (`40.94s`), mencakup `test_p0_p1_remediation.py`, `test_tpsl_risk.py`, `test_paper_engine.py`, `test_smc_structure.py`, dan `test_strategy_generator.py`. | Tidak ada. |
| **2** | **Historical Replay Readiness** | **PASS** | `tests/test_backtest_engine.py` (`3 passed`), `tests/test_data_validation.py` (`11 passed`), dan `test_get_candles_returns_newest_limit_in_chronological_order` (`PASSED`). `BacktestEngine` telah menggunakan `$6.00` isolated margin (`$6.50` max) dan `risk_budget_usdt` dengan zero-lookahead `confirmed_timestamp_ms`. | Tidak ada untuk replay deterministik. (Catatan riset: strategi saat ini masih menunjukkan expectancy historis negatif sebelum filter/parameter dioptimalkan lebih lanjut). |
| **3** | **Forward Paper Testing Readiness** | **PASS** | Siklus live scan `ScannerRunner.run_scan()` berhasil menyinkronkan 11/11 simbol dari AsterDEX (`last_scan_status="SUCCESS"`, usia candle zamknięty `29.3m`), `GET /api/setups` jujur menampilkan `NO_TRADE` saat 0 kandidat memenuhi syarat, `PaperExecutionEngine` memulihkan state SQLite tanpa error, dan semua batas risiko (`$6` margin, `10x` leverage, `$1.50` hard SL cap, `$0.18` daily / `$0.54` weekly loss limit) aktif. | Tidak ada blocker teknis; siap dijalankan untuk forward paper testing pada `SCANNER_MODE=PAPER`. |
| **4** | **TP1-to-Trailing-Stop Readiness** | **FAIL (NOT IMPLEMENTED)** | Sesuai aturan scope remediasi P0/P1, logika partial close di TP1, pemindahan SL ke break-even pasca-TP1, dan trailing stop **belum diimplementasikan (`NOT IMPLEMENTED`)** di `PaperExecutionEngine` maupun `V3OrderLifecycleManager`. | Perlu spesifikasi & implementasi state machine `TP1_PARTIAL_AND_TRAIL` di `PaperExecutionEngine` serta verifikasi paper trade penuh sebelum mengaktifkan trailing stop. |
| **5** | **Live Trading Readiness** | **FAIL (LOCKED / NOT READY)** | `live_trading_locked=True` aktif secara permanen; `ExecutionMode.LIVE` diblokir oleh validator `AppConfig`; `AsterDexV3Adapter` memblokir semua order mainnet. | 1) Live trading sengaja dikunci (`live_trading_locked=True`).<br>2) Belum ada bukti forward paper trading multi-minggu dengan expectancy positif.<br>3) Kredensial `ASTER_TESTNET_*` belum dikonfigurasi untuk uji siklus order di Aster Futures Testnet (Chain ID 714). |
