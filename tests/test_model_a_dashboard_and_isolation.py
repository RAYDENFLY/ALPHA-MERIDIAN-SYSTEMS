import re
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from src.api.app import app, cfg, db, journal


@pytest.fixture
def client():
    return TestClient(app)


def test_1_single_model_a_navigation_item_under_ai_research():
    """1. A single Model A Engine navigation item exists under AI & Research with Font Awesome icon (not emoji)."""
    html = Path("src/api/dashboard.html").read_text(encoding="utf-8")

    # Exactly one sideBtn-model_a button
    assert html.count('id="sideBtn-model_a"') == 1
    assert html.count("switchTab('model_a')") == 1
    assert html.count('id="tab-model_a"') == 1

    # Located inside catGroup-ai_research
    cat_match = re.search(
        r'<div id="catGroup-ai_research"[^>]*>(.*?)</div>\s*</div>\s*<!-- 5\. EXCHANGE',
        html,
        re.DOTALL,
    )
    assert cat_match is not None, "catGroup-ai_research block must exist"
    ai_research_block = cat_match.group(1)
    assert 'id="sideBtn-model_a"' in ai_research_block
    assert 'Model A Engine' in ai_research_block
    assert 'fa-solid fa-shield-halved' in ai_research_block


def test_2_and_3_model_a_page_reachable_in_expanded_collapsed_and_url_routing():
    """2 & 3. Model A page is reachable from expanded & collapsed sidebar modes and URL routing (?tab=model_a)."""
    nav_js = Path("src/static/js/navigation.js").read_text(encoding="utf-8")
    base_css = Path("src/static/css/base.css").read_text(encoding="utf-8")

    # Registered in ALL_KNOWN_TABS and mapped to ai_research
    assert "'model_a'" in nav_js
    assert "'model_a': 'ai_research'" in nav_js
    assert "if (tabId === 'model_a' && typeof loadModelA === 'function') loadModelA();" in nav_js

    # Collapsed sidebar category click exposes child items via selectCollapsedCategory
    assert "if (sidebar?.classList.contains('collapsed'))" in nav_js
    assert "selectCollapsedCategory(catId);" in nav_js
    assert ".sidebar-nav.collapsed.category-selected .sidebar-category.selected > [id^=\"catGroup-\"]" in base_css

    # URL query parameter routing (?tab=model_a)
    assert "const tabParam = urlParams.get('tab');" in nav_js
    assert "url.searchParams.set('tab', tabId);" in nav_js


def test_4_model_a_api_strictly_isolates_model_a_from_model_b(client):
    """4. Model A page displays only Model A data; zero Model B records leak into Model A."""
    resp = client.get("/api/model_a/status")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "SUCCESS"
    assert data["model_id"] == "MODEL_A"
    assert data["strategy_status"]["model_id"] == "MODEL_A"
    assert data["forward_validation"]["model_id"] == "MODEL_A"
    assert data["validation_metrics"]["model_id"] == "MODEL_A"

    # Check open positions, pending orders, recent candidates, and trade journal
    for pos in data["open_positions"]:
        assert str(pos.get("model_id", "")).upper() == "MODEL_A"
    for order in data["pending_orders"]:
        assert str(order.get("model_id", "")).upper() == "MODEL_A"
    for cand in data["recent_candidates"]:
        assert str(cand.get("model_id", "")).upper() == "MODEL_A"
    for trade in data["trade_journal"]:
        assert str(trade.get("model_id", "")).upper() == "MODEL_A"

    # Verify that even though Model B has 42 trades in trades.db, Model A reports 0 trades
    model_b_trades = journal.get_trades(model_id="MODEL_B", limit=100)
    assert len(model_b_trades) > 0, "Database should have Model B trades to prove isolation"
    assert data["forward_validation"]["completed_paper_trades"] == 0
    assert data["validation_metrics"]["completed_trades_count"] == 0
    assert len(data["trade_journal"]) == 0
    assert len(data["open_positions"]) == 0


