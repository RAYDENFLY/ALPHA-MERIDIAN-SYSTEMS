"""Pure parsers and conservative public-source adapters for Phase 2."""
import csv
from datetime import datetime, timezone
from io import StringIO
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Optional

from src.research.types import ResearchObservation


def _date(value: str) -> str:
    return datetime.strptime(value.strip(), "%m/%d/%Y").date().isoformat()


def parse_treasury_csv(text: str, dataset_id: str, source_url: str) -> List[ResearchObservation]:
    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames or "Date" not in reader.fieldnames:
        raise ValueError("Treasury CSV is missing Date column")
    wanted = {"2 Yr": "2Y", "5 Yr": "5Y", "10 Yr": "10Y", "30 Yr": "30Y", "5 YR": "5Y_REAL", "10 YR": "10Y_REAL", "30 YR": "30Y_REAL"}
    rows: List[ResearchObservation] = []
    for row in reader:
        obs_date = _date(row["Date"])
        for column, symbol in wanted.items():
            if column not in row or row[column] in (None, "", "N/A"):
                continue
            try: value = float(row[column])
            except (TypeError, ValueError):
                continue
            rows.append(ResearchObservation("treasury", dataset_id, "US_TREASURY", symbol, obs_date, None, obs_date, value, "percent", source_url, "DELAYED", "VALID", f"{dataset_id}:{obs_date}:{symbol}"))
    return rows


def parse_treasury_xml(text: str, dataset_id: str, source_url: str) -> List[ResearchObservation]:
    root = ET.fromstring(text)
    rows: List[ResearchObservation] = []
    # Treasury namespaces vary; use local tag names and accept only known fields.
    for node in root.iter():
        fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in list(node)}
        date = fields.get("NEW_DATE") or fields.get("d:NEW_DATE") or fields.get("BC_2YEAR") and fields.get("NEW_DATE")
        if not date:
            continue
        for key, symbol in (("BC_2YEAR", "2Y"), ("BC_5YEAR", "5Y"), ("BC_10YEAR", "10Y"), ("BC_30YEAR", "30Y")):
            raw = fields.get(key)
            if raw in (None, "", "N/A"): continue
            try: value = float(raw)
            except ValueError: continue
            rows.append(ResearchObservation("treasury", dataset_id, "US_TREASURY", symbol, date[:10], None, date[:10], value, "percent", source_url, "DELAYED", "VALID", f"{dataset_id}:{date}:{symbol}"))
    return rows


def parse_cot_csv(text: str, source_url: str, dataset_id: str = "cftc_cot_disaggregated") -> List[ResearchObservation]:
    reader = csv.DictReader(StringIO(text))
    required = {"Market_and_Exchange_Names", "Report_Date_as_YYYY-MM-DD", "Open_Interest_All"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError("COT CSV is missing required columns")
    categories = {"Dealer": ("Dealer_Positions_Long_All", "Dealer_Positions_Short_All"), "Asset Manager": ("Asset_Mgr_Positions_Long_All", "Asset_Mgr_Positions_Short_All"), "Leveraged Funds": ("Lev_Money_Positions_Long_All", "Lev_Money_Positions_Short_All"), "Other Reportables": ("Other_Rept_Positions_Long_All", "Other_Rept_Positions_Short_All"), "Non-Reportables": ("NonRept_Positions_Long_All", "NonRept_Positions_Short_All")}
    rows: List[ResearchObservation] = []
    for row in reader:
        name = row["Market_and_Exchange_Names"]
        if not any(term in name.lower() for term in ("bitcoin", "ether")): continue
        date = row["Report_Date_as_YYYY-MM-DD"]
        try: oi = float(row["Open_Interest_All"])
        except (TypeError, ValueError): oi = 0.0
        for category, (long_key, short_key) in categories.items():
            try: gross_long, gross_short = float(row.get(long_key, "")), float(row.get(short_key, ""))
            except (TypeError, ValueError): continue
            net = gross_long - gross_short
            rows.append(ResearchObservation("cftc", dataset_id, name, category, date, None, date, net, "contracts", source_url, "WEEKLY" if False else "DELAYED", "VALID", f"{dataset_id}:{name}:{date}:{category}"))
            if oi > 0:
                rows.append(ResearchObservation("cftc", dataset_id, name, f"{category}:net_position_pct_oi", date, None, date, net / oi * 100.0, "percent", source_url, "DELAYED", "VALID", f"{dataset_id}:{name}:{date}:{category}:pct"))
    return rows
