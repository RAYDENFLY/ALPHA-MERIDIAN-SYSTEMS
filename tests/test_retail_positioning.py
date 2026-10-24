from unittest.mock import Mock
import pytest
from src.research_data.retail_positioning import RetailPositioningProvider

def response(payload,status=200):
    r=Mock(status_code=status); r.json.return_value=payload; return r

def test_bybit_account_ratio_parsing_and_cache():
    s=Mock(); s.get.return_value=response({"result":{"list":[{"symbol":"BTCUSDT","buyRatio":"0.6","sellRatio":"0.4","timestamp":"1700000000000"}]}})
    p=RetailPositioningProvider(session=s)
    first=p.get(); second=p.get()
    assert first["status"] == "AVAILABLE"
    assert first["observations"][0]["long_short_account_ratio"] == pytest.approx(1.5)
    assert second["cache_status"]=="HIT" and s.get.call_count==1

def test_bybit_malformed_and_rate_limit_are_explicit():
    s=Mock(); s.get.return_value=response({"result":{"list":[{"buyRatio":"bad"}]} }); assert RetailPositioningProvider(session=s).get()["status"]=="ERROR"
    s.get.return_value=response({},429); assert RetailPositioningProvider(session=s).get()["status"]=="UNAVAILABLE"
