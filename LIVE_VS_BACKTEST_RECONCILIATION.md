# Live vs Backtest Reconciliation

Live journal records in `data/scanner.db` are a diagnostic sample, not a statistical sample. They include ETHUSDT and SOLUSDT closed trades plus an open/pending ZECUSDT record. The records use `EMA20_RECLAIM_5M_V1`, matching the active Groq execution path.

The configured `15M_SWEEP_RECLAIM_SMC` implementation cannot be used to explain those fills without changing the execution-path audit finding. Four live trades are insufficient to estimate edge.

Required next step for a valid comparison: preserve the exact live signal payload, candle snapshot, actual exchange fills/fees, and run both implementations through a venue-labeled research simulator.
