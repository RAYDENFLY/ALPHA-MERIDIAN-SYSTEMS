"""Simulated Paper Trading Execution Engine.

Simulates order placement, conservative limit fills, slippage, maker/taker fees,
8-hour funding rate payments, TP/SL bracket monitoring, conservative collision resolution (SL_COLLISION),
idempotency, and state persistence with crash recovery.

HARD SAFETY INVARIANT:
This module operates exclusively on local simulation state and SQLite storage.
It NEVER calls live exchange trading endpoints (create_order, cancel_order, etc.).
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from src.core.logger import logger
from src.core.types import Candle, CandidateStatus, Direction, EntryCategory, SetupCandidate, TradeRecord
from src.data.database import DatabaseManager
from src.execution.events import PaperEvent, PaperEventDispatcher, PaperEventType
from src.execution.types import PaperOrder, PaperOrderStatus, PaperPosition
from src.journal.repository import JournalRepository
from src.risk.manager import RiskManager
from config.settings import PaperExecutionConfig


class PaperExecutionEngine:
    """Manages the full lifecycle of simulated paper orders and positions."""

    def __init__(
        self,
        db: DatabaseManager,
        journal: JournalRepository,
        risk_manager: RiskManager,
        config: Optional[PaperExecutionConfig] = None,
        event_dispatcher: Optional[PaperEventDispatcher] = None,
    ) -> None:
        self.db = db
        self.journal = journal
        self.risk_manager = risk_manager
        self.config = config or PaperExecutionConfig()
        self.event_dispatcher = event_dispatcher or PaperEventDispatcher(self.config.discord_webhook_url)

        self.pending_orders: Dict[str, PaperOrder] = {}
        self.open_positions: Dict[str, PaperPosition] = {}

        # Recover persistent state on initialization (safe across restarts)
        self.recover_state()

    def recover_state(self) -> None:
        """Recover active pending orders and open positions from SQLite storage."""
        self.pending_orders.clear()
        self.open_positions.clear()

        # 1. Recover pending orders
        raw_orders = self.journal.get_paper_orders(status=PaperOrderStatus.PENDING.value)
        for row in raw_orders:
            try:
                order = PaperOrder.from_dict(row)
                self.pending_orders[order.order_id] = order
            except Exception as err:
                logger.error(f"Failed to recover paper order from row {row}: {err}")

        # 2. Recover open trades into active positions & sync with RiskManager
        raw_trades = self.journal.get_open_trades()
        for row in raw_trades:
            try:
                direction_str = row["direction"]
                direction = Direction.LONG if str(direction_str).upper() == "LONG" else Direction.SHORT

                pos = PaperPosition(
                    trade_id=row["trade_id"],
                    order_id=row.get("order_id") or f"REC_{row['trade_id']}",
                    setup_id=row.get("setup_id", ""),
                    symbol=row["symbol"],
                    direction=direction,
                    entry_timestamp_ms=row["entry_timestamp_ms"],
                    entry_price=float(row["entry_price"]),
                    stop_loss=float(row["stop_loss"]),
                    take_profit=float(row["take_profit"]),
                    quantity=float(row["quantity"]),
                    notional_usdt=float(row["notional_usdt"]),
                    margin_usdt=float(row.get("margin_usdt", 5.0)),
                    leverage=float(row.get("leverage", 10.0)),
                    entry_fee=float(row.get("fee_paid", 0.0)),
                    funding_paid=float(row.get("funding_paid", 0.0)),
                    funding_data_missing=bool(row.get("funding_data_missing", False)),
                    is_open=True,
                    entry_category=str(row.get("entry_category", "Unclassified")),
                )
                self.open_positions[pos.symbol] = pos

                # Synchronize with RiskManager open_trades if not present
                trade_record = TradeRecord(
                    trade_id=pos.trade_id,
                    symbol=pos.symbol,
                    direction=pos.direction,
                    entry_timestamp_ms=pos.entry_timestamp_ms,
                    entry_price=pos.entry_price,
                    stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit,
                    quantity=pos.quantity,
                    notional_usdt=pos.notional_usdt,
                    fee_paid=pos.entry_fee,
                    funding_paid=pos.funding_paid,
                    is_open=True,
                    setup_id=pos.setup_id,
                    timeframe=row.get("timeframe", "1h"),
                    order_status=row.get("order_status", "FILLED"),
                    leverage=pos.leverage,
                    margin_usdt=pos.margin_usdt,
                    slippage_paid=float(row.get("slippage_paid", 0.0)),
                    fill_model_version=row.get("fill_model_version", "v1_conservative"),
                    data_source=row.get("data_source", "asterdex_live"),
                    funding_data_missing=pos.funding_data_missing,
                    entry_category=EntryCategory(str(row.get("entry_category", "Unclassified"))),
                )
                if not any(t.trade_id == pos.trade_id for t in self.risk_manager.open_trades):
                    self.risk_manager.open_trades.append(trade_record)

            except Exception as err:
                logger.error(f"Failed to recover open trade {row}: {err}")

        logger.info(
            f"PaperExecutionEngine state recovered: {len(self.open_positions)} open positions, "
            f"{len(self.pending_orders)} pending orders."
        )

    def submit_candidate(self, candidate: SetupCandidate, now_ms: int) -> Optional[PaperOrder]:
        """Submit a READY setup candidate into a simulated limit order with idempotency and risk gating."""
        sym = candidate.symbol

        # 1. Idempotency checks:
        if sym in self.open_positions:
            logger.info(f"PaperEngine: Ignored candidate for {sym}; active position already open.")
            return None

        if any(o.symbol == sym for o in self.pending_orders.values()):
            logger.info(f"PaperEngine: Ignored candidate for {sym}; pending order already exists.")
            return None

        # 2. Check candidate qualification and freshness
        if candidate.status != CandidateStatus.READY:
            logger.info(f"PaperEngine: Rejected candidate {candidate.candidate_id}; status is {candidate.status.value}.")
            return None

        # Execution Gate: Stale candle and TTL expiry rejection
        if candidate.expires_at_ms and now_ms > candidate.expires_at_ms:
            logger.warning(f"PaperEngine: Rejected candidate {candidate.candidate_id}; TTL expired.")
            return None

        stale_threshold_ms = getattr(self, "stale_threshold_hours", 3.0) * 3600.0 * 1000.0
        if (now_ms - candidate.timestamp_ms) > stale_threshold_ms:
            logger.warning(
                f"PaperEngine: Rejected candidate {candidate.candidate_id}; "
                f"candle age exceeds {stale_threshold_ms / 3600000.0}h threshold."
            )
            return None

        assessment = candidate.risk_assessment
        if assessment is None or not assessment.is_feasible:
            return None

        order_id = f"PORD_{sym}_{candidate.timestamp_ms}"

        # 3. Prevent duplicate order submission across restarts
        existing_orders = self.journal.get_paper_orders(symbol=sym)
        if any(o["order_id"] == order_id for o in existing_orders):
            logger.info(f"PaperEngine: Order {order_id} already exists in database; skipping.")
            return None

        entry_cat_str = (
            candidate.entry_category.value
            if hasattr(candidate.entry_category, "value")
            else str(candidate.entry_category)
        )

        # 4. Account Risk Gate Check
        can_open, reason = self.risk_manager.can_open_new_position(now_ms)
        if not can_open:
            logger.warning(f"PaperEngine: Risk limits rejected order for {sym}: {reason}")
            rejected_order = PaperOrder(
                order_id=order_id,
                candidate_id=candidate.candidate_id,
                symbol=sym,
                direction=candidate.direction,
                order_type="LIMIT",
                price=candidate.entry_price,
                stop_loss=candidate.stop_loss,
                take_profit=candidate.take_profit_1,
                quantity=assessment.quantity,
                notional_usdt=assessment.position_notional_usdt,
                margin_usdt=assessment.target_isolated_margin_usdt,
                leverage=assessment.position_leverage,
                created_bar_time_ms=candidate.timestamp_ms,
                status=PaperOrderStatus.REJECTED,
                rejection_reason=reason or "Account risk limits exceeded.",
                entry_category=entry_cat_str,
            )
            self.journal.save_paper_order(rejected_order.to_dict())
            self.event_dispatcher.emit_risk_blocked(
                symbol=sym,
                action="SUBMIT_PAPER_ORDER",
                rule="Account Risk Limits",
                reason=reason or "Account risk limits exceeded.",
            )
            self.event_dispatcher.dispatch(
                PaperEvent(
                    event_type=PaperEventType.SETUP_REJECTED,
                    timestamp_ms=now_ms,
                    symbol=sym,
                    data={
                        "order_id": order_id,
                        "candidate_id": candidate.candidate_id,
                        "direction": candidate.direction.value,
                        "reason": reason,
                        "status": "RISK_REJECTED",
                    },
                )
            )
            return None

        # 5. Create simulated limit order
        order = PaperOrder(
            order_id=order_id,
            candidate_id=candidate.candidate_id,
            symbol=sym,
            direction=candidate.direction,
            order_type="LIMIT",
            price=candidate.entry_price,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit_1,
            quantity=assessment.quantity,
            notional_usdt=assessment.position_notional_usdt,
            margin_usdt=assessment.target_isolated_margin_usdt,
            leverage=assessment.position_leverage,
            created_bar_time_ms=candidate.timestamp_ms,
            status=PaperOrderStatus.CREATED,
            entry_category=entry_cat_str,
        )
        order.transition_to(PaperOrderStatus.PENDING)

        # 6. Persist and track
        self.journal.save_paper_order(order.to_dict())
        self.pending_orders[order.order_id] = order

        self.event_dispatcher.dispatch(
            PaperEvent(
                event_type=PaperEventType.ORDER_CREATED,
                timestamp_ms=now_ms,
                symbol=sym,
                data={
                    "order_id": order.order_id,
                    "direction": order.direction.value,
                    "order_type": order.order_type,
                    "price": order.price,
                    "quantity": order.quantity,
                    "notional_usdt": order.notional_usdt,
                    "leverage": order.leverage,
                    "stop_loss": order.stop_loss,
                    "take_profit": order.take_profit,
                    "ttl_bars": self.config.pending_expiry_bars,
                },
            )
        )
        return order

    def evaluate_pending_orders(
        self,
        candles_by_symbol: Dict[str, List[Candle]],
        now_ms: int,
    ) -> List[PaperOrder]:
        """Evaluate pending limit orders against newly closed candles.

        ANTI-LOOKAHEAD ENFORCEMENT:
        Only closed bars with timestamp_ms > order.created_bar_time_ms are evaluated.
        Bar t (where the setup formed) CANNOT fill itself.

        CONSERVATIVE FILL MODEL:
        Merely touching the entry price is insufficient; price must decisively penetrate past entry
        by the conservative_fill_buffer_pct to simulate order queue filling.
        """
        filled_or_terminal_orders: List[PaperOrder] = []

        for order_id, order in list(self.pending_orders.items()):
            candles = candles_by_symbol.get(order.symbol, [])
            # Evaluate only candles strictly after the order creation candle
            eval_candles = [c for c in candles if c.timestamp_ms > order.created_bar_time_ms]
            if not eval_candles:
                continue

            for candle in eval_candles:
                # 1. Structural Invalidation check before entry fill
                if order.direction == Direction.LONG and candle.low <= order.stop_loss:
                    order.transition_to(
                        PaperOrderStatus.INVALIDATED,
                        reason=f"Structural invalidation: 1H low ({candle.low}) hit or breached SL level ({order.stop_loss}) prior to entry fill."
                    )
                    self.journal.save_paper_order(order.to_dict())
                    self.event_dispatcher.dispatch(
                        PaperEvent(
                            event_type=PaperEventType.ORDER_CANCELLED,
                            timestamp_ms=candle.timestamp_ms,
                            symbol=order.symbol,
                            data={
                                "order_id": order.order_id,
                                "direction": order.direction.value,
                                "planned_price": order.price,
                                "reason": order.rejection_reason,
                            },
                        )
                    )
                    filled_or_terminal_orders.append(order)
                    self.pending_orders.pop(order_id, None)
                    break

                if order.direction == Direction.SHORT and candle.high >= order.stop_loss:
                    order.transition_to(
                        PaperOrderStatus.INVALIDATED,
                        reason=f"Structural invalidation: 1H high ({candle.high}) hit or breached SL level ({order.stop_loss}) prior to entry fill."
                    )
                    self.journal.save_paper_order(order.to_dict())
                    self.event_dispatcher.dispatch(
                        PaperEvent(
                            event_type=PaperEventType.ORDER_CANCELLED,
                            timestamp_ms=candle.timestamp_ms,
                            symbol=order.symbol,
                            data={
                                "order_id": order.order_id,
                                "direction": order.direction.value,
                                "planned_price": order.price,
                                "reason": order.rejection_reason,
                            },
                        )
                    )
                    filled_or_terminal_orders.append(order)
                    self.pending_orders.pop(order_id, None)
                    break

                # 2. Expiry check
                order.evaluated_bars_count += 1
                if order.evaluated_bars_count >= self.config.pending_expiry_bars:
                    order.transition_to(
                        PaperOrderStatus.EXPIRED,
                        reason=f"Order expired after {self.config.pending_expiry_bars} bars without decisive fill penetration."
                    )
                    self.journal.save_paper_order(order.to_dict())
                    self.event_dispatcher.dispatch(
                        PaperEvent(
                            event_type=PaperEventType.ORDER_EXPIRED,
                            timestamp_ms=candle.timestamp_ms,
                            symbol=order.symbol,
                            data={
                                "order_id": order.order_id,
                                "direction": order.direction.value,
                                "planned_price": order.price,
                                "reason": order.rejection_reason,
                                "evaluated_bars": order.evaluated_bars_count,
                            },
                        )
                    )
                    filled_or_terminal_orders.append(order)
                    self.pending_orders.pop(order_id, None)
                    break

                # 3. Conservative Fill Evaluation
                buffer_pct = self.config.conservative_fill_buffer_pct
                is_filled = False

                if order.direction == Direction.LONG:
                    fill_threshold = order.price * (1.0 - buffer_pct)
                    if candle.low <= fill_threshold:
                        is_filled = True
                elif order.direction == Direction.SHORT:
                    fill_threshold = order.price * (1.0 + buffer_pct)
                    if candle.high >= fill_threshold:
                        is_filled = True

                if is_filled:
                    fill_price = order.price  # Limit order fills at limit price
                    fee = order.notional_usdt * self.config.maker_fee_rate  # Maker fee for limit fill

                    order.fill_price = fill_price
                    order.fee_paid = fee
                    order.filled_time_ms = candle.timestamp_ms
                    order.transition_to(PaperOrderStatus.FILLED)

                    self.journal.save_paper_order(order.to_dict())
                    pos = self._open_position(order, candle)

                    self.event_dispatcher.dispatch(
                        PaperEvent(
                            event_type=PaperEventType.ORDER_FILLED,
                            timestamp_ms=candle.timestamp_ms,
                            symbol=order.symbol,
                            data={
                                "order_id": order.order_id,
                                "direction": order.direction.value,
                                "fill_price": fill_price,
                                "planned_price": order.price,
                                "slippage_usdt": 0.0,
                                "slippage_pct": 0.0,
                                "quantity": order.quantity,
                                "fee": fee,
                                "stop_loss": order.stop_loss,
                                "take_profit": order.take_profit,
                                "trade_id": pos.trade_id,
                            },
                        )
                    )
                    filled_or_terminal_orders.append(order)
                    self.pending_orders.pop(order_id, None)
                    break

        return filled_or_terminal_orders

    def _open_position(self, order: PaperOrder, fill_candle: Candle) -> PaperPosition:
        """Create and persist an active open position and register it with the RiskManager."""
        trade_id = f"PTRD_{order.symbol}_{order.created_bar_time_ms}"

        pos = PaperPosition(
            trade_id=trade_id,
            order_id=order.order_id,
            setup_id=order.candidate_id,
            symbol=order.symbol,
            direction=order.direction,
            entry_timestamp_ms=order.filled_time_ms or fill_candle.timestamp_ms,
            entry_price=order.fill_price or order.price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            quantity=order.quantity,
            notional_usdt=order.notional_usdt,
            margin_usdt=order.margin_usdt,
            leverage=order.leverage,
            entry_fee=order.fee_paid,
            funding_paid=0.0,
            is_open=True,
            entry_category=order.entry_category,
        )

        trade_record = TradeRecord(
            trade_id=trade_id,
            symbol=order.symbol,
            direction=order.direction,
            entry_timestamp_ms=pos.entry_timestamp_ms,
            entry_price=pos.entry_price,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            quantity=pos.quantity,
            notional_usdt=pos.notional_usdt,
            fee_paid=pos.entry_fee,
            funding_paid=0.0,
            is_open=True,
            setup_id=order.candidate_id,
            timeframe="1h",
            order_status="FILLED",
            leverage=pos.leverage,
            margin_usdt=pos.margin_usdt,
            slippage_paid=0.0,
            fill_model_version="v1_conservative",
            data_source="asterdex_live",
            entry_category=EntryCategory(order.entry_category),
        )

        self.journal.save_trade(trade_record)
        self.risk_manager.record_entry(trade_record)
        self.open_positions[pos.symbol] = pos
        return pos

    def evaluate_open_positions(
        self,
        candles_by_symbol: Dict[str, List[Candle]],
        now_ms: int,
    ) -> List[TradeRecord]:
        """Evaluate open positions for funding settlement and TP/SL execution.

        CONSERVATIVE COLLISION RESOLUTION:
        If both TP and SL are crossed within the same bar, assume SL executes first.
        Marked explicitly with exit_reason='SL_COLLISION'.

        FUNDING SETTLEMENT:
        Calculated for all 8-hour intervals crossed based on real funding rates stored in DB.
        If funding rate data is absent, logged and flagged explicitly (funding_data_missing=True).
        """
        closed_trades: List[TradeRecord] = []

        for sym, pos in list(self.open_positions.items()):
            candles = candles_by_symbol.get(sym, [])
            eval_candles = [c for c in candles if c.timestamp_ms > pos.entry_timestamp_ms]
            if not eval_candles:
                continue

            for candle in eval_candles:
                # 1. Calculate intermediate funding payments
                self._settle_funding(pos, candle.timestamp_ms)

                hit_tp = False
                hit_sl = False
                exit_price = 0.0
                exit_reason = ""
                slippage_paid = 0.0

                if pos.direction == Direction.LONG:
                    hit_tp = candle.high >= pos.take_profit
                    hit_sl = candle.low <= pos.stop_loss

                    if hit_tp and hit_sl:
                        # Conservative institutional assumption: SL triggers first
                        exit_price = pos.stop_loss * (1.0 - self.config.slippage_rate)
                        exit_reason = "SL_COLLISION"
                        slippage_paid = abs(exit_price - pos.stop_loss) * pos.quantity
                    elif hit_sl:
                        exit_price = pos.stop_loss * (1.0 - self.config.slippage_rate)
                        exit_reason = "STOP_LOSS"
                        slippage_paid = abs(exit_price - pos.stop_loss) * pos.quantity
                    elif hit_tp:
                        exit_price = pos.take_profit
                        exit_reason = "TAKE_PROFIT"
                        slippage_paid = 0.0

                elif pos.direction == Direction.SHORT:
                    hit_tp = candle.low <= pos.take_profit
                    hit_sl = candle.high >= pos.stop_loss

                    if hit_tp and hit_sl:
                        # Conservative institutional assumption: SL triggers first
                        exit_price = pos.stop_loss * (1.0 + self.config.slippage_rate)
                        exit_reason = "SL_COLLISION"
                        slippage_paid = abs(exit_price - pos.stop_loss) * pos.quantity
                    elif hit_sl:
                        exit_price = pos.stop_loss * (1.0 + self.config.slippage_rate)
                        exit_reason = "STOP_LOSS"
                        slippage_paid = abs(exit_price - pos.stop_loss) * pos.quantity
                    elif hit_tp:
                        exit_price = pos.take_profit
                        exit_reason = "TAKE_PROFIT"
                        slippage_paid = 0.0

                if hit_tp or hit_sl:
                    # Position is closed!
                    exit_fee = (pos.quantity * exit_price) * self.config.taker_fee_rate
                    total_fee = pos.entry_fee + exit_fee

                    if pos.direction == Direction.LONG:
                        gross_pnl = pos.quantity * (exit_price - pos.entry_price)
                    else:
                        gross_pnl = pos.quantity * (pos.entry_price - exit_price)

                    net_pnl = gross_pnl - total_fee - pos.funding_paid

                    closed_trade = TradeRecord(
                        trade_id=pos.trade_id,
                        symbol=pos.symbol,
                        direction=pos.direction,
                        entry_timestamp_ms=pos.entry_timestamp_ms,
                        entry_price=pos.entry_price,
                        stop_loss=pos.stop_loss,
                        take_profit=pos.take_profit,
                        quantity=pos.quantity,
                        notional_usdt=pos.notional_usdt,
                        exit_timestamp_ms=candle.timestamp_ms,
                        exit_price=exit_price,
                        pnl_gross=gross_pnl,
                        pnl_net=net_pnl,
                        fee_paid=total_fee,
                        funding_paid=pos.funding_paid,
                        exit_reason=exit_reason,
                        is_open=False,
                        setup_id=pos.setup_id,
                        timeframe="1h",
                        order_status="CLOSED",
                        leverage=pos.leverage,
                        margin_usdt=pos.margin_usdt,
                        slippage_paid=slippage_paid,
                        fill_model_version="v1_conservative",
                        data_source="asterdex_live",
                        funding_data_missing=pos.funding_data_missing,
                        entry_category=EntryCategory(pos.entry_category),
                    )

                    # Update database and risk governance
                    self.journal.save_trade(closed_trade)
                    self.risk_manager.record_exit(closed_trade)
                    self.open_positions.pop(sym, None)
                    closed_trades.append(closed_trade)

                    # Dispatch event
                    ev_type = PaperEventType.SL_HIT if "SL" in exit_reason else PaperEventType.TP_HIT
                    risk_dist = abs(pos.entry_price - pos.stop_loss)
                    gain_dist = (exit_price - pos.entry_price) if pos.direction == Direction.LONG else (pos.entry_price - exit_price)
                    realized_rr = (gain_dist / risk_dist) if risk_dist > 0 else 0.0
                    duration_hrs = (candle.timestamp_ms - pos.entry_timestamp_ms) / 3600000.0

                    self.event_dispatcher.dispatch(
                        PaperEvent(
                            event_type=ev_type,
                            timestamp_ms=candle.timestamp_ms,
                            symbol=pos.symbol,
                            data={
                                "trade_id": pos.trade_id,
                                "direction": pos.direction.value,
                                "exit_price": exit_price,
                                "planned_tp": pos.take_profit,
                                "planned_sl": pos.stop_loss,
                                "pnl_gross": gross_pnl,
                                "pnl_net": net_pnl,
                                "fee_paid": total_fee,
                                "exit_reason": exit_reason,
                                "realized_rr": realized_rr,
                                "duration": f"{duration_hrs:.1f}h",
                            },
                        )
                    )
                    break

        return closed_trades

    def _settle_funding(self, pos: PaperPosition, current_candle_ts_ms: int) -> None:
        """Settle 8-hour funding payments between entry and current timestamp."""
        # Query recorded funding rates for this symbol
        rates = self.db.get_funding_rates(pos.symbol)
        for r in rates:
            f_time = r["funding_time_ms"]
            # Check if this funding settlement happened while position was open and not yet settled
            if pos.entry_timestamp_ms < f_time <= current_candle_ts_ms:
                if f_time not in pos.settled_funding_timestamps:
                    rate = float(r["funding_rate"])
                    # Positive rate: Long pays short, Short receives
                    if pos.direction == Direction.LONG:
                        payment = pos.notional_usdt * rate
                    else:
                        payment = pos.notional_usdt * (-rate)

                    pos.funding_paid += payment
                    pos.settled_funding_timestamps.append(f_time)
                    logger.debug(
                        f"PaperEngine: Settled funding for {pos.symbol} at {f_time}: "
                        f"rate={rate:.6f}, payment={payment:.4f} USDT."
                    )
