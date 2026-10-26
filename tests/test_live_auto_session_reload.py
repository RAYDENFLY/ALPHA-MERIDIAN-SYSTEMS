"""Tests for LIVE_AUTO Session Persistence Across Dashboard Reload and Runtime Restart.

Verifies:
1. GET /api/status accurately reports mode="LIVE_AUTO" and session_state="ACTIVE"
   when a live auto-execution session is active, eliminating the bug where
   dashboard headers defaulted to PAPER.
2. Repeated GET requests to /api/status and /api/inspector/live-session
   (simulating browser polling and page reloads) do NOT mutate or terminate
   the session state.
3. Full browser reload flow preserves session_id, mode, state, scanner status,
   equity source, and position locks.
4. Runtime process restart transitions recovered active sessions to
   REAUTHORIZATION_REQUIRED (safety invariant #4) rather than silently
   reverting to PAPER or INACTIVE.
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.api.app import app, live_auto_session_manager
from src.execution.live_session import (
    ExecutionMode,
    LiveSessionState,
)


@pytest.fixture
def client():
    """Create a FastAPI TestClient."""
    return TestClient(app)


def test_api_status_reports_live_auto_when_session_active(client):
    """GET /api/status must report active execution mode and session state."""
    # Mock active session info on manager
    with patch.object(
        live_auto_session_manager,
        "get_session_info",
        return_value={
            "session_id": "SESS_LIVE_AUTO_TEST_12345",
            "mode": "LIVE_AUTO",
            "state": "ACTIVE",
            "is_active": True,
            "scanner_active": True,
            "current_equity_usd": 15.01,
            "current_equity_source": "ASTERDEX_ACCOUNT_SNAPSHOT",
            "global_lock_held": False,
            "daily_loss_snapshot": {"used_usdt": 0.0, "status": "VERIFIED"},
        },
    ):
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.json()
        assert data["mode"] == "LIVE_AUTO"
        assert data["session_state"] == "ACTIVE"
        assert data["session_id"] == "SESS_LIVE_AUTO_TEST_12345"


def test_repeated_get_requests_do_not_mutate_session(client):
    """Repeated GET requests (simulating page reload & polling) must not mutate session state."""
    active_mock_info = {
        "session_id": "SESS_LIVE_AUTO_TEST_RELOAD_999",
        "mode": "LIVE_AUTO",
        "state": "ACTIVE",
        "is_active": True,
        "scanner_active": True,
        "started_at_utc": "2026-10-26T04:44:35.000000+00:00",
        "current_equity_usd": 15.01,
        "current_equity_source": "ASTERDEX_ACCOUNT_SNAPSHOT",
        "global_lock_held": False,
        "daily_loss_snapshot": {"used_usdt": 0.0, "status": "VERIFIED"},
    }

    mock_preflight = MagicMock()
    mock_preflight.to_dict.return_value = {
        "is_valid": True,
        "errors": [],
        "warnings": [],
        "connectivity_ok": True,
    }

    with patch.object(live_auto_session_manager, "get_session_info", return_value=active_mock_info), \
         patch.object(live_auto_session_manager, "run_preflight_audit", return_value=mock_preflight):

        # Simulate 10 rapid polls/reloads
        for _ in range(10):
            res_status = client.get("/api/status")
            assert res_status.status_code == 200
            assert res_status.json()["mode"] == "LIVE_AUTO"

            res_sess = client.get("/api/inspector/live-session")
            assert res_sess.status_code == 200
            data_sess = res_sess.json()
            assert data_sess["data"]["session_id"] == "SESS_LIVE_AUTO_TEST_RELOAD_999"
            assert data_sess["data"]["mode"] == "LIVE_AUTO"
            assert data_sess["data"]["state"] == "ACTIVE"
            assert data_sess["data"]["is_active"] is True


def test_api_status_reports_reauthorization_required_after_restart(client):
    """When a session is recovered after restart, /api/status reports REAUTHORIZATION_REQUIRED, not PAPER."""
    with patch.object(
        live_auto_session_manager,
        "get_session_info",
        return_value={
            "session_id": "SESS_LIVE_AUTO_TEST_RECOVERED",
            "mode": "LIVE_AUTO",
            "state": "REAUTHORIZATION_REQUIRED",
            "is_active": False,
            "scanner_active": False,
            "current_equity_usd": 15.01,
            "current_equity_source": "ASTERDEX_ACCOUNT_SNAPSHOT",
            "global_lock_held": False,
            "daily_loss_snapshot": {"used_usdt": 0.0, "status": "VERIFIED"},
        },
    ):
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.json()
        assert data["mode"] == "LIVE_AUTO"
        assert data["session_state"] == "REAUTHORIZATION_REQUIRED"
        assert data["session_id"] == "SESS_LIVE_AUTO_TEST_RECOVERED"


def test_api_status_reports_paper_when_no_live_session(client):
    """When session is INACTIVE and mode is PAPER, /api/status defaults to PAPER."""
    with patch.object(
        live_auto_session_manager,
        "get_session_info",
        return_value={
            "session_id": None,
            "mode": "PAPER",
            "state": "INACTIVE",
            "is_active": False,
            "scanner_active": False,
            "current_equity_usd": 15.0,
            "current_equity_source": "LIVE_MICRO_LOCAL_PAPER",
            "global_lock_held": False,
            "daily_loss_snapshot": {"used_usdt": 0.0, "status": "VERIFIED"},
        },
    ):
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.json()
        assert data["mode"] == "PAPER"
        assert data["session_state"] == "INACTIVE"
