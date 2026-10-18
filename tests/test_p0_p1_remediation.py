import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from config.settings import get_default_config
from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    MarketBias,
    SetupCandidate,
    SnRZone,
    StructureState,
    SwingPoint,
    SwingType,
    Timeframe,
    TradeRecord,
)
from src.data.database import DatabaseManager
from src.exchange.aster import AsterDexAdapter
from src.exchange.metadata import SymbolSpecs
from src.risk.manager import RiskManager
from src.risk.sizing import PositionSizer
from src.strategy.planner import TPSLPlanner
import src.api.app as app_module


# ==============================================================================
# P0-1: DatabaseManager.get_candles() Newest N Candles in Chronological Order
# ==============================================================================

def test_get_candles_returns_newest_limit_in_chronological_order(tmp_path):
    db_file = tmp_path / "test_candles.db"
    db = DatabaseManager(db_file)

    base_ts = 1_700_000_000_000
    candles = []
    for i in range(10):
        ts = base_ts + i * 3_600_000
        candles.append(
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.H1.value,
                timestamp_ms=ts,
                open=50000.0 + i * 10,
                high=50050.0 + i * 10,
                low=49950.0 + i * 10,
                close=50010.0 + i * 10,
                volume=100.0 + i,
                close_time_ms=ts + 3_599_999,
                is_closed=True,
            )
        )
    # Also insert another symbol and timeframe to verify strict isolation
    for i in range(5):
        ts = base_ts + i * 3_600_000
        candles.append(
            Candle(
                symbol="ETHUSDT",
                timeframe=Timeframe.H1.value,
                timestamp_ms=ts,
                open=3000.0,
                high=3010.0,
                low=2990.0,
                close=3005.0,
                volume=50.0,
                close_time_ms=ts + 3_599_999,
                is_closed=True,
            )
        )
        candles.append(
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.H4.value,
                timestamp_ms=ts,
                open=50000.0,
                high=50100.0,
                low=49900.0,
                close=50020.0,
                volume=400.0,
                close_time_ms=ts + 14_399_999,
                is_closed=True,
            )
        )

    db.insert_candles(candles)

    # 1. With limit=5, must return the NEWEST 5 BTCUSDT 1h candles (indices 5, 6, 7, 8, 9)
    # ordered oldest -> newest
    recent_5 = db.get_candles("BTCUSDT", Timeframe.H1.value, limit=5)
    assert len(recent_5) == 5
    expected_timestamps = [base_ts + i * 3_600_000 for i in range(5, 10)]
    assert [c.timestamp_ms for c in recent_5] == expected_timestamps
    assert all(c.symbol == "BTCUSDT" and c.timeframe == Timeframe.H1.value for c in recent_5)

    # 2. Without limit, must return all 10 candles in chronological order
    all_10 = db.get_candles("BTCUSDT", Timeframe.H1.value, limit=None)
    assert len(all_10) == 10
    assert [c.timestamp_ms for c in all_10] == [base_ts + i * 3_600_000 for i in range(10)]

    # 3. With start_time_ms, end_time_ms, and limit=3, must return the newest 3 within range [idx 2..7] -> [5, 6, 7]
    ranged_3 = db.get_candles(
        "BTCUSDT",
        Timeframe.H1.value,
        start_time_ms=base_ts + 2 * 3_600_000,
        end_time_ms=base_ts + 7 * 3_600_000,
        limit=3,
    )
    assert [c.timestamp_ms for c in ranged_3] == [base_ts + i * 3_600_000 for i in range(5, 8)]


# ==============================================================================
# P0-2: GET /api/setups Empty Scan vs Pre-Scan Fallback & Scan State Telemetry
# ==============================================================================

def _make_dummy_candidate(candidate_id: str, symbol: str = "SOLUSDT") -> SetupCandidate:
    now_ms = int(time.time() * 1000)
    return SetupCandidate(
        candidate_id=candidate_id,
        symbol=symbol,
        timestamp_ms=now_ms - 60_000,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=149.0,
        stop_loss=146.5,
        take_profit_1=154.0,
        gross_reward_to_risk=2.0,
        net_reward_to_risk=1.8,
        invalidation_price=146.5,
        invalidation_reason="Below swing low",
        status=CandidateStatus.WATCHLIST,
        expires_at_ms=now_ms + 3_600_000,
    )


