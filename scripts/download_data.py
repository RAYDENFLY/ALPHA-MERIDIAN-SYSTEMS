"""CLI script to download 365 days of 1H and 4H historical candles and funding rates.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_default_config
from src.core.logger import logger
from src.data.database import DatabaseManager
from src.data.downloader import HistoricalDownloader
from src.exchange.aster import AsterDexAdapter


def main():
    cfg = get_default_config()
    db = DatabaseManager(cfg.db_path)
    exchange = AsterDexAdapter(
        base_url=cfg.exchange.base_url,
        request_timeout=cfg.exchange.request_timeout,
        max_retries=cfg.exchange.max_retries,
        retry_delay_sec=cfg.exchange.retry_delay_sec,
    )

    downloader = HistoricalDownloader(
        exchange=exchange,
        db=db,
        batch_size=1000,
        request_delay_sec=0.05,
    )

    logger.info(f"Starting historical download for {len(cfg.enabled_symbols)} symbols...")
    logger.info(f"Symbols: {cfg.enabled_symbols}")

    for sym in cfg.enabled_symbols:
        logger.info(f"--- Processing {sym} ---")
        for tf in ["1h", "4h"]:
            count = downloader.download_symbol_timeframe(sym, tf, days_back=365)
            total = db.get_candle_count(sym, tf)
            logger.info(f"[{sym} {tf}] Inserted: {count}, Total in DB: {total}")
        
        funding_count = downloader.download_funding_rates(sym, days_back=365)
        logger.info(f"[{sym}] Funding rates stored: {funding_count}")

    logger.info("Download completed successfully!")


if __name__ == "__main__":
    main()
