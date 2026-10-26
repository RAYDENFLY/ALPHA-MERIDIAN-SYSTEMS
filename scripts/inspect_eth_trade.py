import sqlite3, json

conn = sqlite3.connect("data/scanner.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("SELECT * FROM automated_trade_journal WHERE trade_id = 'TRD_ETHUSDT_1792966766085'")
row = dict(cur.fetchone())
for k, v in row.items():
    print(f"{k}: {repr(v)}")
