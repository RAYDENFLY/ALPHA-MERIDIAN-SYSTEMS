import sqlite3
import json

conn = sqlite3.connect("data/scanner.db")
c = conn.cursor()

# 1. Reconcile Candidate 1 with its actual filled order
c.execute("SELECT details_json FROM setup_candidates WHERE candidate_id = 'MCAND_B_AGTUSDT_1792341746379'")
row = c.fetchone()
if row and row[0]:
    d = json.loads(row[0])
    d["execution_status"] = "FILLED"
    d["order_id"] = "PORD_AGTUSDT_1792341746379"
    d["filled_quantity"] = 190.0
    d["filled_at_ms"] = 1792342800000
    c.execute("UPDATE setup_candidates SET details_json = ? WHERE candidate_id = 'MCAND_B_AGTUSDT_1792341746379'", (json.dumps(d),))
    print("Reconciled candidate 1 execution status to FILLED")

# 2. Reconcile Candidates 2 and 3 with explicit suppression reason
for cid in ['MCAND_B_AGTUSDT_1792343843731', 'MCAND_B_AGTUSDT_1792343889355']:
    c.execute("SELECT details_json FROM setup_candidates WHERE candidate_id = ?", (cid,))
    row = c.fetchone()
    if row and row[0]:
        d = json.loads(row[0])
        reason = "Submission suppressed by deduplication: Active position already open on AGTUSDT"
        reasons = d.get("rejection_reasons", [])
        if reason not in reasons:
            reasons.append(reason)
        d["rejection_reasons"] = reasons
        c.execute("UPDATE setup_candidates SET details_json = ? WHERE candidate_id = ?", (json.dumps(d), cid))
        print(f"Reconciled {cid} with suppression reason")

conn.commit()
conn.close()
print("Historical candidate reconciliation complete.")