def test_api_setups_never_scanned_vs_completed_empty_scan():
    with TestClient(app_module.app) as client:
        orig_results = list(app_module.runner.last_results)
        orig_completed = app_module.runner.has_completed_scan
        orig_scan_count = app_module.runner.scan_count
        orig_scan_status = app_module.runner.last_scan_status
        orig_symbols = list(app_module.runner.last_symbols_attempted)
        orig_error = app_module.runner.last_error

        try:
            # Seed a non-expired candidate in the journal DB
            seeded = _make_dummy_candidate("CAND_HIST_FALLBACK_TEST", "SOLUSDT")
            app_module.journal.save_setup(seeded)

            # Case 1: Runner has completed a scan and found 0 setups -> MUST return 0 setups (NO_TRADE),
            # NOT resurrect historical DB rows!
            app_module.runner.has_completed_scan = True
            app_module.runner.scan_count = 3
            app_module.runner.last_scan_status = "COMPLETED_EMPTY"
            app_module.runner.last_symbols_attempted = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
            app_module.runner.last_results = []
            app_module.runner.last_error = None

            res_empty = client.get("/api/setups")
            assert res_empty.status_code == 200
            body_empty = res_empty.json()
            assert body_empty["count"] == 0
            assert body_empty["setups"] == []
            assert body_empty["has_completed_scan"] is True
            assert body_empty["scan_count"] == 3
            assert body_empty["last_scan_status"] == "COMPLETED_EMPTY"
            assert body_empty["symbols_attempted"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

            # Case 2: Explicit include_historical=True allows inspecting historical DB rows
            res_hist = client.get("/api/setups?include_historical=true")
            assert res_hist.status_code == 200
            body_hist = res_hist.json()
            assert any(s["candidate_id"] == "CAND_HIST_FALLBACK_TEST" for s in body_hist["setups"])

            # Case 3: Runner has completed a scan with non-empty setups -> returns live scan results
            live_cand = _make_dummy_candidate("CAND_LIVE_SCAN_TEST", "BTCUSDT")
            app_module.runner.last_results = [live_cand]
            app_module.runner.last_scan_status = "COMPLETED_WITH_SETUPS"

            res_live = client.get("/api/setups")
            assert res_live.status_code == 200
            body_live = res_live.json()
            assert body_live["count"] == 1
            assert body_live["setups"][0]["candidate_id"] == "CAND_LIVE_SCAN_TEST"
            assert body_live["last_scan_status"] == "COMPLETED_WITH_SETUPS"

            # Case 4: Failed scan telemetry in /api/status and /api/setups
            app_module.runner.scanner_state = "ERROR"
            app_module.runner.last_scan_status = "FAILED"
            app_module.runner.last_error = "Simulated network timeout"
            res_status = client.get("/api/status")
            assert res_status.status_code == 200
            st_json = res_status.json()
            assert st_json["scanner"]["has_completed_scan"] is True
            assert st_json["scanner"]["last_scan_status"] == "FAILED"
            assert st_json["scanner"]["last_error"] == "Simulated network timeout"

        finally:
            with app_module.db.get_connection() as conn:
                conn.execute("DELETE FROM setup_candidates WHERE candidate_id = ?", ("CAND_HIST_FALLBACK_TEST",))
                conn.commit()
            app_module.runner.last_results = orig_results
            app_module.runner.has_completed_scan = orig_completed
            app_module.runner.scan_count = orig_scan_count
            app_module.runner.last_scan_status = orig_scan_status
            app_module.runner.last_symbols_attempted = orig_symbols
            app_module.runner.last_error = orig_error
            app_module.runner.scanner_state = "IDLE"


# ==============================================================================
# P0-3: $6.00 Target Isolated Margin & Risk Step-Down / Hard Cap Enforcement
# ==============================================================================

def test_target_isolated_margin_6_usdt_and_risk_step_down():
    cfg = get_default_config()
    assert cfg.risk.target_isolated_margin_usdt == 1.0
    assert cfg.risk.target_isolated_margin_max_usdt == 1.5
    assert cfg.risk.hard_risk_limit_usdt == 1.50
    assert cfg.risk.max_leverage == 10

    specs = SymbolSpecs(
        symbol="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        contract_type="PERPETUAL",
        status="TRADING",
        price_precision=2,
        quantity_precision=2,
        tick_size=0.01,
        step_size=0.01,
        min_qty=0.01,
        max_qty=10000.0,
        min_notional=5.0,
        maint_margin_percent=2.5,
    )

    # Test stop distances 1.5%, 2.3%, 2.45%, and 3.5% for both LONG and SHORT under 6.0 margin step-down
    stop_distances = [0.015, 0.023, 0.0245, 0.035]
    for direction in (Direction.LONG, Direction.SHORT):
        for d in stop_distances:
            entry_p = 100.0
            sl_p = round(entry_p * (1.0 - d), 4) if direction == Direction.LONG else round(entry_p * (1.0 + d), 4)
            ra = PositionSizer.calculate_sizing(
                symbol_specs=specs,
                entry_price=entry_p,
                stop_loss=sl_p,
                target_isolated_margin=6.0,
                target_isolated_margin_max=6.5,
                direction=direction,
            )
            if d == 0.015:
                # Full $6.00 margin ($60 notional) is feasible for both LONG and SHORT
                assert ra.is_feasible is True
                assert abs(ra.actual_margin_usdt - 6.0) < 1e-6
                assert abs(ra.position_notional_usdt - 60.0) < 1e-6
                assert ra.estimated_loss_usdt <= 1.50
            elif d == 0.023:
                # At 2.3% stop distance:
                # LONG exit notional is 97.70% of entry ($1.49862 <= $1.50) -> full $6.00 margin
                # SHORT exit notional is 102.30% of entry ($1.50138 > $1.50 at $60) -> steps down 1 lot to $5.90 margin ($1.476357 <= $1.50)
                assert ra.is_feasible is True
                assert ra.estimated_loss_usdt <= 1.50
                if direction == Direction.LONG:
                    assert abs(ra.actual_margin_usdt - 6.0) < 1e-6
                else:
                    assert abs(ra.actual_margin_usdt - 5.9) < 1e-6
            elif d == 0.0245:
                # Stepped down from $6.00 to ~$5.60 margin ($56 notional) so loss <= $1.50
                assert ra.is_feasible is True
                assert 4.50 <= ra.actual_margin_usdt < 6.00
                assert ra.estimated_loss_usdt <= 1.50
            elif d == 0.035:
                # 3.5% stop distance requires margin < $4.50 to stay under $1.50 cap -> INFEASIBLE
                assert ra.is_feasible is False
                assert ra.infeasible_reason is not None
                assert "exceeds hard risk limit" in ra.infeasible_reason

    # Verify daily ($0.18) and weekly ($0.54) risk guards cannot be bypassed
    rm = RiskManager(
        initial_equity_usdt=16.0,
        trade_risk_usdt=0.09,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
        max_open_positions=1,
        max_entries_per_week=3,
    )
    now_ms = 1_700_000_000_000
    t1 = TradeRecord(
        trade_id="T_LOSS_1",
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=now_ms,
        entry_price=100.0,
        stop_loss=98.5,
        take_profit=103.0,
        quantity=0.60,
        notional_usdt=60.0,
        is_open=False,
        exit_timestamp_ms=now_ms + 60_000,
        exit_price=99.7,
        pnl_net=-0.18,
    )
    rm.record_entry(t1)
    rm.record_exit(t1)
    can_open_daily, reason_daily = rm.can_open_new_position(now_ms + 120_000)
    assert can_open_daily is False
    assert "Daily loss limit ($0.18) reached" in (reason_daily or "")

    eff_risk = rm.get_effective_trade_risk(now_ms + 120_000)
    assert eff_risk == 0.0
    ra_blocked = PositionSizer.calculate_sizing(
        symbol_specs=specs,
        entry_price=100.0,
        stop_loss=98.5,
        risk_budget_usdt=eff_risk,
    )
    assert ra_blocked.is_feasible is False
    assert "risk budget exhausted" in (ra_blocked.infeasible_reason or "").lower()


# ==============================================================================
# P1-4: Security Remediation (.gitignore, Host Binding, Token Auth, Origin, Non-Loopback)
# ==============================================================================

def test_security_remediation_controls():
    # 1. Verify .gitignore exists and covers sensitive patterns
    gitignore_path = Path(__file__).resolve().parent.parent / ".gitignore"
    assert gitignore_path.exists()
    gitignore_text = gitignore_path.read_text(encoding="utf-8")
    for pattern in [".env", "*.db", "*.db-wal", "*.db-shm", "logs/"]:
        assert pattern in gitignore_text

    # 2. Verify default host binding is 127.0.0.1
    cfg = get_default_config()
    assert cfg.host == "127.0.0.1"

    # 3. Verify AsterDexAdapter does NOT load ASTER_API_KEY / ASTER_API_SECRET by default
    with patch.dict("os.environ", {"ASTER_API_KEY": "secret_key_123", "ASTER_API_SECRET": "secret_val_456"}):
        adapter_default = AsterDexAdapter()
        assert not adapter_default.api_key
        assert not adapter_default.api_secret

        adapter_explicit = AsterDexAdapter(load_env_credentials=True)
        assert adapter_explicit.api_key == "secret_key_123"
        assert adapter_explicit.api_secret == "secret_val_456"

    # 4. Verify mutating POST endpoints enforce Origin, loopback, and SCANNER_API_TOKEN
    mutating_routes = [
        ("/api/scan", {}),
        ("/api/scanner/start", {}),
        ("/api/scanner/stop", {}),
        ("/api/symbols/BTCUSDT/toggle", {"enabled": True}),
        ("/api/discord/test", {}),
    ]

    with TestClient(app_module.app) as client:
        orig_token = app_module.cfg.scanner_api_token
        try:
            # 4a. Forged Origin or Referer rejected with 403 Forbidden across all mutating endpoints
            app_module.cfg.scanner_api_token = None
            for path, payload in mutating_routes:
                res_evil_origin = client.post(
                    path,
                    json=payload if payload else None,
                    headers={"Origin": "https://evil.example.com"},
                )
                assert res_evil_origin.status_code == 403, f"Expected 403 on {path} with forged Origin"

                res_evil_ref = client.post(
                    path,
                    json=payload if payload else None,
                    headers={"Referer": "https://evil.example.com/attack.html"},
                )
                assert res_evil_ref.status_code == 403, f"Expected 403 on {path} with forged Referer"

            # 4b. Non-loopback client rejected with 403 Forbidden when no token is configured
            mock_req = MagicMock()
            mock_req.client.host = "192.168.1.99"
            try:
                app_module.require_mutating_api_access(
                    request=mock_req,
                    x_scanner_token=None,
                    authorization=None,
                    origin=None,
                    referer=None,
                )
                assert False, "Non-loopback client should have raised HTTPException(403)"
            except Exception as exc:
                assert getattr(exc, "status_code", None) == 403

            # 4c. When SCANNER_API_TOKEN is configured, missing or wrong token rejected with 401
            app_module.cfg.scanner_api_token = "test-secret-scanner-token"

            for path, payload in mutating_routes:
                # Without token (and without Origin) -> 401 Unauthorized
                res_unauth = client.post(path, json=payload if payload else None)
                assert res_unauth.status_code == 401, f"Expected 401 on {path} without token"

                # With wrong token -> 401 Unauthorized
                res_wrong = client.post(
                    path,
                    json=payload if payload else None,
                    headers={"X-Scanner-Token": "wrong-token"},
                )
                assert res_wrong.status_code == 401, f"Expected 401 on {path} with wrong token"

            # With valid X-Scanner-Token -> 200 OK
            res_ok = client.post("/api/scanner/stop", headers={"X-Scanner-Token": "test-secret-scanner-token"})
            assert res_ok.status_code == 200

            # With valid Authorization: Bearer <token> -> 200 OK
            res_bearer = client.post(
                "/api/scanner/stop",
                headers={"Authorization": "Bearer test-secret-scanner-token"},
            )
            assert res_bearer.status_code == 200
        finally:
            app_module.cfg.scanner_api_token = orig_token


# ==============================================================================
# P1-5: TPSLPlanner Confirmed Swings & Opposing Structure Wall Wiring
# ==============================================================================

def test_tpsl_planner_receives_confirmed_swings_and_opposing_walls():
    # 1. Opposing resistance wall closer than 1.2R rejects LONG setup
    resistance_wall = SnRZone(
        zone_id="RES_1",
        price_center=101.5,
        price_low=101.2,
        price_high=101.8,
    )
    sl_1, tp1_1, tp2_1, _, _ = TPSLPlanner.plan_trade_levels(
        direction=Direction.LONG,
        entry_price=100.0,
        invalidation_price=98.5,
        atr=1.0,
        swings=[],
        resistance=resistance_wall,
        support=None,
    )
    assert sl_1 is None and tp1_1 is None

    # 2. Opposing support wall closer than 1.2R rejects SHORT setup
    support_wall = SnRZone(
        zone_id="SUP_1",
        price_center=98.5,
        price_low=98.2,
        price_high=98.8,
    )
    sl_2, tp1_2, tp2_2, _, _ = TPSLPlanner.plan_trade_levels(
        direction=Direction.SHORT,
        entry_price=100.0,
        invalidation_price=101.5,
        atr=1.0,
        swings=[],
        resistance=None,
        support=support_wall,
    )
    assert sl_2 is None and tp1_2 is None

    # 3. Valid structural swing high within [1.5R, 2.5R] is selected as TP1
    valid_swing_high = SwingPoint(
        index=10,
        timestamp_ms=1_700_000_000_000,
        confirmed_index=13,
        confirmed_timestamp_ms=1_700_000_010_000,
        price=104.2,
        swing_type=SwingType.HIGH,
    )
    sl_3, tp1_3, tp2_3, gross_rr_3, net_rr_3 = TPSLPlanner.plan_trade_levels(
        direction=Direction.LONG,
        entry_price=100.0,
        invalidation_price=98.5,
        atr=1.0,
        swings=[valid_swing_high],
        resistance=None,
        support=None,
    )
    assert sl_3 == 98.0
    assert tp1_3 == 104.2
    assert gross_rr_3 > 2.0
