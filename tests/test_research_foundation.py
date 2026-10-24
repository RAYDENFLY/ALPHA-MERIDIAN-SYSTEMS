from pathlib import Path
import uuid
from src.data.database import DatabaseManager
from src.research_data.adapters import parse_cot_csv, parse_treasury_csv
from src.research_data.storage import ResearchDataStore


def test_research_schema_and_idempotent_observations():
    db = DatabaseManager(Path(f".pytest-research-foundation-{uuid.uuid4().hex}.db"))
    store = ResearchDataStore(db)
    store.upsert_source({"source_id":"treasury", "source_name":"US Treasury", "base_url":"https://home.treasury.gov", "source_type":"official"})
    store.upsert_dataset({"dataset_id":"treasury_nominal", "source_id":"treasury", "dataset_name":"Nominal", "update_frequency":"DAILY", "unit":"percent"})
    obs = parse_treasury_csv("Date,2 Yr,5 Yr,10 Yr,30 Yr\n01/02/2024,4.3,4.0,3.9,4.1\n", "treasury_nominal", "https://example.test")
    assert store.insert_observations(obs) == 4
    assert store.insert_observations(obs) == 0
    assert len(store.list_observations("treasury_nominal")) == 4


def test_treasury_missing_values_and_cot_net_position():
    assert len(parse_treasury_csv("Date,2 Yr,5 Yr,10 Yr,30 Yr\n01/02/2024,N/A,4.0,,4.1\n", "x", "u")) == 2
    csv = "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,Open_Interest_All,Dealer_Positions_Long_All,Dealer_Positions_Short_All\nBitcoin,2024-01-02,100,60,40\n"
    rows = parse_cot_csv(csv, "u")
    assert len(rows) == 1
    assert rows[0].value == 20


def test_cot_invalid_report_date_is_rejected():
    from pytest import raises
    csv = "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,Open_Interest_All,Dealer_Positions_Long_All,Dealer_Positions_Short_All\nBitcoin,not-a-date,100,60,40\n"
    with raises(ValueError):
        parse_cot_csv(csv, "u")
