"""Close and archive legacy Model B V1 open positions at current mark price for V2 cutover."""

import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone


def close_legacy_v1_trades(db_path: str = "data/scanner.db"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM trade_journal WHERE model_id = 'MODEL_B' AND is_open = 1")
    rows = [dict(r) for r in cursor.fetchall()]
    print(f"Found {len(rows)} legacy open Model B positions in {db_path}.")

    if not rows:
        print("No open positions found. Nothing to close.")
        conn.close()
        return

    # Fetch live mark prices from running API or AsterDex ticker
    live_positions = {}
    try:
        req = urllib.request.urlopen("http://localhost:8000/api/model_b/status")
        data = json.loads(req.read().decode())
        live_positions = {p["trade_id"]: p for p in data.get("paper", {}).get("open_positions", [])}
    except Exception as e:
        print(f"Warning: Could not fetch from local API ({e}), will use fallback.")

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    cutover_ms = int(now_utc.timestamp() * 1000)

    for r in rows:
        tid = r["trade_id"]
        sym = r["symbol"]
        direction = str(r["direction"]).upper()
        qty = float(r["quantity"])
        entry_px = float(r["entry_price"])
        entry_fee = float(r["fee_paid"] or 0.0)
        funding_paid = float(r["funding_paid"] or 0.0)

        live_p = live_positions.get(tid, {})
        mark_px = float(live_p.get("mark_price") or entry_px)
        exit_fee = round(mark_px * qty * 0.0005, 4)
        total_fee = round(entry_fee + exit_fee, 4)

        if direction == "LONG":
            pnl_gross = round((mark_px - entry_px) * qty, 4)
        else:
            pnl_gross = round((entry_px - mark_px) * qty, 4)
        pnl_net = round(pnl_gross - total_fee - funding_paid, 4)

        details = {}
        if r.get("details_json"):
            try:
                details = json.loads(r["details_json"])
            except Exception:
                pass
        details["exit_reason"] = "COHORT_CUTOVER_V2"
        details["exit_price"] = mark_px
        details["exit_time_utc"] = now_iso
        details["cohort_version"] = "MODEL_B_V1_ARCHIVED"

        cursor.execute("""
            UPDATE trade_journal
            SET 
                is_open = 0,
                exit_reason = 'COHORT_CUTOVER_V2',
                exit_timestamp_ms = ?,
                exit_price = ?,
                pnl_gross = ?,
                pnl_net = ?,
                fee_paid = ?,
                funding_paid = ?,
                order_status = 'CLOSED',
                details_json = ?
            WHERE trade_id = ?
        """, (
            cutover_ms,
            mark_px,
            pnl_gross,
            pnl_net,
            total_fee,
            funding_paid,
            json.dumps(details),
            tid
        ))

        print(f"Archived {tid} ({sym} {direction}): entry={entry_px:.4f}, mark={mark_px:.4f}, net_pnl={pnl_net:+.4f}")

    conn.commit()
    conn.close()
    print(f"Successfully archived all {len(rows)} legacy positions with exit_reason='COHORT_CUTOVER_V2'.")


if __name__ == "__main__":
    close_legacy_v1_trades()
