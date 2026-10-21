from fastapi.testclient import TestClient

from src.api.app import app


def client():
    # Deliberately do not enter the TestClient context: the legacy app lifespan
    # performs external exchange discovery, while these contract tests are offline.
    return TestClient(app)


def test_status_contract_and_safety_fields():
    c = client(); response = c.get('/api/model_d/status'); c.close()
    assert response.status_code == 200
    data = response.json()
    assert data.keys() >= {'model_id', 'enabled', 'paper_only', 'live_trading_locked', 'collector_enabled', 'wallet_tracking', 'sources'}
    assert data['model_id'] == 'MODEL_D'
    assert isinstance(data['sources'], dict)
    assert data['paper_only'] is True and data['live_trading_locked'] is True
    assert data['wallet_tracking'] == 'UNAVAILABLE'


def test_signals_contract_empty_state_and_pagination_validation():
    c = client(); response = c.get('/api/model_d/signals?limit=200&offset=0'); invalid = c.get('/api/model_d/signals?limit=0'); c.close()
    assert response.status_code == 200
    data = response.json()
    assert data.keys() >= {'model_id', 'items', 'count', 'offset', 'limit'}
    assert isinstance(data['items'], list) and data['limit'] == 200 and len(data['items']) <= 200
    assert invalid.status_code == 422


def test_liquidity_contract_never_fabricates_disabled_data():
    c = client(); response = c.get('/api/model_d/liquidity?symbol=BTCUSDT'); c.close()
    assert response.status_code == 200
    data = response.json()
    assert data.keys() >= {'model_id', 'symbol', 'status', 'reason'}
    assert data['status'] == 'UNAVAILABLE'
    assert data['reason'] == 'collector_disabled'
    assert 'error' not in data


def test_whale_events_and_diagnostic_contracts():
    c = client(); events = c.get('/api/model_d/whale-events?limit=50&offset=0'); report = c.get('/api/model_d/diagnostic/report'); c.close()
    assert events.status_code == 200 and report.status_code == 200
    event_data = events.json(); report_data = report.json()
    assert event_data.keys() >= {'model_id', 'items', 'count', 'offset', 'limit', 'status'}
    assert event_data['status'] == 'UNAVAILABLE' and event_data['items'] == []
    assert report_data.keys() >= {'model_id', 'paper_only', 'metrics', 'long', 'short', 'warning'}
    assert report_data['long']['sample_size'] == 0 and report_data['short']['sample_size'] == 0


def test_model_d_endpoint_state_remains_read_only():
    c = client(); before = c.get('/api/model_d/signals').json(); c.close()
    c = client(); after = c.get('/api/model_d/signals').json(); c.close()
    assert before == after
