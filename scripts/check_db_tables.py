import sys, os
sys.path.insert(0, os.path.abspath("."))
import glob, sqlite3
from config.settings import get_default_config

cfg = get_default_config()
print("Default DB Path in config:", cfg.db_path)

for p in glob.glob("data/*.*") + glob.glob("*.*"):
    if p.endswith(".sqlite") or p.endswith(".db"):
        print("Checking:", p)
        conn = sqlite3.connect(p)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print("  Tables:", tables)
        if "trades" in tables:
            cur.execute("SELECT count(*) FROM trades")
            print("  trades count:", cur.fetchone()[0])
        if "fill_ledger" in tables:
            cur.execute("SELECT count(*) FROM fill_ledger")
            print("  fill_ledger count:", cur.fetchone()[0])
        if "automated_trade_journal" in tables:
            cur.execute("SELECT count(*) FROM automated_trade_journal")
            print("  automated_trade_journal count:", cur.fetchone()[0])
            cur.execute("SELECT trade_id, symbol, status, reconciliation_status, notes FROM automated_trade_journal")
            for row in cur.fetchall():
                print("   Record:", row)
