from datetime import date
from pathlib import Path
from src.data.database import DatabaseManager
from src.research_data.integration import ResearchDataAccess
from src.research_data.storage import ResearchDataStore
from src.research_data.types import ResearchObservation

def test_research_contract_preserves_identity_and_prevents_lookahead():
    db=DatabaseManager(Path(f".pytest-integration-{id(object())}.db")); store=ResearchDataStore(db)
    store.insert_observations([ResearchObservation("treasury","treasury_nominal","US","10Y","2024-01-02","2024-01-03",None,4.0,"percent","u","DELAYED","VALID","x")])
    access=ResearchDataAccess(db); result=access.get_range("treasury_nominal",date(2024,1,1),date(2024,1,2))
    assert len(result["observations"])==1
    row=result["observations"][0]
    assert row["observation_timestamp"]=="2024-01-02" and row["publication_timestamp"]=="2024-01-03"
    future=access.get_range("treasury_nominal",date(2024,1,3),date(2024,1,4))
    assert future["observations"]==[]

def test_readiness_preserves_independent_dimensions():
    db=DatabaseManager(Path(f".pytest-integration-ready-{id(object())}.db")); result=ResearchDataAccess(db).readiness("unknown_dataset",date(2024,1,1),date(2024,1,5))
    assert result["availability"]=="MISSING" and result["coverage"]=="UNKNOWN" and result["quality"]=="VALID"

def test_market_participant_ui_has_explicit_cot_and_retail_states():
    html=Path("src/api/dashboard.html").read_text(encoding="utf-8")
    js=Path("src/static/js/market_participant.js").read_text(encoding="utf-8")
    assert 'id="tab-cot"' in html and 'id="tab-retail_sentiment"' in html
    assert 'id="tab-institutional_positioning"' in html
    assert 'no verified retail positioning dataset' in html
    assert '/api/research/data/cftc_cot_disaggregated' in js
