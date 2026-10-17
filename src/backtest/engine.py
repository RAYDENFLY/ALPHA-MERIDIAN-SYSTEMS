"""Event-Driven Historical Backtesting Engine.

Simulates exact bar-by-bar execution, fees, slippage, funding payments,
holding-time expiration, and conservative intrabar collision resolution (SL-first)
with full risk limits and dynamic break-even ML confidence gating.
"""

from typing import Dict, List, Optional
import uuid
from src.core.logger import logger
from src.core.types import Candle, Direction, SetupCandidate, TradeRecord
from src.data.database import DatabaseManager
from src.exchange.metadata import SymbolSpecs
from src.risk.manager import RiskManager
from src.risk.sizing import PositionSizer
from src.strategy.generator import SetupGenerator
from src.backtest.performance import BacktestReport, PerformanceCalculator
from src.ml.pipeline import FeaturePipeline


class BacktestEngine:
    """Event-driven simulator enforcing real-world execution rules."""

    def __init__(
        self,
        db: DatabaseManager,
        symbol_specs_map: Dict[str, SymbolSpecs],
        initial_equity: float = 16.0,
        trade_risk: float = 0.09,
        daily_loss_limit: float = 0.18,
        weekly_loss_limit: float = 0.54,
        max_open_positions: int = 1,
        max_entries_per_week: int = 3,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0005,
        slippage: float = 0.0005,
        max_holding_bars: Optional[int] = 48,
        cost_buffer: float = 0.02,
        target_isolated_margin: float = 6.0,
        target_isolated_margin_max: float = 6.5,
        hard_risk_limit_usdt: float = 1.50,
    ) -> None:
        self.db = db
        self.specs_map = symbol_specs_map
        self.initial_equity = initial_equity
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage = slippage
        self.max_holding_bars = max_holding_bars
        self.cost_buffer = cost_buffer
        self.target_isolated_margin = target_isolated_margin
        self.target_isolated_margin_max = target_isolated_margin_max
        self.hard_risk_limit_usdt = hard_risk_limit_usdt

        self.risk_manager = RiskManager(
            initial_equity_usdt=initial_equity,
            trade_risk_usdt=trade_risk,
            daily_loss_limit_usdt=daily_loss_limit,
            weekly_loss_limit_usdt=weekly_loss_limit,
            max_open_positions=max_open_positions,
            max_entries_per_week=max_entries_per_week,
        )

        self.strategy_gen = SetupGenerator()
        self.funding_cache: Dict[str, Dict[int, float]] = {}

    def _load_funding_cache(self, symbol: str) -> None:
        if symbol not in self.funding_cache:
            rates = self.db.get_funding_rates(symbol)
            self.funding_cache[symbol] = {
                r["funding_time_ms"]: float(r["funding_rate"]) for r in rates
            }

    @staticmethod
    def calculate_break_even_probability(
        candidate: SetupCandidate,
        cost_buffer: float = 0.02,
    ) -> float:
        """Calculate setup-specific break-even win probability including fee/slippage buffer.

        p_break_even = Loss / (Reward + Loss) + cost_buffer
        """
        loss_dist = abs(candidate.entry_price - candidate.stop_loss)
        reward_dist = abs(candidate.take_profit_1 - candidate.entry_price)
        total_span = reward_dist + loss_dist
        if total_span <= 1e-12:
            return 0.5 + cost_buffer
        return (loss_dist / total_span) + cost_buffer

    def run_backtest(
        self,
        symbols: List[str],
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        ml_model=None,
        min_ml_confidence: Optional[float] = None,
        max_holding_bars: Optional[int] = None,
        cost_buffer: Optional[float] = None,
    ) -> BacktestReport:
        """Run synchronized bar-by-bar backtest across symbols."""
        logger.info(f"Loading candles for backtest across {len(symbols)} symbols...")

        effective_holding_bars = (
            max_holding_bars if max_holding_bars is not None else self.max_holding_bars
        )
        if (
            effective_holding_bars is None
            and isinstance(ml_model, dict)
            and "label_horizon_bars" in ml_model
        ):
            effective_holding_bars = int(ml_model["label_horizon_bars"])

        effective_cost_buffer = (
            cost_buffer if cost_buffer is not None else self.cost_buffer
        )

        # Load 1H and 4H candles for all symbols
        candles_1h_by_sym: Dict[str, List[Candle]] = {}
        candles_4h_by_sym: Dict[str, List[Candle]] = {}

        all_timestamps = set()

        for s in symbols:
            self._load_funding_cache(s)
            c1 = self.db.get_candles(
                s, "1h", start_time_ms=start_time_ms, end_time_ms=end_time_ms
            )
            c4 = self.db.get_candles(
                s, "4h", start_time_ms=start_time_ms, end_time_ms=end_time_ms
            )
            candles_1h_by_sym[s] = c1
            candles_4h_by_sym[s] = c4
            for c in c1:
                all_timestamps.add(c.timestamp_ms)

        sorted_timestamps = sorted(all_timestamps)
        logger.info(
            f"Running event-driven simulation over {len(sorted_timestamps)} time intervals "
            f"(max_holding_bars={effective_holding_bars})..."
        )

        # Map symbol -> timestamp -> candle
        sym_ts_map: Dict[str, Dict[int, Candle]] = {
            s: {c.timestamp_ms: c for c in candles_1h_by_sym[s]} for s in symbols
        }

        active_trade: Optional[TradeRecord] = None
        closed_trades: List[TradeRecord] = []

        # Maintain running indices for each symbol
        sym_indices: Dict[str, int] = {s: 0 for s in symbols}

        for ts in sorted_timestamps:
            # 1. First, evaluate existing open position against the current bar
            if active_trade is not None:
                current_candle = sym_ts_map.get(active_trade.symbol, {}).get(ts)
                if current_candle is not None:
                    trade_closed = self._process_open_trade_bar(
                        active_trade,
                        current_candle,
                        max_holding_bars=effective_holding_bars,
                    )
                    if trade_closed:
                        self.risk_manager.record_exit(active_trade)
                        closed_trades.append(active_trade)
                        active_trade = None

            # 2. Advance symbol index
            for s in symbols:
                if ts in sym_ts_map[s]:
                    sym_indices[s] += 1

            # 3. Check if account allows opening a new position
            if active_trade is None:
                can_open, reason = self.risk_manager.can_open_new_position(ts)
                if can_open:
                    # Scan symbols for candidate setups
                    for s in symbols:
                        c1_list = candles_1h_by_sym[s]
                        c4_list = candles_4h_by_sym[s]
                        cur_idx = sym_indices[s] - 1
                        if cur_idx < 35:
                            continue

                        # Check if candle at ts matches
                        if cur_idx >= len(c1_list) or c1_list[cur_idx].timestamp_ms != ts:
                            continue

                        candidate = self.strategy_gen.generate_candidate(
                            symbol=s,
                            candles_1h=c1_list,
                            candles_4h=c4_list,
                            as_of_index_1h=cur_idx,
                        )

                        if candidate is None:
                            continue

                        # CRITICAL FIX (FINDING-01): Dynamic break-even probability gating
                        # instead of unreachable static 0.55 cutoff
                        if ml_model is not None:
                            try:
                                funding = self.funding_cache.get(s, {}).get(ts, 0.0)
                                feats_dict = FeaturePipeline.extract_features(
                                    candidate, c1_list[: cur_idx + 1], funding
                                )
                                df_f = FeaturePipeline.to_dataframe([feats_dict])
                                if (
                                    isinstance(ml_model, dict)
                                    and "model" in ml_model
                                    and "scaler" in ml_model
                                ):
                                    x_scaled = ml_model["scaler"].transform(df_f.values)
                                    raw_prob = float(
                                        ml_model["model"].predict_proba(x_scaled)[0, 1]
                                    )
                                    calibrator = ml_model.get("calibrator")
                                    if calibrator is not None and hasattr(
                                        calibrator, "predict"
                                    ):
                                        prob = float(calibrator.predict([raw_prob])[0])
                                    else:
                                        prob = raw_prob
                                elif hasattr(ml_model, "predict_proba"):
                                    prob = float(
                                        ml_model.predict_proba(df_f.values)[0, 1]
                                    )
                                else:
                                    prob = 0.5
                            except Exception:
                                prob = 0.5

                            candidate.ml_probability = prob
                            required_prob = (
                                min_ml_confidence
                                if min_ml_confidence is not None
                                else self.calculate_break_even_probability(
                                    candidate, cost_buffer=effective_cost_buffer
                                )
                            )
                            if prob < required_prob:
                                continue

                        # Calculate sizing
                        specs = self.specs_map.get(s)
                        if specs is None:
                            continue

                        effective_risk = self.risk_manager.get_effective_trade_risk(ts)
                        assessment = PositionSizer.calculate_sizing(
                            symbol_specs=specs,
                            entry_price=candidate.entry_price,
                            stop_loss=candidate.stop_loss,
                            target_isolated_margin=self.target_isolated_margin,
                            target_isolated_margin_max=self.target_isolated_margin_max,
                            available_equity_usdt=self.risk_manager.equity_usdt,
                            max_leverage=10,
                            hard_risk_limit_usdt=self.hard_risk_limit_usdt,
                            risk_budget_usdt=effective_risk,
                        )

                        if not assessment.is_feasible:
                            continue

                        # Realistic Execution: Enter on confirmation close + slippage
                        slip_mult = (
                            (1.0 + self.slippage)
                            if candidate.direction == Direction.LONG
                            else (1.0 - self.slippage)
                        )
                        actual_entry_price = candidate.entry_price * slip_mult
                        entry_fee = assessment.position_notional_usdt * self.taker_fee

                        active_trade = TradeRecord(
                            trade_id=str(uuid.uuid4())[:8],
                            symbol=s,
                            direction=candidate.direction,
                            entry_timestamp_ms=ts,
                            entry_price=actual_entry_price,
                            stop_loss=candidate.stop_loss,
                            take_profit=candidate.take_profit_1,
                            quantity=assessment.quantity,
                            notional_usdt=assessment.position_notional_usdt,
                            fee_paid=entry_fee,
                            funding_paid=0.0,
                            is_open=True,
                            setup_id=candidate.candidate_id,
                            entry_category=(
                                candidate.entry_category.value
                                if hasattr(candidate.entry_category, "value")
                                else str(candidate.entry_category)
                            ),
                        )
                        self.risk_manager.record_entry(active_trade)
                        break  # Max 1 open position across account

        return PerformanceCalculator.calculate_report(
            closed_trades, initial_equity=self.initial_equity
        )

    def _process_open_trade_bar(
        self,
        trade: TradeRecord,
        candle: Candle,
        max_holding_bars: Optional[int] = None,
    ) -> bool:
        """Process price bar for open position.
        Uses conservative SL-first collision assumption if both TP and SL are touched,
        and enforces time-horizon expiration matching SetupLabeler (FINDING-03).
        """
        # Apply funding payments if candle timestamp corresponds to funding hour
        funding_rate = self.funding_cache.get(trade.symbol, {}).get(
            candle.timestamp_ms, 0.0
        )
        if funding_rate != 0.0:
            # Long pays positive funding, short receives
            if trade.direction == Direction.LONG:
                funding_payment = trade.notional_usdt * funding_rate
            else:
                funding_payment = trade.notional_usdt * (-funding_rate)
            trade.funding_paid += funding_payment

        if trade.direction == Direction.LONG:
            hit_tp = candle.high >= trade.take_profit
            hit_sl = candle.low <= trade.stop_loss

            if hit_tp and hit_sl:
                # Conservative collision policy: SL executes first
                exit_price = trade.stop_loss * (1.0 - self.slippage)
                exit_fee = (trade.quantity * exit_price) * self.taker_fee
                trade.fee_paid += exit_fee
                trade.exit_timestamp_ms = candle.timestamp_ms
                trade.exit_price = exit_price
                trade.pnl_gross = trade.quantity * (exit_price - trade.entry_price)
                trade.pnl_net = trade.pnl_gross - trade.fee_paid - trade.funding_paid
                trade.exit_reason = "SL_COLLISION"
                trade.is_open = False
                return True
            elif hit_sl:
                exit_price = trade.stop_loss * (1.0 - self.slippage)
                exit_fee = (trade.quantity * exit_price) * self.taker_fee
                trade.fee_paid += exit_fee
                trade.exit_timestamp_ms = candle.timestamp_ms
                trade.exit_price = exit_price
                trade.pnl_gross = trade.quantity * (exit_price - trade.entry_price)
                trade.pnl_net = trade.pnl_gross - trade.fee_paid - trade.funding_paid
                trade.exit_reason = "SL"
                trade.is_open = False
                return True
            elif hit_tp:
                exit_price = trade.take_profit * (1.0 - self.slippage)
                exit_fee = (trade.quantity * exit_price) * self.taker_fee
                trade.fee_paid += exit_fee
                trade.exit_timestamp_ms = candle.timestamp_ms
                trade.exit_price = exit_price
                trade.pnl_gross = trade.quantity * (exit_price - trade.entry_price)
                trade.pnl_net = trade.pnl_gross - trade.fee_paid - trade.funding_paid
                trade.exit_reason = "TP"
                trade.is_open = False
                return True

        elif trade.direction == Direction.SHORT:
            hit_tp = candle.low <= trade.take_profit
            hit_sl = candle.high >= trade.stop_loss

            if hit_tp and hit_sl:
                # Conservative collision policy: SL executes first
                exit_price = trade.stop_loss * (1.0 + self.slippage)
                exit_fee = (trade.quantity * exit_price) * self.taker_fee
                trade.fee_paid += exit_fee
                trade.exit_timestamp_ms = candle.timestamp_ms
                trade.exit_price = exit_price
                trade.pnl_gross = trade.quantity * (trade.entry_price - exit_price)
                trade.pnl_net = trade.pnl_gross - trade.fee_paid - trade.funding_paid
                trade.exit_reason = "SL_COLLISION"
                trade.is_open = False
                return True
            elif hit_sl:
                exit_price = trade.stop_loss * (1.0 + self.slippage)
                exit_fee = (trade.quantity * exit_price) * self.taker_fee
                trade.fee_paid += exit_fee
                trade.exit_timestamp_ms = candle.timestamp_ms
                trade.exit_price = exit_price
                trade.pnl_gross = trade.quantity * (trade.entry_price - exit_price)
                trade.pnl_net = trade.pnl_gross - trade.fee_paid - trade.funding_paid
                trade.exit_reason = "SL"
                trade.is_open = False
                return True
            elif hit_tp:
                exit_price = trade.take_profit * (1.0 + self.slippage)
                exit_fee = (trade.quantity * exit_price) * self.taker_fee
                trade.fee_paid += exit_fee
                trade.exit_timestamp_ms = candle.timestamp_ms
                trade.exit_price = exit_price
                trade.pnl_gross = trade.quantity * (trade.entry_price - exit_price)
                trade.pnl_net = trade.pnl_gross - trade.fee_paid - trade.funding_paid
                trade.exit_reason = "TP"
                trade.is_open = False
                return True

        # HIGH PRIORITY FIX (FINDING-03): Enforce holding-time expiration (TIMEOUT)
        horizon = max_holding_bars if max_holding_bars is not None else self.max_holding_bars
        if horizon is not None and horizon > 0:
            elapsed_hours = (candle.timestamp_ms - trade.entry_timestamp_ms) // (3600 * 1000)
            if elapsed_hours >= horizon:
                if trade.direction == Direction.LONG:
                    exit_price = candle.close * (1.0 - self.slippage)
                    trade.pnl_gross = trade.quantity * (exit_price - trade.entry_price)
                else:
                    exit_price = candle.close * (1.0 + self.slippage)
                    trade.pnl_gross = trade.quantity * (trade.entry_price - exit_price)

                exit_fee = (trade.quantity * exit_price) * self.taker_fee
                trade.fee_paid += exit_fee
                trade.exit_timestamp_ms = candle.timestamp_ms
                trade.exit_price = exit_price
                trade.pnl_net = trade.pnl_gross - trade.fee_paid - trade.funding_paid
                trade.exit_reason = "TIMEOUT"
                trade.is_open = False
                return True

        return False

    def _extract_quick_features(
        self, candidate: SetupCandidate, candles: List[Candle]
    ) -> List[float]:
        """Extract lightweight numerical feature vector for ML model inference."""
        entry = candidate.entry_price
        sl = candidate.stop_loss
        tp = candidate.take_profit_1
        c = candles[-1]
        rng = max(c.high - c.low, 1e-8)
        body = abs(c.close - c.open) / rng
        upper_wick = (c.high - max(c.open, c.close)) / rng
        lower_wick = (min(c.open, c.close) - c.low) / rng

        vol = c.volume
        avg_vol = (
            sum(x.volume for x in candles[-20:]) / 20.0 if len(candles) >= 20 else vol
        )
        rvol = vol / avg_vol if avg_vol > 0 else 1.0

        sl_dist = abs(entry - sl) / entry
        tp_dist = abs(tp - entry) / entry

        return [
            1.0 if candidate.direction == Direction.LONG else -1.0,
            1.0
            if candidate.bias_4h.value == "BULLISH"
            else (-1.0 if candidate.bias_4h.value == "BEARISH" else 0.0),
            candidate.gross_reward_to_risk,
            candidate.net_reward_to_risk,
            sl_dist,
            tp_dist,
            body,
            upper_wick,
            lower_wick,
            rvol,
        ]