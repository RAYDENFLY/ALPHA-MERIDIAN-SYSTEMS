"""Research Storage Repository for Model B Long vs Short Research Expansion.

Manages dedicated SQLite storage for:
- research_shadow_signals: Canonical signals evaluated across all variants
- research_shadow_evaluations: Independent counterfactual decisions, orders, and simulated trades

ISOLATION GUARANTEE:
Never mutates or mixes with trade_journal, paper_orders, or paper_lifecycle_events.
"""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.core.logger import logger
from src.data.database import DatabaseManager
from src.research.types import (
    EvaluationDecision,
    ResearchVariant,
    ShadowOrderStatus,
    ShadowSignalRecord,
    ShadowVariantEvaluation,
    VARIANT_METADATA,
    VariantPerformanceMetrics,
    compute_wilson_ci,
)


class ResearchStorage:
    """Independent SQLite repository for shadow research signals and counterfactual evaluations."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self._init_tables()

    def _init_tables(self) -> None:
        """Create research shadow tables if they do not exist."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS research_shadow_signals (
                signal_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit_1 REAL NOT NULL,
                take_profit_2 REAL,
                gross_reward_to_risk REAL NOT NULL DEFAULT 1.5,
                net_reward_to_risk REAL NOT NULL DEFAULT 1.4,
                active_execution_eligible INTEGER NOT NULL DEFAULT 1,
                active_ineligible_reason TEXT,
                market_context_json TEXT NOT NULL DEFAULT '{}',
                created_at_utc TEXT NOT NULL
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_shadow_sig_sym_time 
            ON research_shadow_signals (symbol, timestamp_ms);
            """,
            """
            CREATE TABLE IF NOT EXISTS research_shadow_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                signal_id TEXT NOT NULL,
                variant TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                decision TEXT NOT NULL,
                rejection_reason TEXT,
                simulated_entry_price REAL,
                simulated_stop_loss REAL,
                simulated_take_profit REAL,
                simulated_take_profit_2 REAL,
                simulated_quantity REAL,
                simulated_notional_usdt REAL,
                simulated_margin_usdt REAL,
                simulated_leverage REAL DEFAULT 10.0,
                order_status TEXT NOT NULL DEFAULT 'CREATED',
                fill_timestamp_ms INTEGER,
                fill_price REAL,
                exit_timestamp_ms INTEGER,
                exit_price REAL,
                exit_reason TEXT,
                holding_time_min REAL DEFAULT 0.0,
                holding_bars INTEGER DEFAULT 0,
                pnl_gross REAL DEFAULT 0.0,
                fee_paid REAL DEFAULT 0.0,
                slippage_paid REAL DEFAULT 0.0,
                funding_paid REAL DEFAULT 0.0,
                pnl_net REAL DEFAULT 0.0,
                mae_pct REAL DEFAULT 0.0,
                mfe_pct REAL DEFAULT 0.0,
                is_open INTEGER DEFAULT 0,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                FOREIGN KEY (signal_id) REFERENCES research_shadow_signals (signal_id)
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_shadow_eval_var_sig 
            ON research_shadow_evaluations (variant, signal_id);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_shadow_eval_var_status 
            ON research_shadow_evaluations (variant, order_status);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_shadow_eval_dir 
            ON research_shadow_evaluations (direction, variant);
            """,
        ]
        with self.db.get_connection() as conn:
            for q in queries:
                conn.execute(q)
            conn.commit()

    def save_signal(self, signal: ShadowSignalRecord) -> None:
        """Persist or update a canonical shadow signal observation."""
        query = """
        INSERT OR REPLACE INTO research_shadow_signals (
            signal_id, candidate_id, symbol, direction, timestamp_ms,
            entry_price, stop_loss, take_profit_1, take_profit_2,
            gross_reward_to_risk, net_reward_to_risk,
            active_execution_eligible, active_ineligible_reason,
            market_context_json, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            signal.signal_id,
            signal.candidate_id,
            signal.symbol,
            signal.direction,
            signal.timestamp_ms,
            signal.entry_price,
            signal.stop_loss,
            signal.take_profit_1,
            signal.take_profit_2,
            signal.gross_reward_to_risk,
            signal.net_reward_to_risk,
            1 if signal.active_execution_eligible else 0,
            signal.active_ineligible_reason,
            json.dumps(signal.market_context),
            signal.created_at_utc,
        )
        with self.db.get_connection() as conn:
            conn.execute(query, params)
            conn.commit()

    def save_evaluation(self, evaluation: ShadowVariantEvaluation) -> None:
        """Persist or update a shadow variant evaluation."""
        self.save_evaluations_batch([evaluation])

    def save_evaluations_batch(self, evaluations: List[ShadowVariantEvaluation]) -> None:
        """Persist a batch of variant evaluations atomically."""
        if not evaluations:
            return

        query = """
        INSERT OR REPLACE INTO research_shadow_evaluations (
            evaluation_id, signal_id, variant, symbol, direction, timestamp_ms,
            decision, rejection_reason, simulated_entry_price, simulated_stop_loss,
            simulated_take_profit, simulated_take_profit_2, simulated_quantity,
            simulated_notional_usdt, simulated_margin_usdt, simulated_leverage,
            order_status, fill_timestamp_ms, fill_price, exit_timestamp_ms,
            exit_price, exit_reason, holding_time_min, holding_bars,
            pnl_gross, fee_paid, slippage_paid, funding_paid, pnl_net,
            mae_pct, mfe_pct, is_open, details_json, created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params_list = []
        for e in evaluations:
            params_list.append((
                e.evaluation_id,
                e.signal_id,
                e.variant.value if hasattr(e.variant, "value") else str(e.variant),
                e.symbol,
                e.direction,
                e.timestamp_ms,
                e.decision.value if hasattr(e.decision, "value") else str(e.decision),
                e.rejection_reason,
                e.simulated_entry_price,
                e.simulated_stop_loss,
                e.simulated_take_profit,
                e.simulated_take_profit_2,
                e.simulated_quantity,
                e.simulated_notional_usdt,
                e.simulated_margin_usdt,
                e.simulated_leverage,
                e.order_status.value if hasattr(e.order_status, "value") else str(e.order_status),
                e.fill_timestamp_ms,
                e.fill_price,
                e.exit_timestamp_ms,
                e.exit_price,
                e.exit_reason,
                e.holding_time_min,
                e.holding_bars,
                e.pnl_gross,
                e.fee_paid,
                e.slippage_paid,
                e.funding_paid,
                e.pnl_net,
                e.mae_pct,
                e.mfe_pct,
                1 if e.is_open else 0,
                json.dumps(e.details),
                e.created_at_utc,
                e.updated_at_utc,
            ))

        with self.db.get_connection() as conn:
            conn.executemany(query, params_list)
            conn.commit()

    def get_signal_by_id(self, signal_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single canonical signal."""
        query = "SELECT * FROM research_shadow_signals WHERE signal_id = ? LIMIT 1;"
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (signal_id,))
            row = cur.fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["market_context"] = json.loads(res.get("market_context_json", "{}"))
        except Exception:
            res["market_context"] = {}
        return res

    def get_all_signals(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Fetch all canonical research signals ordered by timestamp descending."""
        query = "SELECT * FROM research_shadow_signals ORDER BY timestamp_ms DESC LIMIT ?;"
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (limit,))
            rows = cur.fetchall()
        signals = []
        for r in rows:
            d = dict(r)
            try:
                d["market_context"] = json.loads(d.get("market_context_json", "{}"))
            except Exception:
                d["market_context"] = {}
            signals.append(d)
        return signals

    def get_evaluations_by_signal(self, signal_id: str) -> List[Dict[str, Any]]:
        """Fetch all variant evaluations for a common signal ID (paired comparison)."""
        query = "SELECT * FROM research_shadow_evaluations WHERE signal_id = ? ORDER BY variant ASC;"
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (signal_id,))
            rows = cur.fetchall()
        evals = []
        for r in rows:
            d = dict(r)
            try:
                d["details"] = json.loads(d.get("details_json", "{}"))
            except Exception:
                d["details"] = {}
            evals.append(d)
        return evals

    def get_evaluations_for_variant(
        self,
        variant: str,
        direction: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Fetch evaluations for a specific variant with optional filters."""
        conditions = ["variant = ?"]
        params: List[Any] = [variant]

        if direction:
            conditions.append("direction = ?")
            params.append(direction.upper())
        if status:
            conditions.append("order_status = ?")
            params.append(status)

        where = " AND ".join(conditions)
        query = f"SELECT * FROM research_shadow_evaluations WHERE {where} ORDER BY timestamp_ms DESC LIMIT ?;"
        params.append(limit)

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()

        results = []
        for r in rows:
            d = dict(r)
            try:
                d["details"] = json.loads(d.get("details_json", "{}"))
            except Exception:
                d["details"] = {}
            results.append(d)
        return results

    def get_open_shadow_orders(self) -> List[ShadowVariantEvaluation]:
        """Fetch active pending or filled (open) shadow evaluations across all variants."""
        query = """
        SELECT * FROM research_shadow_evaluations 
        WHERE order_status IN ('PENDING', 'FILLED') OR is_open = 1;
        """
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query)
            rows = cur.fetchall()

        open_evals: List[ShadowVariantEvaluation] = []
        for r in rows:
            d = dict(r)
            try:
                details = json.loads(d.get("details_json", "{}"))
            except Exception:
                details = {}
            eval_obj = ShadowVariantEvaluation(
                evaluation_id=d["evaluation_id"],
                signal_id=d["signal_id"],
                variant=ResearchVariant(d["variant"]),
                symbol=d["symbol"],
                direction=d["direction"],
                timestamp_ms=d["timestamp_ms"],
                decision=EvaluationDecision(d["decision"]),
                rejection_reason=d["rejection_reason"],
                simulated_entry_price=d["simulated_entry_price"],
                simulated_stop_loss=d["simulated_stop_loss"],
                simulated_take_profit=d["simulated_take_profit"],
                simulated_take_profit_2=d["simulated_take_profit_2"],
                simulated_quantity=d["simulated_quantity"],
                simulated_notional_usdt=d["simulated_notional_usdt"],
                simulated_margin_usdt=d["simulated_margin_usdt"],
                simulated_leverage=d.get("simulated_leverage", 10.0),
                order_status=ShadowOrderStatus(d["order_status"]),
                fill_timestamp_ms=d["fill_timestamp_ms"],
                fill_price=d["fill_price"],
                exit_timestamp_ms=d["exit_timestamp_ms"],
                exit_price=d["exit_price"],
                exit_reason=d["exit_reason"],
                holding_time_min=d.get("holding_time_min", 0.0),
                holding_bars=d.get("holding_bars", 0),
                pnl_gross=d.get("pnl_gross", 0.0),
                fee_paid=d.get("fee_paid", 0.0),
                slippage_paid=d.get("slippage_paid", 0.0),
                funding_paid=d.get("funding_paid", 0.0),
                pnl_net=d.get("pnl_net", 0.0),
                mae_pct=d.get("mae_pct", 0.0),
                mfe_pct=d.get("mfe_pct", 0.0),
                is_open=bool(d.get("is_open", 0)),
                details=details,
                created_at_utc=d["created_at_utc"],
                updated_at_utc=d["updated_at_utc"],
            )
            open_evals.append(eval_obj)
        return open_evals

    def compute_metrics(
        self,
        variant: str,
        direction: Optional[str] = None,
    ) -> VariantPerformanceMetrics:
        """Compute aggregated statistical metrics for a variant and direction."""
        meta = VARIANT_METADATA.get(variant, {})
        evals = self.get_evaluations_for_variant(variant, direction=direction, limit=10000)
        metrics = VariantPerformanceMetrics(
            variant=variant,
            direction=direction.upper() if direction else "ALL",
            variant_name=meta.get("name", variant),
            variant_description=meta.get("description", ""),
        )

        metrics.total_signals_seen = len(evals)
        accepted = [e for e in evals if e["decision"] == "ACCEPTED"]
        rejected = [e for e in evals if e["decision"] == "REJECTED"]
        metrics.signals_accepted = len(accepted)
        metrics.signals_rejected = len(rejected)

        pending = [e for e in accepted if e["order_status"] == "PENDING"]
        expired = [e for e in accepted if e["order_status"] == "EXPIRED"]
        metrics.pending_orders = len(pending)
        metrics.missed_fills = len(expired)

        filled = [e for e in accepted if e["order_status"] in ("FILLED", "CLOSED")]
        metrics.simulated_fills = len(filled)

        closed = [e for e in filled if e["order_status"] == "CLOSED"]
        metrics.simulated_closed = len(closed)

        if not closed:
            return metrics

        pnls = [c["pnl_net"] for c in closed]
        gross_pnls = [c["pnl_gross"] for c in closed]
        fees = [c["fee_paid"] for c in closed]
        slips = [c["slippage_paid"] for c in closed]
        fundings = [c["funding_paid"] for c in closed]
        maes = [c["mae_pct"] for c in closed if c["mae_pct"] is not None]
        mfes = [c["mfe_pct"] for c in closed if c["mfe_pct"] is not None]
        holdings = [c["holding_time_min"] for c in closed if c["holding_time_min"] is not None]

        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]
        sl_hits = [c for c in closed if c.get("exit_reason") in ("STOP_LOSS", "SL_COLLISION")]

        metrics.winning_trades = len(winners)
        metrics.losing_trades = len(losers)
        metrics.win_rate_pct = round(len(winners) / len(closed) * 100.0, 2)
        metrics.gross_pnl_usdt = round(sum(gross_pnls), 4)
        metrics.net_pnl_usdt = round(sum(pnls), 4)
        metrics.expectancy_usdt = round(sum(pnls) / len(closed), 4)

        gross_wins = sum(g for g in gross_pnls if g > 0)
        gross_losses = abs(sum(g for g in gross_pnls if g < 0))
        metrics.profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (999.0 if gross_wins > 0 else 0.0)

        metrics.avg_winner_usdt = round(sum(winners) / len(winners), 4) if winners else 0.0
        metrics.avg_loser_usdt = round(sum(losers) / len(losers), 4) if losers else 0.0
        metrics.sl_frequency_pct = round(len(sl_hits) / len(closed) * 100.0, 2)

        metrics.avg_holding_time_min = round(float(np.mean(holdings)), 1) if holdings else 0.0
        metrics.avg_mae_pct = round(float(np.mean(maes)), 2) if maes else 0.0
        metrics.avg_mfe_pct = round(float(np.mean(mfes)), 2) if mfes else 0.0

        metrics.total_fee_drag_usdt = round(sum(fees), 4)
        metrics.total_slippage_drag_usdt = round(sum(slips), 4)
        metrics.total_funding_drag_usdt = round(sum(fundings), 4)

        # Running max drawdown
        cum = np.cumsum(pnls)
        running_max = np.maximum.accumulate(np.insert(cum, 0, 0))
        dd = float(np.max(running_max[1:] - cum)) if len(cum) > 0 else 0.0
        metrics.max_drawdown_usdt = round(dd, 4)

        # Statistical uncertainty and sample-size indicators
        n_closed = len(closed)
        k_wins = len(winners)
        if n_closed > 0:
            lower, upper = compute_wilson_ci(k_wins, n_closed)
            metrics.win_rate_ci_lower_pct = lower
            metrics.win_rate_ci_upper_pct = upper
            metrics.sample_size_adequate = (n_closed >= 30)
            if n_closed < 30:
                metrics.sample_size_warning = (
                    f"LOW_SAMPLE_SIZE: N={n_closed} closed trades (< 30). "
                    f"Win rate 95% CI is wide [{lower}% - {upper}%]. Statistical superiority cannot be established."
                )

        return metrics

    def compute_portfolio_metrics(
        self,
        long_variant: str = ResearchVariant.VARIANT_A_LONG_BASELINE.value,
        short_variant: str = ResearchVariant.VARIANT_F_SHORT_COMBINED.value,
    ) -> Dict[str, Any]:
        """Compute synthetic portfolio metrics combining a LONG variant with a SHORT variant."""
        long_evals = [e for e in self.get_evaluations_for_variant(long_variant, direction="LONG") if e["order_status"] == "CLOSED"]
        short_evals = [e for e in self.get_evaluations_for_variant(short_variant, direction="SHORT") if e["order_status"] == "CLOSED"]

        combined = sorted(long_evals + short_evals, key=lambda x: x["timestamp_ms"])
        if not combined:
            return {
                "name": f"{long_variant} + {short_variant}",
                "closed_trades": 0,
                "win_rate_pct": 0.0,
                "net_pnl_usdt": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_usdt": 0.0,
                "long_trades": len(long_evals),
                "short_trades": len(short_evals),
            }

        pnls = [c["pnl_net"] for c in combined]
        gross_pnls = [c["pnl_gross"] for c in combined]
        winners = [p for p in pnls if p > 0]
        gross_wins = sum(g for g in gross_pnls if g > 0)
        gross_losses = abs(sum(g for g in gross_pnls if g < 0))

        cum = np.cumsum(pnls)
        running_max = np.maximum.accumulate(np.insert(cum, 0, 0))
        dd = float(np.max(running_max[1:] - cum)) if len(cum) > 0 else 0.0

        n_comb = len(combined)
        k_comb = len(winners)
        lower_comb, upper_comb = compute_wilson_ci(k_comb, n_comb) if n_comb > 0 else (0.0, 0.0)
        short_count = len(short_evals)
        sample_warning = None
        if short_count < 30:
            sample_warning = (
                f"LOW_SAMPLE_SIZE: Short variant has only {short_count} closed trades (< 30). "
                f"Performance differential reflects candidate suppression, not proven statistical edge."
            )

        return {
            "name": f"{long_variant} + {short_variant}",
            "closed_trades": len(combined),
            "winning_trades": len(winners),
            "win_rate_pct": round(len(winners) / len(combined) * 100.0, 2),
            "win_rate_ci_lower_pct": lower_comb,
            "win_rate_ci_upper_pct": upper_comb,
            "gross_pnl_usdt": round(sum(gross_pnls), 4),
            "net_pnl_usdt": round(sum(pnls), 4),
            "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else (999.0 if gross_wins > 0 else 0.0),
            "expectancy_usdt": round(sum(pnls) / len(combined), 4),
            "max_drawdown_usdt": round(dd, 4),
            "long_trades": len(long_evals),
            "short_trades": len(short_evals),
            "short_trades_adequate": (short_count >= 30),
            "sample_warning": sample_warning,
        }

    def get_paired_signals_summary(
        self,
        limit: int = 50,
        offset: int = 0,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch canonical signals along with their paired variant evaluations."""
        conditions = []
        params: List[Any] = []

        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol.upper())
        if direction:
            conditions.append("direction = ?")
            params.append(direction.upper())

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"SELECT * FROM research_shadow_signals {where} ORDER BY timestamp_ms DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            signal_rows = cur.fetchall()

        results = []
        for s_row in signal_rows:
            s_dict = dict(s_row)
            try:
                s_dict["market_context"] = json.loads(s_dict.get("market_context_json", "{}"))
            except Exception:
                s_dict["market_context"] = {}

            evals = self.get_evaluations_by_signal(s_dict["signal_id"])
            s_dict["evaluations"] = evals
            results.append(s_dict)

        return results
