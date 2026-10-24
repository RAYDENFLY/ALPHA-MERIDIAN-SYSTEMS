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
import json

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
        model_id: str = "MODEL_A",
    ) -> None:
        self.db = db
        self.journal = journal
        self.risk_manager = risk_manager
        self.config = config or PaperExecutionConfig()
        self.event_dispatcher = event_dispatcher or PaperEventDispatcher(self.config.discord_webhook_url)
        self.model_id = model_id

        self.pending_orders: Dict[str, PaperOrder] = {}
        self.open_positions: Dict[str, PaperPosition] = {}

        # Recover persistent state on initialization (safe across restarts)
        self.recover_state()

    def _serialize_position_details(self, pos: PaperPosition) -> str:
        """Serialize extended position state (TP1/TP2, trailing stop, partial PnL) to JSON."""
        d = pos.to_dict()
        if pos.model_id == "MODEL_B_FAST":
            d["cohort_version"] = "MODEL_B_FAST_V2"
            d["timeframe"] = "15m"
        elif pos.model_id == "MODEL_B":
            d["cohort_version"] = "MODEL_B_V2_REMEDIATED"
            d["timeframe"] = "1h"
        return json.dumps(d)

    def _build_open_trade_record(self, pos: PaperPosition, order_status: str = "FILLED") -> TradeRecord:
        """Construct an open TradeRecord reflecting current PaperPosition state for SQLite persistence."""
        active_model = pos.model_id or self.model_id
        tf_str = "15m" if active_model == "MODEL_B_FAST" else "1h"
        return TradeRecord(
            trade_id=pos.trade_id,
            symbol=pos.symbol,
            direction=pos.direction,
            entry_timestamp_ms=pos.entry_timestamp_ms,
            entry_price=pos.entry_price,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            quantity=pos.quantity,
            notional_usdt=pos.notional_usdt,
            pnl_gross=pos.realized_partial_pnl_gross,
            pnl_net=pos.realized_partial_pnl_gross - pos.entry_fee - pos.realized_partial_fee_paid - pos.funding_paid,
            fee_paid=pos.entry_fee + pos.realized_partial_fee_paid,
            funding_paid=pos.funding_paid,
            is_open=True,
            setup_id=pos.setup_id,
            timeframe=tf_str,
            order_status=order_status,
            leverage=pos.leverage,
            margin_usdt=pos.margin_usdt,
            slippage_paid=0.0,
            fill_model_version="v1_conservative",
            data_source="asterdex_live",
            funding_data_missing=pos.funding_data_missing,
            details_json=self._serialize_position_details(pos),
            entry_category=EntryCategory(pos.entry_category),
            model_id=active_model,
        )

    def recover_state(self) -> None:
        """Recover active pending orders, open positions, and risk manager meters from SQLite storage."""
        self.pending_orders.clear()
        self.open_positions.clear()

        # 0. Recover RiskManager authoritative realized losses, equity, and quota counters
        if hasattr(self.risk_manager, "sync_state_from_journal") and self.journal is not None:
            self.risk_manager.sync_state_from_journal(self.journal, model_id=self.model_id)

        # 1. Recover pending orders
        raw_orders = self.journal.get_paper_orders(status=PaperOrderStatus.PENDING.value, model_id=self.model_id)
        for row in raw_orders:
            try:
                order = PaperOrder.from_dict(row)
                self.pending_orders[order.order_id] = order
            except Exception as err:
                logger.error(f"Failed to recover paper order from row {row}: {err}")

        # 2. Recover open trades into active positions & sync with RiskManager
        raw_trades = self.journal.get_open_trades(model_id=self.model_id)
        for row in raw_trades:
            try:
                direction_str = row["direction"]
                direction = Direction.LONG if str(direction_str).upper() == "LONG" else Direction.SHORT

                details: Dict[str, Any] = {}
                raw_details = row.get("details_json")
                if raw_details and isinstance(raw_details, str):
                    try:
                        details = json.loads(raw_details)
                    except Exception:
                        details = {}

                entry_price = float(row["entry_price"])
                stop_loss = float(row["stop_loss"])
                take_profit = float(row["take_profit"])
                quantity = float(row["quantity"])
                fee_paid = float(row.get("fee_paid", 0.0))
                realized_partial_fee = float(details.get("realized_partial_fee_paid", 0.0))
                entry_fee = float(details.get("entry_fee", max(0.0, fee_paid - realized_partial_fee)))

                pos = PaperPosition(
                    trade_id=row["trade_id"],
                    order_id=details.get("order_id") or row.get("order_id") or f"REC_{row['trade_id']}",
                    setup_id=row.get("setup_id", ""),
                    symbol=row["symbol"],
                    direction=direction,
                    entry_timestamp_ms=int(row["entry_timestamp_ms"]),
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    quantity=quantity,
                    notional_usdt=float(row["notional_usdt"]),
                    margin_usdt=float(row.get("margin_usdt", 5.0)),
                    leverage=float(row.get("leverage", 10.0)),
                    entry_fee=entry_fee,
                    model_id=str(row.get("model_id") or self.model_id),
                    funding_paid=float(row.get("funding_paid", 0.0)),
                    funding_data_missing=bool(row.get("funding_data_missing", False)),
                    settled_funding_timestamps=list(details.get("settled_funding_timestamps", [])),
                    is_open=True,
                    entry_category=str(row.get("entry_category", "Unclassified")),
                    initial_stop_loss=float(details["initial_stop_loss"]) if details.get("initial_stop_loss") is not None else stop_loss,
                    initial_quantity=float(details["initial_quantity"]) if details.get("initial_quantity") is not None else quantity,
                    take_profit_1=float(details["take_profit_1"]) if details.get("take_profit_1") is not None else take_profit,
                    take_profit_2=float(details["take_profit_2"]) if details.get("take_profit_2") is not None else None,
                    tp1_hit=bool(details.get("tp1_hit", False)),
                    tp1_hit_timestamp_ms=int(details["tp1_hit_timestamp_ms"]) if details.get("tp1_hit_timestamp_ms") is not None else None,
                    realized_partial_pnl_gross=float(details.get("realized_partial_pnl_gross", 0.0)),
                    realized_partial_fee_paid=realized_partial_fee,
                    trailing_active=bool(details.get("trailing_active", False)),
                    highest_favorable_price=float(details["highest_favorable_price"]) if details.get("highest_favorable_price") is not None else None,
                    lowest_favorable_price=float(details["lowest_favorable_price"]) if details.get("lowest_favorable_price") is not None else None,
                    trailing_distance=float(details["trailing_distance"]) if details.get("trailing_distance") is not None else None,
                    breakeven_stop_price=float(details["breakeven_stop_price"]) if details.get("breakeven_stop_price") is not None else None,
                )
                self.open_positions[pos.symbol] = pos

                # Synchronize with RiskManager open_trades if not present
                trade_record = self._build_open_trade_record(pos, order_status=row.get("order_status", "FILLED"))
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

        order_prefix = "PORD_FAST" if self.model_id == "MODEL_B_FAST" else "PORD"
        order_id = f"{order_prefix}_{sym}_{candidate.timestamp_ms}"

        # 3. Prevent duplicate order submission across restarts
        existing_orders = self.journal.get_paper_orders(symbol=sym, model_id=self.model_id)
        if any(o["order_id"] == order_id for o in existing_orders):
            logger.info(f"PaperEngine: Order {order_id} already exists in database; skipping.")
            return None

        entry_cat_str = (
            candidate.entry_category.value
            if hasattr(candidate.entry_category, "value")
            else str(candidate.entry_category)
        )

        # 4. Account Risk Gate Check
        can_open, reason = self.risk_manager.can_open_new_position(now_ms, direction=candidate.direction)
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
                take_profit_2=candidate.take_profit_2,
                quantity=assessment.quantity,
                notional_usdt=assessment.position_notional_usdt,
                margin_usdt=assessment.target_isolated_margin_usdt,
                leverage=assessment.position_leverage,
                created_bar_time_ms=candidate.timestamp_ms,
                status=PaperOrderStatus.REJECTED,
                rejection_reason=reason or "Account risk limits exceeded.",
                entry_category=entry_cat_str,
                model_id=self.model_id,
            )
            self.journal.save_paper_order(rejected_order.to_dict())
            self._log_lifecycle_event(
                candidate_id=candidate.candidate_id,
                symbol=sym,
                stage="RISK_VALIDATION",
                timestamp_ms=now_ms,
                reason_code="ACCOUNT_RISK_REJECTED",
                reason_detail=reason or "Account risk limits exceeded.",
                order_id=order_id,
            )
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
            take_profit_2=candidate.take_profit_2,
            quantity=assessment.quantity,
            notional_usdt=assessment.position_notional_usdt,
            margin_usdt=assessment.target_isolated_margin_usdt,
            leverage=assessment.position_leverage,
            created_bar_time_ms=candidate.timestamp_ms,
            status=PaperOrderStatus.CREATED,
            entry_category=entry_cat_str,
            model_id=self.model_id,
        )
        order.transition_to(PaperOrderStatus.PENDING)

        # 6. Persist and track
        self.journal.save_paper_order(order.to_dict())
        self.pending_orders[order.order_id] = order

        self._log_lifecycle_event(
            candidate_id=candidate.candidate_id,
            symbol=sym,
            stage="PAPER_ORDER_CREATED",
            timestamp_ms=now_ms,
            reason_code="LIMIT_ORDER_PENDING",
            reason_detail=f"Created pending LIMIT order {order.order_id} at {order.price}",
            order_id=order.order_id,
            metadata={"price": order.price, "quantity": order.quantity, "notional_usdt": order.notional_usdt},
        )

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
                    "take_profit_2": order.take_profit_2,
                    "ttl_bars": self.config.pending_expiry_bars,
                },
            )
        )
        return order

    def _log_lifecycle_event(
        self,
        candidate_id: str,
        symbol: str,
        stage: str,
        timestamp_ms: int,
        reason_code: str,
        reason_detail: str = "",
        order_id: Optional[str] = None,
        trade_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist an auditable lifecycle transition event if supported by the journal."""
        if hasattr(self.journal, "save_lifecycle_event"):
            try:
                self.journal.save_lifecycle_event(
                    candidate_id=candidate_id,
                    symbol=symbol,
                    stage=stage,
                    timestamp_ms=timestamp_ms,
                    reason_code=reason_code,
                    reason_detail=reason_detail,
                    order_id=order_id,
                    trade_id=trade_id,
                    metadata=metadata,
                    model_id=self.model_id,
                )
            except Exception as lc_err:
                logger.debug(f"Lifecycle event log skipped: {lc_err}")

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

        # Model B diagnostic kill switch: a fill is a new entry, so cancel resting paper orders instead
        # of filling them. Risk managers without this attribute (Model A) are unaffected.
        if getattr(self.risk_manager, "entry_kill_switch", False):
            for order_id, order in list(self.pending_orders.items()):
                order.transition_to(PaperOrderStatus.CANCELLED, reason="Model B entry kill switch ON: pending paper order cancelled.")
                self.journal.save_paper_order(order.to_dict())
                filled_or_terminal_orders.append(order)
                self.pending_orders.pop(order_id, None)
            return filled_or_terminal_orders

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
                    self._log_lifecycle_event(
                        candidate_id=order.candidate_id,
                        symbol=order.symbol,
                        stage="PAPER_ORDER_CREATED",
                        timestamp_ms=candle.timestamp_ms,
                        reason_code="ORDER_INVALIDATED_PRE_FILL",
                        reason_detail=order.rejection_reason or "",
                        order_id=order.order_id,
                    )
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
                    self._log_lifecycle_event(
                        candidate_id=order.candidate_id,
                        symbol=order.symbol,
                        stage="PAPER_ORDER_CREATED",
                        timestamp_ms=candle.timestamp_ms,
                        reason_code="ORDER_INVALIDATED_PRE_FILL",
                        reason_detail=order.rejection_reason or "",
                        order_id=order.order_id,
                    )
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
                    self._log_lifecycle_event(
                        candidate_id=order.candidate_id,
                        symbol=order.symbol,
                        stage="PAPER_ORDER_CREATED",
                        timestamp_ms=candle.timestamp_ms,
                        reason_code="ORDER_EXPIRED_TTL",
                        reason_detail=order.rejection_reason or "",
                        order_id=order.order_id,
                    )
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
                    self._log_lifecycle_event(
                        candidate_id=order.candidate_id,
                        symbol=order.symbol,
                        stage="ENTRY_FILLED",
                        timestamp_ms=candle.timestamp_ms,
                        reason_code="CONSERVATIVE_LIMIT_PENETRATED",
                        reason_detail=f"Order {order.order_id} filled at {fill_price:.4f} (maker fee=${fee:.4f})",
                        order_id=order.order_id,
                        metadata={"fill_price": fill_price, "fee_paid": fee},
                    )
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
                                "take_profit_2": order.take_profit_2,
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
        trade_prefix = "PTRD_FAST" if self.model_id == "MODEL_B_FAST" else "PTRD"
        trade_id = f"{trade_prefix}_{order.symbol}_{order.created_bar_time_ms}"
        entry_price = order.fill_price or order.price
        initial_r = abs(entry_price - order.stop_loss)
        trail_dist = initial_r * self.config.trailing_stop_distance_r if initial_r > 0 else 0.0

        pos = PaperPosition(
            trade_id=trade_id,
            order_id=order.order_id,
            setup_id=order.candidate_id,
            symbol=order.symbol,
            direction=order.direction,
            entry_timestamp_ms=order.filled_time_ms or fill_candle.timestamp_ms,
            entry_price=entry_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            quantity=order.quantity,
            notional_usdt=order.notional_usdt,
            margin_usdt=order.margin_usdt,
            leverage=order.leverage,
            entry_fee=order.fee_paid,
            model_id=self.model_id,
            funding_paid=0.0,
            is_open=True,
            entry_category=order.entry_category,
            initial_stop_loss=order.stop_loss,
            initial_quantity=order.quantity,
            take_profit_1=order.take_profit,
            take_profit_2=order.take_profit_2,
            tp1_hit=False,
            trailing_active=False,
            trailing_distance=trail_dist,
        )

        trade_record = self._build_open_trade_record(pos, order_status="FILLED")

        self.journal.save_trade(trade_record)
        self.risk_manager.record_entry(trade_record)
        self.open_positions[pos.symbol] = pos
        self._log_lifecycle_event(
            candidate_id=order.candidate_id,
            symbol=pos.symbol,
            stage="POSITION_OPEN",
            timestamp_ms=pos.entry_timestamp_ms,
            reason_code="POSITION_ACTIVE",
            reason_detail=f"Opened position {pos.trade_id} ({pos.direction.value}) qty={pos.quantity}",
            order_id=order.order_id,
            trade_id=pos.trade_id,
            metadata={
                "entry_price": pos.entry_price,
                "stop_loss": pos.stop_loss,
                "take_profit_1": pos.take_profit_1,
                "take_profit_2": pos.take_profit_2,
            },
        )
        return pos

    def _compute_fee_adjusted_breakeven(self, pos: PaperPosition) -> float:
        """Compute fee-and-slippage-buffered break-even stop price for the runner leg."""
        round_trip_buffer = (
            self.config.maker_fee_rate
            + self.config.taker_fee_rate
            + self.config.slippage_rate
        )
        if pos.direction == Direction.LONG:
            return pos.entry_price * (1.0 + round_trip_buffer)
        else:
            return pos.entry_price * (1.0 - round_trip_buffer)

    def _execute_tp1_partial_and_arm_trailing(self, pos: PaperPosition, candle: Candle) -> None:
        """Execute partial position close at TP1, move SL to fee-adjusted break-even, and arm trailing stop."""
        tp1_price = pos.take_profit_1 if pos.take_profit_1 is not None else pos.take_profit
        partial_pct = min(max(self.config.tp1_partial_close_pct, 0.0), 1.0)
        closed_qty = round(pos.quantity * partial_pct, 8)
        remaining_qty = round(pos.quantity - closed_qty, 8)

        if pos.direction == Direction.LONG:
            partial_gross_pnl = closed_qty * (tp1_price - pos.entry_price)
        else:
            partial_gross_pnl = closed_qty * (pos.entry_price - tp1_price)

        partial_exit_fee = (closed_qty * tp1_price) * self.config.taker_fee_rate
        allocated_entry_fee = pos.entry_fee * partial_pct
        partial_net_pnl = partial_gross_pnl - partial_exit_fee - allocated_entry_fee

        pos.realized_partial_pnl_gross += partial_gross_pnl
        pos.realized_partial_fee_paid += partial_exit_fee
        pos.quantity = remaining_qty
        pos.notional_usdt = remaining_qty * pos.entry_price
        pos.margin_usdt = pos.notional_usdt / pos.leverage if pos.leverage > 0 else pos.margin_usdt

        pos.tp1_hit = True
        pos.tp1_hit_timestamp_ms = candle.timestamp_ms
        pos.trailing_active = True

        initial_sl = pos.initial_stop_loss if pos.initial_stop_loss is not None else pos.stop_loss
        initial_r = abs(pos.entry_price - initial_sl)
        pos.trailing_distance = initial_r * self.config.trailing_stop_distance_r

        # Move stop-loss to fee-adjusted break-even
        be_price = self._compute_fee_adjusted_breakeven(pos)
        pos.breakeven_stop_price = be_price
        if self.config.move_sl_to_breakeven_on_tp1:
            if pos.direction == Direction.LONG:
                pos.stop_loss = max(pos.stop_loss, be_price)
            else:
                pos.stop_loss = min(pos.stop_loss, be_price)

        # Transition active target from TP1 to TP2
        if pos.take_profit_2 is not None:
            pos.take_profit = pos.take_profit_2

        # Initialize favorable price watermark at TP1 candle extremum and ratchet if favorable beyond BE
        if pos.direction == Direction.LONG:
            pos.highest_favorable_price = max(tp1_price, candle.high)
            if pos.trailing_distance and pos.trailing_distance > 0:
                candidate_trail = pos.highest_favorable_price - pos.trailing_distance
                if candidate_trail > pos.stop_loss:
                    pos.stop_loss = candidate_trail
        else:
            pos.lowest_favorable_price = min(tp1_price, candle.low)
            if pos.trailing_distance and pos.trailing_distance > 0:
                candidate_trail = pos.lowest_favorable_price + pos.trailing_distance
                if candidate_trail < pos.stop_loss:
                    pos.stop_loss = candidate_trail

        # Persist updated open position state in trade_journal
        open_record = self._build_open_trade_record(pos, order_status="PARTIALLY_CLOSED_TP1")
        self.journal.save_trade(open_record)
        self._log_lifecycle_event(
            candidate_id=pos.setup_id,
            symbol=pos.symbol,
            stage="EXIT_TRIGGERED",
            timestamp_ms=candle.timestamp_ms,
            reason_code="TP1_PARTIAL_CLOSE_AND_TRAILING_ARMED",
            reason_detail=f"TP1 reached at {tp1_price:.4f}; closed {partial_pct*100:.0f}%, SL moved to {pos.stop_loss:.4f}",
            order_id=pos.order_id,
            trade_id=pos.trade_id,
            metadata={"tp1_price": tp1_price, "new_stop_loss": pos.stop_loss, "partial_pnl_net": partial_net_pnl},
        )

        # Sync RiskManager open trade snapshot
        for idx, t in enumerate(self.risk_manager.open_trades):
            if t.trade_id == pos.trade_id:
                self.risk_manager.open_trades[idx] = open_record
                break

        self.event_dispatcher.dispatch(
            PaperEvent(
                event_type=PaperEventType.TP1_PARTIAL_HIT,
                timestamp_ms=candle.timestamp_ms,
                symbol=pos.symbol,
                data={
                    "trade_id": pos.trade_id,
                    "direction": pos.direction.value,
                    "tp1_price": tp1_price,
                    "closed_pct": partial_pct,
                    "closed_quantity": closed_qty,
                    "remaining_quantity": remaining_qty,
                    "partial_pnl_gross": partial_gross_pnl,
                    "partial_pnl_net": partial_net_pnl,
                    "new_stop_loss": pos.stop_loss,
                    "breakeven_stop_price": be_price,
                    "take_profit_2": pos.take_profit_2,
                    "trailing_distance": pos.trailing_distance,
                },
            )
        )

    def _ratchet_trailing_stop(self, pos: PaperPosition, candle: Candle) -> bool:
        """Ratchet trailing stop monotonically in the favorable direction. Returns True if updated."""
        if not pos.trailing_active:
            return False

        initial_sl = pos.initial_stop_loss if pos.initial_stop_loss is not None else pos.stop_loss
        trail_dist = pos.trailing_distance or (abs(pos.entry_price - initial_sl) * self.config.trailing_stop_distance_r)
        if trail_dist <= 0:
            return False

        old_sl = pos.stop_loss
        updated = False

        if pos.direction == Direction.LONG:
            prev_high = pos.highest_favorable_price if pos.highest_favorable_price is not None else candle.high
            pos.highest_favorable_price = max(prev_high, candle.high)
            candidate_sl = pos.highest_favorable_price - trail_dist
            min_allowed_sl = pos.breakeven_stop_price if pos.breakeven_stop_price is not None else old_sl
            new_sl = max(old_sl, min_allowed_sl, candidate_sl)
            if new_sl > old_sl + 1e-8:
                pos.stop_loss = new_sl
                updated = True
                favorable_extreme = pos.highest_favorable_price
        else:
            prev_low = pos.lowest_favorable_price if pos.lowest_favorable_price is not None else candle.low
            pos.lowest_favorable_price = min(prev_low, candle.low)
            candidate_sl = pos.lowest_favorable_price + trail_dist
            max_allowed_sl = pos.breakeven_stop_price if pos.breakeven_stop_price is not None else old_sl
            new_sl = min(old_sl, max_allowed_sl, candidate_sl)
            if new_sl < old_sl - 1e-8:
                pos.stop_loss = new_sl
                updated = True
                favorable_extreme = pos.lowest_favorable_price

        if updated:
            open_record = self._build_open_trade_record(pos, order_status="TRAILING_ACTIVE")
            self.journal.save_trade(open_record)
            for idx, t in enumerate(self.risk_manager.open_trades):
                if t.trade_id == pos.trade_id:
                    self.risk_manager.open_trades[idx] = open_record
                    break

            self.event_dispatcher.dispatch(
                PaperEvent(
                    event_type=PaperEventType.TRAILING_STOP_UPDATED,
                    timestamp_ms=candle.timestamp_ms,
                    symbol=pos.symbol,
                    data={
                        "trade_id": pos.trade_id,
                        "direction": pos.direction.value,
                        "old_stop_loss": old_sl,
                        "new_stop_loss": pos.stop_loss,
                        "favorable_extreme": favorable_extreme,
                        "trailing_distance": trail_dist,
                        "take_profit_2": pos.take_profit_2,
                    },
                )
            )
        return updated

    def evaluate_open_positions(
        self,
        candles_by_symbol: Dict[str, List[Candle]],
        now_ms: int,
    ) -> List[TradeRecord]:
        """Evaluate open positions for funding settlement, TP1 partial close, trailing stop, and TP/SL execution.

        CONSERVATIVE COLLISION RESOLUTION:
        If both TP and SL are crossed within the same bar, assume SL executes first.
        Marked explicitly with exit_reason='SL_COLLISION'.

        POST-TP1 TRAILING STOP:
        When enable_tp1_trailing_stop is True and take_profit_2 is defined:
        - Reaching TP1 closes tp1_partial_close_pct (e.g. 50%) of the position at TP1,
          moves stop_loss to fee-adjusted break-even, and arms the trailing stop toward TP2.
        - Subsequent bars evaluate the runner leg against the active stop_loss (break-even or trailed)
          and TP2, ratcheting stop_loss monotonically when favorable highs/lows extend.
        """
        closed_trades: List[TradeRecord] = []

        for sym, pos in list(self.open_positions.items()):
            candles = candles_by_symbol.get(sym, [])
            min_eval_ts = pos.tp1_hit_timestamp_ms if (pos.tp1_hit and pos.tp1_hit_timestamp_ms) else pos.entry_timestamp_ms
            eval_candles = [c for c in candles if c.timestamp_ms > min_eval_ts]
            if not eval_candles:
                continue

            for candle in eval_candles:
                # 1. Calculate intermediate funding payments on currently open notional
                self._settle_funding(pos, candle.timestamp_ms)

                has_tp2_runner = (
                    self.config.enable_tp1_trailing_stop
                    and pos.take_profit_2 is not None
                    and 0.0 < self.config.tp1_partial_close_pct < 1.0
                )

                hit_tp = False
                hit_sl = False
                exit_price = 0.0
                exit_reason = ""
                slippage_paid = 0.0

                if pos.direction == Direction.LONG:
                    hit_tp = candle.high >= pos.take_profit
                    hit_sl = candle.low <= pos.stop_loss

                    if hit_tp and hit_sl:
                        # Conservative institutional assumption: active SL triggers first
                        exit_price = pos.stop_loss * (1.0 - self.config.slippage_rate)
                        exit_reason = "SL_COLLISION"
                        slippage_paid = abs(exit_price - pos.stop_loss) * pos.quantity
                    elif hit_sl:
                        exit_price = pos.stop_loss * (1.0 - self.config.slippage_rate)
                        if pos.tp1_hit:
                            be_ref = pos.breakeven_stop_price if pos.breakeven_stop_price is not None else pos.entry_price
                            exit_reason = "TRAILING_STOP" if pos.stop_loss > be_ref + 1e-8 else "BREAKEVEN_STOP"
                        else:
                            exit_reason = "STOP_LOSS"
                        slippage_paid = abs(exit_price - pos.stop_loss) * pos.quantity
                    elif hit_tp:
                        if not pos.tp1_hit and has_tp2_runner:
                            # Transition to TP1 partial close + break-even SL + trailing stop toward TP2
                            self._execute_tp1_partial_and_arm_trailing(pos, candle)
                            # If the same impulse bar also cleanly reaches TP2 without having breached initial SL, close runner at TP2
                            if pos.take_profit_2 is not None and candle.high >= pos.take_profit_2:
                                exit_price = pos.take_profit_2
                                exit_reason = "TAKE_PROFIT_2"
                                slippage_paid = 0.0
                            else:
                                continue
                        else:
                            exit_price = pos.take_profit
                            exit_reason = "TAKE_PROFIT_2" if pos.tp1_hit else "TAKE_PROFIT"
                            slippage_paid = 0.0

                elif pos.direction == Direction.SHORT:
                    hit_tp = candle.low <= pos.take_profit
                    hit_sl = candle.high >= pos.stop_loss

                    if hit_tp and hit_sl:
                        # Conservative institutional assumption: active SL triggers first
                        exit_price = pos.stop_loss * (1.0 + self.config.slippage_rate)
                        exit_reason = "SL_COLLISION"
                        slippage_paid = abs(exit_price - pos.stop_loss) * pos.quantity
                    elif hit_sl:
                        exit_price = pos.stop_loss * (1.0 + self.config.slippage_rate)
                        if pos.tp1_hit:
                            be_ref = pos.breakeven_stop_price if pos.breakeven_stop_price is not None else pos.entry_price
                            exit_reason = "TRAILING_STOP" if pos.stop_loss < be_ref - 1e-8 else "BREAKEVEN_STOP"
                        else:
                            exit_reason = "STOP_LOSS"
                        slippage_paid = abs(exit_price - pos.stop_loss) * pos.quantity
                    elif hit_tp:
                        if not pos.tp1_hit and has_tp2_runner:
                            # Transition to TP1 partial close + break-even SL + trailing stop toward TP2
                            self._execute_tp1_partial_and_arm_trailing(pos, candle)
                            if pos.take_profit_2 is not None and candle.low <= pos.take_profit_2:
                                exit_price = pos.take_profit_2
                                exit_reason = "TAKE_PROFIT_2"
                                slippage_paid = 0.0
                            else:
                                continue
                        else:
                            exit_price = pos.take_profit
                            exit_reason = "TAKE_PROFIT_2" if pos.tp1_hit else "TAKE_PROFIT"
                            slippage_paid = 0.0

                if hit_tp or hit_sl:
                    # Position is fully closed (either full single-leg exit or runner leg exit)
                    runner_exit_fee = (pos.quantity * exit_price) * self.config.taker_fee_rate
                    total_fee = pos.entry_fee + pos.realized_partial_fee_paid + runner_exit_fee

                    if pos.direction == Direction.LONG:
                        runner_gross_pnl = pos.quantity * (exit_price - pos.entry_price)
                    else:
                        runner_gross_pnl = pos.quantity * (pos.entry_price - exit_price)

                    gross_pnl = pos.realized_partial_pnl_gross + runner_gross_pnl
                    net_pnl = gross_pnl - total_fee - pos.funding_paid

                    initial_qty = pos.initial_quantity if (pos.initial_quantity and pos.initial_quantity > 0) else pos.quantity
                    if pos.tp1_hit and initial_qty > pos.quantity:
                        tp1_closed_qty = initial_qty - pos.quantity
                        tp1_fill_p = pos.take_profit_1 if pos.take_profit_1 is not None else exit_price
                        effective_exit_price = ((tp1_closed_qty * tp1_fill_p) + (pos.quantity * exit_price)) / initial_qty
                    else:
                        effective_exit_price = exit_price

                    closed_trade = TradeRecord(
                        trade_id=pos.trade_id,
                        symbol=pos.symbol,
                        direction=pos.direction,
                        entry_timestamp_ms=pos.entry_timestamp_ms,
                        entry_price=pos.entry_price,
                        stop_loss=pos.stop_loss,
                        take_profit=pos.take_profit,
                        quantity=initial_qty,
                        notional_usdt=initial_qty * pos.entry_price,
                        exit_timestamp_ms=candle.timestamp_ms,
                        exit_price=effective_exit_price,
                        pnl_gross=gross_pnl,
                        pnl_net=net_pnl,
                        fee_paid=total_fee,
                        funding_paid=pos.funding_paid,
                        exit_reason=exit_reason,
                        is_open=False,
                        setup_id=pos.setup_id,
                        timeframe="15m" if (pos.model_id or self.model_id) == "MODEL_B_FAST" else "1h",
                        order_status="CLOSED",
                        leverage=pos.leverage,
                        margin_usdt=(initial_qty * pos.entry_price) / pos.leverage if pos.leverage > 0 else pos.margin_usdt,
                        slippage_paid=slippage_paid,
                        fill_model_version="v1_conservative",
                        data_source="asterdex_live",
                        funding_data_missing=pos.funding_data_missing,
                        details_json=self._serialize_position_details(pos),
                        entry_category=EntryCategory(pos.entry_category),
                        model_id=pos.model_id or self.model_id,
                    )

                    # Update database and risk governance
                    self._log_lifecycle_event(
                        candidate_id=pos.setup_id,
                        symbol=pos.symbol,
                        stage="EXIT_TRIGGERED",
                        timestamp_ms=candle.timestamp_ms,
                        reason_code=exit_reason,
                        reason_detail=f"Exit triggered ({exit_reason}) at {exit_price:.4f}",
                        order_id=pos.order_id,
                        trade_id=pos.trade_id,
                        metadata={"exit_price": exit_price, "exit_reason": exit_reason},
                    )
                    self.journal.save_trade(closed_trade)
                    self.risk_manager.record_exit(closed_trade)
                    self.open_positions.pop(sym, None)
                    closed_trades.append(closed_trade)
                    self._log_lifecycle_event(
                        candidate_id=pos.setup_id,
                        symbol=pos.symbol,
                        stage="POSITION_CLOSED",
                        timestamp_ms=candle.timestamp_ms,
                        reason_code=exit_reason,
                        reason_detail=f"Closed {pos.trade_id} at effective price {effective_exit_price:.4f} (net PnL=${net_pnl:.4f})",
                        order_id=pos.order_id,
                        trade_id=pos.trade_id,
                        metadata={"pnl_gross": gross_pnl, "pnl_net": net_pnl, "fee_paid": total_fee},
                    )
                    self._log_lifecycle_event(
                        candidate_id=pos.setup_id,
                        symbol=pos.symbol,
                        stage="JOURNAL_FINALIZED",
                        timestamp_ms=candle.timestamp_ms,
                        reason_code="SAVED_TO_TRADE_JOURNAL",
                        reason_detail=f"Finalized closed trade {pos.trade_id} in trade_journal (order_status=CLOSED)",
                        order_id=pos.order_id,
                        trade_id=pos.trade_id,
                        metadata={"pnl_net": net_pnl, "exit_reason": exit_reason},
                    )

                    # Dispatch event
                    ev_type = (
                        PaperEventType.SL_HIT
                        if exit_reason in ("STOP_LOSS", "SL_COLLISION")
                        else PaperEventType.TP_HIT
                        if exit_reason in ("TAKE_PROFIT", "TAKE_PROFIT_2", "TRAILING_STOP")
                        else PaperEventType.POSITION_CLOSED
                    )
                    initial_sl_ref = pos.initial_stop_loss if pos.initial_stop_loss is not None else pos.stop_loss
                    risk_dist = abs(pos.entry_price - initial_sl_ref)
                    gain_dist = (
                        (effective_exit_price - pos.entry_price)
                        if pos.direction == Direction.LONG
                        else (pos.entry_price - effective_exit_price)
                    )
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
                                "exit_price": effective_exit_price,
                                "runner_exit_price": exit_price,
                                "planned_tp": pos.take_profit,
                                "planned_sl": pos.stop_loss,
                                "pnl_gross": gross_pnl,
                                "pnl_net": net_pnl,
                                "fee_paid": total_fee,
                                "exit_reason": exit_reason,
                                "realized_rr": realized_rr,
                                "duration": f"{duration_hrs:.1f}h",
                                "tp1_hit": pos.tp1_hit,
                            },
                        )
                    )
                    break

                # If position remains open and is in post-TP1 runner phase, ratchet trailing stop
                if pos.tp1_hit and pos.trailing_active:
                    self._ratchet_trailing_stop(pos, candle)

        return closed_trades

    def _settle_funding(self, pos: PaperPosition, current_candle_ts_ms: int) -> None:
        """Settle 8-hour funding payments between entry and current timestamp.

        Detects missing expected 8-hour settlement boundaries (00:00, 08:00, 16:00 UTC),
        flags pos.funding_data_missing = True, and applies a conservative 1 bps (0.0001)
        fallback penalty against position direction.
        """
        rates = self.db.get_funding_rates(pos.symbol) if self.db else []
        rate_by_time = {r["funding_time_ms"]: float(r["funding_rate"]) for r in rates}

        FUNDING_INTERVAL_MS = 28_800_000  # 8 hours
        first_boundary = ((pos.entry_timestamp_ms // FUNDING_INTERVAL_MS) + 1) * FUNDING_INTERVAL_MS
        expected_boundaries: List[int] = []
        b = first_boundary
        while b <= current_candle_ts_ms:
            expected_boundaries.append(b)
            b += FUNDING_INTERVAL_MS

        for b_time in expected_boundaries:
            if b_time in pos.settled_funding_timestamps:
                continue

            matched_rate = None
            if b_time in rate_by_time:
                matched_rate = rate_by_time[b_time]
            else:
                for r_time, r_val in rate_by_time.items():
                    if abs(r_time - b_time) <= 3_600_000:
                        matched_rate = r_val
                        break

            if matched_rate is not None:
                rate = matched_rate
            else:
                pos.funding_data_missing = True
                rate = 0.0001 if pos.direction == Direction.LONG else -0.0001

            if pos.direction == Direction.LONG:
                payment = pos.notional_usdt * rate
            else:
                payment = pos.notional_usdt * (-rate)

            pos.funding_paid += payment
            pos.settled_funding_timestamps.append(b_time)
            logger.debug(
                f"PaperEngine: Settled funding for {pos.symbol} at {b_time}: "
                f"rate={rate:.6f}, payment={payment:.4f} USDT, missing={matched_rate is None}."
            )
