# Four-Year Entry Location Forensics

Not completed as a performance claim because the required four-year 15m/1h dataset and faithful simulator are unavailable.

The source-level entry rules are documented in `src/strategy/sweep_reclaim_15m.py`: confirmed closed 15m candles, 15m/1h swings, sweep and reclaim, wick-ratio and RSI gates, 72h dealing range, discount LONG lock, premium SHORT lock, structural stop, and 2.25R target. The current Groq LIVE_AUTO path does not execute this class; it executes the EMA20 5m path instead.

No future-candle reconstruction was performed.