def test_5_and_6_no_mock_values_and_honest_empty_or_insufficient_sample_states(client):
    """5 & 6. No mock values are used; empty or insufficient sample states render honestly."""
    resp = client.get("/api/model_a/status")
    assert resp.status_code == 200
    data = resp.json()

    vm = data["validation_metrics"]
    assert vm["is_sample_sufficient"] is False
    assert vm["sample_status"] == "INSUFFICIENT_SAMPLE"
    assert vm["required_sample_size"] == 30
    assert vm["completed_trades_count"] == 0
    assert vm["win_rate_pct"] is None
    assert vm["expectancy_per_trade_usdt"] is None
    assert vm["profit_factor"] is None
    assert vm["max_drawdown_usdt"] is None

    fv = data["forward_validation"]
    assert fv["validation_status"] == "UNPROVEN_NO_COMPLETED_FORWARD_TRADES"
    assert fv["sample_badge"] == "INSUFFICIENT_SAMPLE"
    assert fv["candidate_setups_evaluated"] == 128
    assert fv["valid_ready_setups"] == 0
    assert fv["paper_orders_created"] == 0
    assert fv["completed_paper_trades"] == 0

    # Frontend JS must render INSUFFICIENT_SAMPLE and UNAVAILABLE honestly
    js = Path("src/static/js/model-a.js").read_text(encoding="utf-8")
    assert "INSUFFICIENT_SAMPLE" in js
    assert "UNAVAILABLE" in js
    assert "NEVER_RUN" in js


def test_7_strategy_configuration_is_strictly_read_only(client):
    """7. Strategy configuration section is strictly read-only with no mutable inputs or forms."""
    html = Path("src/api/dashboard.html").read_text(encoding="utf-8")
    start_idx = html.index('id="tab-model_a"')
    end_idx = html.index('id="tab-model_d"', start_idx)
    model_a_html = html[start_idx:end_idx]

    # Must not contain any <input>, <select>, <textarea>, or <form> inside #tab-model_a
    assert "<input" not in model_a_html.lower()
    assert "<select" not in model_a_html.lower()
    assert "<textarea" not in model_a_html.lower()
    assert "<form" not in model_a_html.lower()
    assert "IMMUTABLE IN UI" in model_a_html

    # Verify API returns exact frozen parameters
    resp = client.get("/api/model_a/status")
    sc = resp.json()["strategy_configuration"]
    assert sc["strategy_version"] == "MODEL_A_FROZEN"
    assert len(sc["whitelisted_symbols"]) == 11
    assert sc["timeframes"]["bias_timeframe"] == "4h"
    assert sc["timeframes"]["execution_timeframe"] == "1h"
    assert sc["strategy_parameters"]["min_reward_to_risk"] == 1.5
    assert sc["risk_settings"]["initial_equity_usdt"] == 16.0
    assert sc["risk_settings"]["max_open_positions"] == 1
    assert sc["risk_settings"]["margin_mode"] == "ISOLATED"


def test_8_paper_only_and_live_trading_locked_preserved(client):
    """8. Paper-only and live-trading-locked states remain intact."""
    assert cfg.live_trading_locked is True

    resp_status = client.get("/api/status").json()
    assert resp_status["live_trading_locked"] is True
    assert resp_status["forward_validation"]["completed_forward_trades"] == 0

    resp_a = client.get("/api/model_a/status").json()
    assert resp_a["strategy_status"]["live_trading_locked"] is True
    assert resp_a["strategy_status"]["paper_only"] is True


def test_9_and_10_model_b_behavior_unchanged_and_existing_journals_intact(client):
    """9 & 10. Model B behavior is unchanged and existing journal records remain intact."""
    resp_b = client.get("/api/model_b/status")
    assert resp_b.status_code == 200
    b_data = resp_b.json()
    assert b_data["model_id"] == "MODEL_B"
    assert b_data["risk_limits"]["max_open_positions"] == 6

    # Verify SQLite database counts are completely untouched
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM setup_candidates WHERE model_id='MODEL_A'")
        ma_setups = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM trade_journal WHERE model_id='MODEL_B'")
        mb_trades = cur.fetchone()[0]

    assert ma_setups == 128
    assert mb_trades >= 42
