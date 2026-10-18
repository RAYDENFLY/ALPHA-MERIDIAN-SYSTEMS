import sqlite3
import json
from datetime import datetime, timezone

conn = sqlite3.connect("data/scanner.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("""
    SELECT candidate_id, symbol, timestamp_ms, direction, bias_4h, structure_1h,
           entry_price, stop_loss, take_profit_1, take_profit_2, gross_rr, net_rr,
           invalidation_price, ml_probability, calibration_status, eval_sample_count,
           status, details_json, created_at_utc, entry_category, model_id
    FROM setup_candidates 
    WHERE model_id = 'MODEL_B' 
    ORDER BY timestamp_ms ASC
""")
rows = cursor.fetchall()

print(f"Total Model B setup candidate rows: {len(rows)}")

orders = {r['candidate_id']: dict(r) for r in cursor.execute("SELECT * FROM paper_orders WHERE model_id = 'MODEL_B'").fetchall()}
symbol_orders = cursor.execute("SELECT * FROM paper_orders WHERE model_id = 'MODEL_B'").fetchall()
trades = {r['setup_id']: dict(r) for r in cursor.execute("SELECT * FROM trade_journal WHERE model_id = 'MODEL_B'").fetchall()}

for idx, r in enumerate(rows[:7], 1):
    d = dict(r)
    cid = d['candidate_id']
    dj = json.loads(d['details_json'] or "{}")
    ai = dj.get("ai_analysis", {})
    risk = dj.get("risk_assessment", {})
    ord_match = orders.get(cid)
    trd_match = trades.get(cid)
    
    print(f"\n--- [{idx}] Candidate: {cid} ---")
    print(f"  Symbol: {d['symbol']} | Direction: {d['direction']} | Category: {d['entry_category']}")
    print(f"  Created UTC: {d['created_at_utc']} | Timestamp ms: {d['timestamp_ms']}")
    print(f"  Prices: Entry={d['entry_price']} | SL={d['stop_loss']} | TP1={d['take_profit_1']} | TP2={d['take_profit_2']}")
    print(f"  Status: {d['status']} | ExecutionStatus in JSON: {dj.get('execution_status')} | Reason: {dj.get('rejection_reasons')}")
    print(f"  Supporting Reasons: {dj.get('supporting_reasons')}")
    print(f"  Metrics: Spread bps={ai.get('spread_bps')} | Imbalance={ai.get('imbalance_25bps')} | CVD={ai.get('cvd_1h')}")
    print(f"  Risk: Qty={risk.get('quantity')} | Margin USDT={risk.get('target_isolated_margin_usdt')} | Risk Budget={risk.get('risk_budget_usdt')}")
    print(f"  Linked Order: {ord_match.get('order_id') if ord_match else 'None'} (Status: {ord_match.get('status') if ord_match else 'None'})")
    print(f"  Linked Trade: {trd_match.get('trade_id') if trd_match else 'None'} (Status: {'OPEN' if trd_match and trd_match.get('is_open') else 'CLOSED' if trd_match else 'None'})")

