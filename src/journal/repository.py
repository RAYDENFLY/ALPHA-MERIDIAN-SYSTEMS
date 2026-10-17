"""Journal Repository for Storing and Querying Setups, Rejections, and Trades.
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
from src.core.types import CandidateStatus, Direction, MarketBias, SetupCandidate, StructureState, TradeRecord
from src.data.database import DatabaseManager


class JournalRepository:
    """Manages persistence and queries for trading candidates and executed trades."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def save_setup(self, candidate: SetupCandidate) -> None:
        """Store a candidate setup in the database."""
        now_utc = datetime.now(timezone.utc).isoformat()
        details_json = json.dumps(candidate.to_dict())
        entry_cat_val = candidate.entry_category.value if hasattr(candidate.entry_category, "value") else str(candidate.entry_category)

        query = """
        INSERT OR REPLACE INTO setup_candidates (
            candidate_id, symbol, timestamp_ms, direction, bias_4h, structure_1h,
            entry_price, stop_loss, take_profit_1, take_profit_2, gross_rr, net_rr,
            invalidation_price, ml_probability, calibration_status, eval_sample_count,
            status, details_json, created_at_utc, entry_category
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            candidate.candidate_id,
            candidate.symbol,
            candidate.timestamp_ms,
            candidate.direction.value,
            candidate.bias_4h.value,
            candidate.structure_1h.value,
            candidate.entry_price,
            candidate.stop_loss,
            candidate.take_profit_1,
            candidate.take_profit_2,
            candidate.gross_reward_to_risk,
            candidate.net_reward_to_risk,
            candidate.invalidation_price,
            candidate.ml_probability,
            candidate.calibration_status,
            candidate.eval_sample_count,
            candidate.status.value,
            details_json,
            now_utc,
            entry_cat_val,
        )
        with self.db.get_connection() as conn:
            conn.execute(query, params)
            conn.commit()

    def get_latest_setups(
        self,
        symbol: Optional[str] = None,
        status: Optional[CandidateStatus] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Fetch latest setups from the journal."""
        conditions = []
        params: List[Any] = []

        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if status:
            conditions.append("status = ?")
            params.append(status.value)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
        SELECT candidate_id, symbol, timestamp_ms, direction, bias_4h, structure_1h,
               entry_price, stop_loss, take_profit_1, take_profit_2, gross_rr, net_rr,
               invalidation_price, ml_probability, calibration_status, eval_sample_count,
               status, details_json, created_at_utc, entry_category
        FROM setup_candidates
        {where_clause}
        ORDER BY timestamp_ms DESC
        LIMIT {limit};
        """
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()

        results = []
        for r in rows:
            data = dict(r)
            try:
                data["details"] = json.loads(data["details_json"])
            except Exception:
                data["details"] = {}
            if "entry_category" not in data or not data["entry_category"]:
                data["entry_category"] = data["details"].get("entry_category", "Unclassified")
            data["details"]["entry_category"] = data["entry_category"]
            results.append(data)
        return results

    def reconcile_stale_setups(
        self,
        now_ms: Optional[int] = None,
        stale_threshold_hours: float = 3.0,
    ) -> int:
        """Audit and update un-expired setups in the database to STALE if candle age exceeds threshold or TTL expired.

        Preserves all historical setup parameters and details while ensuring the status column
        accurately reflects that the signal is expired/stale rather than misleadingly READY.
        """
        current_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        stale_cutoff_ms = current_ms - int(stale_threshold_hours * 3600.0 * 1000.0)

        query = """
        SELECT candidate_id, details_json, timestamp_ms
        FROM setup_candidates
        WHERE status IN ('READY', 'VALID', 'WATCHLIST');
        """
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query)
            rows = cur.fetchall()

        updated_count = 0
        for r in rows:
            cid = r["candidate_id"]
            try:
                det = json.loads(r["details_json"])
            except Exception:
                det = {}

            try:
                raw_exp = det.get("expires_at_ms")
                expires_at_ms = int(raw_exp) if raw_exp is not None else 0
            except (ValueError, TypeError):
                expires_at_ms = 0

            try:
                ts_ms = int(r["timestamp_ms"]) if r["timestamp_ms"] is not None else 0
            except (ValueError, TypeError):
                ts_ms = 0

            is_expired = bool(expires_at_ms > 0 and current_ms > expires_at_ms)
            is_stale_candle = (ts_ms <= 0) or (ts_ms < stale_cutoff_ms)

            if is_expired or is_stale_candle:
                rejection_reasons = det.get("rejection_reasons", [])
                candle_age_h = (current_ms - ts_ms) / (3600.0 * 1000.0)
                if is_expired and not any("expired" in str(x).lower() for x in rejection_reasons):
                    exp_utc = (
                        datetime.fromtimestamp(expires_at_ms / 1000.0, tz=timezone.utc).isoformat()
                        if expires_at_ms
                        else "N/A"
                    )
                    rejection_reasons.append(f"Setup expired at {exp_utc} (12h TTL exceeded).")
                if is_stale_candle and not any("candle data age" in str(x).lower() for x in rejection_reasons):
                    rejection_reasons.append(
                        f"Candle data age ({candle_age_h:.1f}h) exceeds {stale_threshold_hours:.1f}h staleness threshold."
                    )
                det["status"] = "STALE"
                det["rejection_reasons"] = rejection_reasons
                new_details_json = json.dumps(det)

                update_sql = """
                UPDATE setup_candidates
                SET status = 'STALE', details_json = ?
                WHERE candidate_id = ?;
                """
                with self.db.get_connection() as conn:
                    conn.execute(update_sql, (new_details_json, cid))
                    conn.commit()
                updated_count += 1

        return updated_count

    def save_trade(self, trade: TradeRecord) -> None:
        """Store or update a trade record in the journal."""
        now_utc = datetime.now(timezone.utc).isoformat()
        direction_val = trade.direction.value if hasattr(trade.direction, "value") else str(trade.direction)
        entry_cat_val = (
            trade.entry_category.value
            if hasattr(trade.entry_category, "value")
            else str(getattr(trade, "entry_category", "Unclassified"))
        )
        query = """
        INSERT OR REPLACE INTO trade_journal (
            trade_id, symbol, direction, entry_timestamp_ms, entry_price,
            stop_loss, take_profit, quantity, notional_usdt, exit_timestamp_ms,
            exit_price, pnl_gross, pnl_net, fee_paid, funding_paid, exit_reason,
            is_open, created_at_utc, setup_id, timeframe, order_status,
            leverage, margin_usdt, slippage_paid, fill_model_version,
            data_source, rejection_reason, funding_data_missing, details_json,
            entry_category
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            trade.trade_id,
            trade.symbol,
            direction_val,
            trade.entry_timestamp_ms,
            trade.entry_price,
            trade.stop_loss,
            trade.take_profit,
            trade.quantity,
            trade.notional_usdt,
            trade.exit_timestamp_ms,
            trade.exit_price,
            trade.pnl_gross,
            trade.pnl_net,
            trade.fee_paid,
            trade.funding_paid,
            trade.exit_reason,
            1 if trade.is_open else 0,
            now_utc,
            trade.setup_id or "",
            getattr(trade, "timeframe", "1h"),
            getattr(trade, "order_status", "FILLED"),
            getattr(trade, "leverage", 10.0),
            getattr(trade, "margin_usdt", 5.0),
            getattr(trade, "slippage_paid", 0.0),
            getattr(trade, "fill_model_version", "v1_conservative"),
            getattr(trade, "data_source", "asterdex_live"),
            getattr(trade, "rejection_reason", ""),
            1 if getattr(trade, "funding_data_missing", False) else 0,
            getattr(trade, "details_json", "{}"),
            entry_cat_val,
        )
        with self.db.get_connection() as conn:
            conn.execute(query, params)
            conn.commit()

    def get_trades(
        self,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        entry_category: Optional[str] = None,
        outcome: Optional[str] = None,
        open_only: bool = False,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Retrieve trade history with rich filtering and outcome calculation."""
        conditions = []
        params: List[Any] = []

        if open_only:
            conditions.append("is_open = 1")
        if symbol and symbol.strip():
            conditions.append("UPPER(symbol) LIKE ?")
            params.append(f"%{symbol.strip().upper()}%")
        if direction and direction.upper() != "ALL":
            conditions.append("UPPER(direction) = ?")
            params.append(direction.strip().upper())
        if entry_category and entry_category.upper() != "ALL":
            conditions.append("UPPER(entry_category) = ?")
            params.append(entry_category.strip().upper())
        if start_time_ms is not None:
            conditions.append("entry_timestamp_ms >= ?")
            params.append(start_time_ms)
        if end_time_ms is not None:
            conditions.append("entry_timestamp_ms <= ?")
            params.append(end_time_ms)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
        SELECT *
        FROM trade_journal
        {where_clause}
        ORDER BY entry_timestamp_ms DESC
        LIMIT {limit};
        """
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()

        results = []
        for r in rows:
            d = dict(r)
            is_open = bool(d.get("is_open", 1))
            pnl_net = float(d.get("pnl_net", 0.0))
            if is_open or d.get("exit_timestamp_ms") is None:
                trade_outcome = "OPEN"
            elif pnl_net > 0.0001:
                trade_outcome = "WIN"
            elif pnl_net < -0.0001:
                trade_outcome = "LOSS"
            else:
                trade_outcome = "BREAKEVEN"

            # Filter by outcome if requested
            if outcome and outcome.upper() != "ALL" and trade_outcome != outcome.upper():
                continue

            entry_ts = d.get("entry_timestamp_ms")
            exit_ts = d.get("exit_timestamp_ms")
            duration_sec = max(0.0, (exit_ts - entry_ts) / 1000.0) if (exit_ts and entry_ts) else None
            if duration_sec is not None:
                duration_str = f"{duration_sec / 3600.0:.1f}h" if duration_sec >= 3600.0 else f"{duration_sec / 60.0:.0f}m"
            else:
                duration_str = "Open"

            fee_p = float(d.get("fee_paid", 0.0))
            funding_p = float(d.get("funding_paid", 0.0))
            slip_p = float(d.get("slippage_paid", 0.0))

            d["outcome"] = trade_outcome
            d["duration_seconds"] = duration_sec
            d["duration_display"] = duration_str
            d["total_fees"] = round(fee_p + funding_p + slip_p, 4)
            d["entry_category"] = d.get("entry_category") or "Unclassified"
            d["entry_time_utc"] = (
                datetime.fromtimestamp(entry_ts / 1000.0, tz=timezone.utc).isoformat()
                if entry_ts
                else None
            )
            d["exit_time_utc"] = (
                datetime.fromtimestamp(exit_ts / 1000.0, tz=timezone.utc).isoformat()
                if exit_ts
                else None
            )
            results.append(d)

        return results

    @staticmethod
    def compute_trade_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute performance metrics strictly isolating realized vs open trades."""
        closed_trades = [t for t in trades if not t.get("is_open", False) and t.get("exit_timestamp_ms") is not None]
        closed_count = len(closed_trades)
        wins = [t for t in closed_trades if t.get("outcome") == "WIN"]
        losses = [t for t in closed_trades if t.get("outcome") == "LOSS"]
        breakevens = [t for t in closed_trades if t.get("outcome") == "BREAKEVEN"]

        win_rate_pct = round((len(wins) / closed_count * 100.0), 2) if closed_count > 0 else 0.0
        gross_pnl = round(sum(float(t.get("pnl_gross", 0.0)) for t in closed_trades), 4)
        total_fees = round(
            sum(
                float(
                    t.get(
                        "total_fees",
                        float(t.get("fee_paid", 0.0))
                        + float(t.get("funding_paid", 0.0))
                        + float(t.get("slippage_paid", 0.0)),
                    )
                )
                for t in closed_trades
            ),
            4,
        )
        net_pnl = round(sum(float(t.get("pnl_net", 0.0)) for t in closed_trades), 4)
        avg_net_pnl = round((net_pnl / closed_count), 4) if closed_count > 0 else 0.0
        open_count = sum(1 for t in trades if t.get("is_open", False) or t.get("exit_timestamp_ms") is None)

        return {
            "closed_trades_count": closed_count,
            "wins_count": len(wins),
            "losses_count": len(losses),
            "breakeven_count": len(breakevens),
            "win_rate_pct": win_rate_pct,
            "gross_pnl": gross_pnl,
            "total_fees": total_fees,
            "net_pnl": net_pnl,
            "avg_net_pnl": avg_net_pnl,
            "open_trades_count": open_count,
        }

    def get_open_trades(self) -> List[Dict[str, Any]]:
        """Retrieve all currently active open trades."""
        return self.get_trades(open_only=True)

    def save_paper_order(self, order_dict: Dict[str, Any]) -> None:
        """Store or update a simulated paper order."""
        now_utc = datetime.now(timezone.utc).isoformat()
        direction_val = order_dict["direction"]
        if hasattr(direction_val, "value"):
            direction_val = direction_val.value

        status_val = order_dict["status"]
        if hasattr(status_val, "value"):
            status_val = status_val.value

        entry_cat = order_dict.get("entry_category", "Unclassified")
        if hasattr(entry_cat, "value"):
            entry_cat = entry_cat.value
        entry_cat_val = str(entry_cat) if entry_cat else "Unclassified"

        query = """
        INSERT OR REPLACE INTO paper_orders (
            order_id, candidate_id, symbol, direction, order_type,
            price, stop_price, take_profit, quantity, notional_usdt,
            margin_usdt, leverage, status, created_bar_time_ms,
            evaluated_bars_count, filled_time_ms, fill_price,
            slippage_paid, fee_paid, rejection_reason, created_at_utc, updated_at_utc,
            entry_category
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            order_dict["order_id"],
            order_dict["candidate_id"],
            str(order_dict["symbol"]),
            str(direction_val),
            str(order_dict["order_type"]),
            float(order_dict["price"]),
            float(order_dict["stop_price"]) if order_dict.get("stop_price") is not None else None,
            float(order_dict["take_profit"]) if order_dict.get("take_profit") is not None else None,
            float(order_dict["quantity"]),
            float(order_dict["notional_usdt"]),
            float(order_dict["margin_usdt"]),
            float(order_dict["leverage"]),
            str(status_val),
            int(order_dict["created_bar_time_ms"]),
            int(order_dict.get("evaluated_bars_count", 0)),
            int(order_dict["filled_time_ms"]) if order_dict.get("filled_time_ms") is not None else None,
            float(order_dict["fill_price"]) if order_dict.get("fill_price") is not None else None,
            float(order_dict.get("slippage_paid", 0.0)),
            float(order_dict.get("fee_paid", 0.0)),
            str(order_dict.get("rejection_reason", "")),
            order_dict.get("created_at_utc", now_utc),
            now_utc,
            entry_cat_val,
        )
        with self.db.get_connection() as conn:
            conn.execute(query, params)
            conn.commit()

    def get_paper_orders(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve paper orders with optional symbol and status filters."""
        query = "SELECT * FROM paper_orders WHERE 1=1"
        params: List[Any] = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_bar_time_ms DESC"

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

