"""Aster DEX Read-Only Account Service.

Fetches and caches live authenticated account data from Aster DEX Perpetual Futures V3:
- Total Account Equity (totalMarginBalance)
- Wallet Balance (totalWalletBalance)
- Available Balance (availableBalance)
- Used Margin (totalPositionInitialMargin)
- Unrealized PnL (totalUnrealizedProfit)
- Open Positions and Risk Metrics

Enforces strict data integrity:
- Never uses $16.00 or static values as live data fallback.
- Explicitly flags ACCOUNT_DATA_UNAVAILABLE, STALE_ACCOUNT_DATA, or UNAUTHENTICATED.
- Implements thread-safe cached polling with configurable TTL to avoid rate limit exhaustion.
- Strictly read-only; guarded by live_trading_locked.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import threading
import time
from typing import Any, Dict, List, Optional

from src.core.logger import logger
from src.exchange.aster_v3 import AsterDexV3Adapter


class AccountConnectionStatus(str, Enum):
    CONNECTED = "CONNECTED"
    ACCOUNT_DATA_UNAVAILABLE = "ACCOUNT_DATA_UNAVAILABLE"
    STALE_ACCOUNT_DATA = "STALE_ACCOUNT_DATA"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    ERROR = "ERROR"


@dataclass
class AccountSnapshot:
    """Immutable snapshot of Aster DEX account state."""
    status: AccountConnectionStatus
    total_equity_usdt: Optional[float] = None
    wallet_balance_usdt: Optional[float] = None
    available_balance_usdt: Optional[float] = None
    used_margin_usdt: Optional[float] = None
    unrealized_pnl_usdt: Optional[float] = None
    open_positions_count: int = 0
    last_sync_utc: Optional[str] = None
    last_sync_timestamp_ms: Optional[int] = None
    is_stale: bool = False
    error_message: Optional[str] = None
    positions: List[Dict[str, Any]] = field(default_factory=list)
    assets: List[Dict[str, Any]] = field(default_factory=list)
    account_alias: Optional[str] = None
    source: str = "ASTERDEX_V3_REST"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize account snapshot for API endpoints and telemetry."""
        age_sec = None
        if self.last_sync_timestamp_ms:
            now_ms = int(time.time() * 1000)
            age_sec = max(0, int((now_ms - self.last_sync_timestamp_ms) / 1000))

        positions_summary = []
        for p in self.positions:
            if "take_profit_label" in p and "liquidation_label" in p:
                item = dict(p)
                item["last_sync_age_seconds"] = age_sec
                item["last_sync_utc"] = self.last_sync_utc
                positions_summary.append(item)
            else:
                raw_amt = float(p.get("positionAmt", p.get("raw_position_amt", 0.0)))
                if abs(raw_amt) < 1e-9:
                    continue
                pos_amt = abs(raw_amt)
                side = p.get("side", "LONG" if raw_amt > 0 else "SHORT")
                entry_p = float(p.get("entry_price", p.get("entryPrice", 0.0)))
                mark_p = float(p.get("mark_price", p.get("markPrice", entry_p)))
                notional = float(p.get("notional_usdt", p.get("notional", pos_amt * mark_p)))
                lev = int(p.get("leverage", 1))
                m_mode = str(p.get("margin_mode", p.get("marginType", "cross"))).upper()
                liq_p = p.get("liquidation_price", p.get("liquidationPrice"))
                liq_val = float(liq_p) if (liq_p is not None and str(liq_p).strip() != "") else None
                if liq_val is not None and liq_val > 0:
                    liq_lbl = f"${liq_val:,.4f}"
                else:
                    liq_lbl = "Unavailable (Exchange reported 0)" if (liq_val == 0) else "Unavailable"
                tp_p = p.get("take_profit_price")
                tp_val = float(tp_p) if tp_p is not None else None
                tp_lbl = f"${tp_val:,.4f}" if tp_val is not None else "Not configured"
                sl_p = p.get("stop_loss_price")
                sl_val = float(sl_p) if sl_p is not None else None
                sl_lbl = f"${sl_val:,.4f}" if sl_val is not None else "Not configured"
                upnl = float(p.get("unrealized_pnl_usdt", p.get("unRealizedProfit", p.get("unrealizedProfit", 0.0))))
                alloc_m = float(p.get("allocated_margin_usdt", p.get("isolatedMargin", p.get("positionInitialMargin", 0.0))))
                roe = round((upnl / alloc_m) * 100, 2) if alloc_m > 0 else 0.0
                roi = round((upnl / notional) * 100, 2) if notional > 0 else 0.0
                positions_summary.append({
                    "symbol": p.get("symbol"),
                    "side": side,
                    "position_amt": pos_amt,
                    "raw_position_amt": raw_amt,
                    "entry_price": entry_p,
                    "mark_price": mark_p,
                    "notional_usdt": notional,
                    "leverage": lev,
                    "margin_mode": m_mode,
                    "margin_type": m_mode.lower(),
                    "isolated_margin": float(p.get("isolatedMargin", 0.0)),
                    "allocated_margin_usdt": alloc_m,
                    "allocated_margin_label": f"${alloc_m:,.2f}" if alloc_m > 0 else "Cross Margin Pool",
                    "take_profit_price": tp_val,
                    "take_profit_label": tp_lbl,
                    "stop_loss_price": sl_val,
                    "stop_loss_label": sl_lbl,
                    "liquidation_price": liq_val if (liq_val and liq_val > 0) else None,
                    "liquidation_label": liq_lbl,
                    "unrealized_pnl_usdt": upnl,
                    "roe_pct": roe,
                    "roi_pct": roi,
                    "denominator_label": p.get("denominator_label", "Allocated Margin"),
                    "source": self.source,
                    "last_sync_utc": self.last_sync_utc,
                    "last_sync_age_seconds": age_sec,
                })

        return {
            "status": self.status.value,
            "is_connected": self.status == AccountConnectionStatus.CONNECTED,
            "is_stale": self.is_stale,
            "total_equity_usdt": self.total_equity_usdt,
            "wallet_balance_usdt": self.wallet_balance_usdt,
            "available_balance_usdt": self.available_balance_usdt,
            "used_margin_usdt": self.used_margin_usdt,
            "unrealized_pnl_usdt": self.unrealized_pnl_usdt,
            "open_positions_count": self.open_positions_count,
            "last_sync_utc": self.last_sync_utc,
            "last_sync_timestamp_ms": self.last_sync_timestamp_ms,
            "last_sync_age_seconds": age_sec,
            "source": self.source,
            "error_message": self.error_message,
            "account_alias": self.account_alias,
            "positions_summary": positions_summary,
        }


def enrich_positions_with_risk_and_orders(
    raw_positions: List[Dict[str, Any]],
    pos_risk_list: Optional[List[Dict[str, Any]]] = None,
    open_orders_list: Optional[List[Dict[str, Any]]] = None,
    sync_utc: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Enrich open positions with mark prices, leverage, liquidation prices, and TP/SL orders.
    
    Supports both One-Way Mode (positionSide == "BOTH") and Hedge Mode (positionSide in ("LONG", "SHORT")).
    Validates order status, matching active orders strictly and classifying protection status.
    """
    risk_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    risk_by_symbol: Dict[str, Dict[str, Any]] = {}
    for r in (pos_risk_list or []):
        if isinstance(r, dict) and "symbol" in r:
            sym = str(r["symbol"]).upper()
            pos_side = str(r.get("positionSide", "BOTH")).upper()
            risk_by_key[(sym, pos_side)] = r
            risk_by_symbol[sym] = r

    orders_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for o in (open_orders_list or []):
        if isinstance(o, dict) and "symbol" in o:
            sym = str(o["symbol"]).upper()
            orders_by_symbol.setdefault(sym, []).append(o)

    # Gather all positions keyed by (symbol, positionSide)
    pos_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for p in raw_positions:
        if isinstance(p, dict) and "symbol" in p:
            sym = str(p["symbol"]).upper()
            pos_side = str(p.get("positionSide", "BOTH")).upper()
            amt = float(p.get("positionAmt", p.get("raw_position_amt", 0.0)))
            if abs(amt) > 1e-9:
                pos_map[(sym, pos_side)] = p

    for (sym, pos_side), r in risk_by_key.items():
        amt = float(r.get("positionAmt", 0.0))
        if abs(amt) > 1e-9 and (sym, pos_side) not in pos_map:
            pos_map[(sym, pos_side)] = r

    enriched: List[Dict[str, Any]] = []
    for (sym, pos_side), p in pos_map.items():
        raw_amt = float(p.get("positionAmt", p.get("raw_position_amt", 0.0)))
        if abs(raw_amt) < 1e-9:
            continue

        # Determine directional side (LONG vs SHORT)
        if pos_side == "LONG":
            is_long = True
        elif pos_side == "SHORT":
            is_long = False
        else:  # "BOTH" (One-Way Mode)
            is_long = raw_amt > 0
        side = "LONG" if is_long else "SHORT"
        pos_amt = abs(raw_amt)
        entry_p = float(p.get("entryPrice", p.get("entry_price", 0.0)))

        # Find risk metrics matching (sym, pos_side) or fallback to sym
        r = risk_by_key.get((sym, pos_side), risk_by_symbol.get(sym, {}))
        mark_p = float(r.get("markPrice") or p.get("markPrice") or entry_p)
        lev = int(r.get("leverage") or p.get("leverage") or 1)
        margin_type_raw = str(r.get("marginType") or p.get("marginType") or "cross").lower()
        margin_mode = "ISOLATED" if margin_type_raw == "isolated" else "CROSS"

        notional = float(r.get("notional") or p.get("notional") or (pos_amt * mark_p))
        upnl = float(r.get("unRealizedProfit") or p.get("unrealizedProfit") or p.get("unRealizedProfit") or 0.0)

        isolated_m = float(r.get("isolatedMargin") or p.get("isolatedMargin") or 0.0)
        initial_m = float(p.get("positionInitialMargin") or 0.0)

        if margin_mode == "ISOLATED" and isolated_m > 0:
            allocated_margin = isolated_m
            alloc_label = f"${isolated_m:,.2f} (Isolated)"
            denom_label = "Allocated Margin (Isolated)"
        else:
            if initial_m <= 0 and lev > 0 and notional > 0:
                initial_m = notional / lev
            allocated_margin = initial_m
            alloc_label = f"${allocated_margin:,.2f} (Cross Initial)" if allocated_margin > 0 else "Cross Margin Pool"
            denom_label = "Initial Margin (Cross)" if allocated_margin > 0 else "Position Notional"

        roe_pct = round((upnl / allocated_margin) * 100, 2) if allocated_margin > 0 else 0.0
        roi_pct = round((upnl / notional) * 100, 2) if notional > 0 else 0.0

        # Liquidation price
        liq_raw = r.get("liquidationPrice") if "liquidationPrice" in r else p.get("liquidationPrice")
        liquidation_price: Optional[float] = None
        liquidation_label: str = "Unavailable"
        if liq_raw is not None and str(liq_raw).strip() != "":
            try:
                liq_val = float(liq_raw)
                if liq_val > 0:
                    liquidation_price = liq_val
                    liquidation_label = f"${liq_val:,.4f}"
                else:
                    liquidation_label = "Unavailable (Exchange reported 0)"
            except (ValueError, TypeError):
                liquidation_label = "Unavailable"
        else:
            liquidation_label = "Unavailable"

        # TP / SL matching
        closing_side = "SELL" if is_long else "BUY"
        sym_orders = orders_by_symbol.get(sym, [])
        tp_orders: List[Dict[str, Any]] = []
        sl_orders: List[Dict[str, Any]] = []

        for order in sym_orders:
            # 1. Filter out inactive/stale orders (must be active on exchange)
            o_status = str(order.get("status", "NEW")).upper()
            if o_status not in ("NEW", "PARTIALLY_FILLED"):
                continue

            # 2. Check positionSide match for hedge mode
            o_pos_side = str(order.get("positionSide", "BOTH")).upper()
            if pos_side != "BOTH" and o_pos_side != "BOTH":
                if o_pos_side != pos_side:
                    continue

            # 3. Check closing side
            o_side = str(order.get("side", "")).upper()
            if o_side != closing_side:
                continue

            o_type = str(order.get("type", "")).upper()
            is_reduce = bool(order.get("reduceOnly", False)) or bool(order.get("closePosition", False))
            stop_p = float(order.get("stopPrice", 0.0) or 0.0)
            limit_p = float(order.get("price", 0.0) or 0.0)
            orig_qty = float(order.get("origQty", 0.0) or 0.0)

            # Take Profit matching
            if o_type in ("TAKE_PROFIT", "TAKE_PROFIT_MARKET"):
                target = stop_p if stop_p > 0 else limit_p
                if target > 0:
                    tp_orders.append({
                        "order_id": order.get("orderId"),
                        "price": target,
                        "orig_qty": orig_qty,
                        "type": o_type,
                        "reduce_only": is_reduce,
                        "close_position": bool(order.get("closePosition", False)),
                    })
            # Stop Loss matching
            elif o_type in ("STOP", "STOP_MARKET"):
                target = stop_p if stop_p > 0 else limit_p
                if target > 0:
                    sl_orders.append({
                        "order_id": order.get("orderId"),
                        "price": target,
                        "orig_qty": orig_qty,
                        "type": o_type,
                        "reduce_only": is_reduce,
                        "close_position": bool(order.get("closePosition", False)),
                    })
            # Reduce-only limit order fallback
            elif is_reduce and limit_p > 0:
                if is_long:
                    if limit_p > entry_p:
                        tp_orders.append({
                            "order_id": order.get("orderId"),
                            "price": limit_p,
                            "orig_qty": orig_qty,
                            "type": o_type,
                            "reduce_only": is_reduce,
                            "close_position": bool(order.get("closePosition", False)),
                        })
                    elif limit_p < entry_p:
                        sl_orders.append({
                            "order_id": order.get("orderId"),
                            "price": limit_p,
                            "orig_qty": orig_qty,
                            "type": o_type,
                            "reduce_only": is_reduce,
                            "close_position": bool(order.get("closePosition", False)),
                        })
                else:  # SHORT
                    if limit_p < entry_p:
                        tp_orders.append({
                            "order_id": order.get("orderId"),
                            "price": limit_p,
                            "orig_qty": orig_qty,
                            "type": o_type,
                            "reduce_only": is_reduce,
                            "close_position": bool(order.get("closePosition", False)),
                        })
                    elif limit_p > entry_p:
                        sl_orders.append({
                            "order_id": order.get("orderId"),
                            "price": limit_p,
                            "orig_qty": orig_qty,
                            "type": o_type,
                            "reduce_only": is_reduce,
                            "close_position": bool(order.get("closePosition", False)),
                        })

        # Sort TP orders: ascending for LONG (TP1 is nearest), descending for SHORT (TP1 is nearest)
        if is_long:
            tp_orders.sort(key=lambda x: x["price"])
            sl_orders.sort(key=lambda x: x["price"], reverse=True)
        else:
            tp_orders.sort(key=lambda x: x["price"], reverse=True)
            sl_orders.sort(key=lambda x: x["price"])

        tp_price = tp_orders[0]["price"] if tp_orders else None
        if tp_price is not None:
            if len(tp_orders) > 1:
                tp_label = f"${tp_price:,.4f} (+{len(tp_orders)-1} TP)"
            else:
                tp_label = f"${tp_price:,.4f}"
        else:
            tp_label = "Not configured"

        sl_price = sl_orders[0]["price"] if sl_orders else None
        sl_qty_total = sum(o["orig_qty"] for o in sl_orders)
        has_close_position = any(o.get("close_position", False) for o in sl_orders)

        if sl_price is not None:
            if not has_close_position and (0 < sl_qty_total < pos_amt - 1e-6):
                protection_status = "PARTIALLY_PROTECTED"
                is_protected = False
                sl_label = f"${sl_price:,.4f} (Partial {sl_qty_total:g}/{pos_amt:g})"
            else:
                protection_status = "PROTECTED"
                is_protected = True
                sl_label = f"${sl_price:,.4f}"
        else:
            protection_status = "UNPROTECTED"
            is_protected = False
            sl_label = "Not configured"

        enriched.append({
            "symbol": sym,
            "side": side,
            "position_side": pos_side,
            "position_amt": pos_amt,
            "raw_position_amt": raw_amt,
            "entry_price": entry_p,
            "mark_price": mark_p,
            "notional_usdt": round(notional, 4),
            "leverage": lev,
            "margin_mode": margin_mode,
            "margin_type": margin_type_raw,
            "isolated_margin": round(isolated_m, 4),
            "allocated_margin_usdt": round(allocated_margin, 4),
            "allocated_margin_label": alloc_label,
            "take_profit_price": tp_price,
            "take_profit_label": tp_label,
            "stop_loss_price": sl_price,
            "stop_loss_label": sl_label,
            "is_protected": is_protected,
            "protection_status": protection_status,
            "tp_orders": tp_orders,
            "sl_orders": sl_orders,
            "liquidation_price": liquidation_price,
            "liquidation_label": liquidation_label,
            "unrealized_pnl_usdt": round(upnl, 4),
            "roe_pct": roe_pct,
            "roi_pct": roi_pct,
            "denominator_label": denom_label,
            "source": "ASTERDEX_V3_REST",
            "last_sync_utc": sync_utc,
        })

    return enriched


class AsterAccountService:
    """Thread-safe read-only account data service with caching and staleness management."""

    def __init__(
        self,
        adapter: Optional[AsterDexV3Adapter] = None,
        cache_ttl_sec: float = 15.0,
        stale_threshold_sec: float = 120.0,
    ) -> None:
        self.adapter = adapter
        self.cache_ttl_sec = max(0.01, float(cache_ttl_sec))
        self.stale_threshold_sec = max(self.cache_ttl_sec, float(stale_threshold_sec))
        self._lock = threading.Lock()
        self._cached_snapshot: Optional[AccountSnapshot] = None
        self._last_fetch_mono: float = 0.0

    def get_snapshot(self, force_refresh: bool = False) -> AccountSnapshot:
        """Return the current account snapshot, updating cache if expired or forced."""
        with self._lock:
            now_mono = time.monotonic()
            if (
                not force_refresh
                and self._cached_snapshot is not None
                and (now_mono - self._last_fetch_mono) < self.cache_ttl_sec
            ):
                return self._cached_snapshot

            snapshot = self._fetch_live_snapshot()
            self._cached_snapshot = snapshot
            self._last_fetch_mono = now_mono
            return snapshot

    def sync(self) -> AccountSnapshot:
        """Force an immediate live account synchronization."""
        return self.get_snapshot(force_refresh=True)

    def _fetch_live_snapshot(self) -> AccountSnapshot:
        """Perform authenticated account query with validation and error containment."""
        now_dt = datetime.now(timezone.utc)
        now_utc_str = now_dt.isoformat()
        now_ms = int(now_dt.timestamp() * 1000)

        if self.adapter is None:
            return AccountSnapshot(
                status=AccountConnectionStatus.ACCOUNT_DATA_UNAVAILABLE,
                error_message="No exchange adapter configured.",
                last_sync_utc=None,
                last_sync_timestamp_ms=None,
            )

        # Validate signer credentials
        try:
            is_valid, validation_msg = self.adapter.signer.validate_credentials()
            if not is_valid:
                return AccountSnapshot(
                    status=AccountConnectionStatus.UNAUTHENTICATED,
                    error_message=f"Authentication credentials incomplete or invalid: {validation_msg}",
                    last_sync_utc=None,
                    last_sync_timestamp_ms=None,
                )
        except Exception as auth_err:
            return AccountSnapshot(
                status=AccountConnectionStatus.ERROR,
                error_message=f"Credential validation error: {auth_err}",
                last_sync_utc=None,
                last_sync_timestamp_ms=None,
            )

        # Fetch account data via /fapi/v3/account
        try:
            account_data = self.adapter.get_account_info()
            if not account_data or not isinstance(account_data, dict):
                raise ValueError("Empty or malformed account info response from exchange.")

            wallet_bal = float(account_data.get("totalWalletBalance", 0.0))
            unrealized_pnl = float(account_data.get("totalUnrealizedProfit", 0.0))
            margin_bal = float(account_data.get("totalMarginBalance", wallet_bal + unrealized_pnl))
            avail_bal = float(account_data.get("availableBalance", 0.0))
            used_margin = float(account_data.get("totalPositionInitialMargin", 0.0))

            raw_positions = account_data.get("positions", [])
            raw_assets = account_data.get("assets", [])

            active_positions = [
                p for p in raw_positions
                if abs(float(p.get("positionAmt", 0.0))) > 1e-9
            ]

            pos_risk_list: List[Dict[str, Any]] = []
            open_orders_list: List[Dict[str, Any]] = []

            try:
                pos_risk_list = self.adapter.get_positions()
            except Exception as pos_err:
                logger.warning(f"Failed to fetch positionRisk from Aster DEX: {pos_err}")

            try:
                open_orders_list = self.adapter.get_open_orders()
            except Exception as ord_err:
                logger.warning(f"Failed to fetch openOrders from Aster DEX: {ord_err}")

            enriched_positions = enrich_positions_with_risk_and_orders(
                raw_positions=active_positions,
                pos_risk_list=pos_risk_list,
                open_orders_list=open_orders_list,
                sync_utc=now_utc_str,
            )

            alias = str(account_data.get("accountAlias", "")) or None

            snapshot = AccountSnapshot(
                status=AccountConnectionStatus.CONNECTED,
                total_equity_usdt=round(margin_bal, 4),
                wallet_balance_usdt=round(wallet_bal, 4),
                available_balance_usdt=round(avail_bal, 4),
                used_margin_usdt=round(used_margin, 4),
                unrealized_pnl_usdt=round(unrealized_pnl, 4),
                open_positions_count=len(enriched_positions),
                last_sync_utc=now_utc_str,
                last_sync_timestamp_ms=now_ms,
                is_stale=False,
                error_message=None,
                positions=enriched_positions,
                assets=raw_assets,
                account_alias=alias,
            )
            return snapshot

        except Exception as fetch_err:
            err_msg = str(fetch_err)
            logger.warning(f"Failed to fetch Aster DEX account data: {err_msg}")

            # If previous cached snapshot exists, check whether it is salvageable as STALE
            if self._cached_snapshot and self._cached_snapshot.last_sync_timestamp_ms:
                cache_age = (now_ms - self._cached_snapshot.last_sync_timestamp_ms) / 1000.0
                if cache_age <= self.stale_threshold_sec:
                    return AccountSnapshot(
                        status=AccountConnectionStatus.STALE_ACCOUNT_DATA,
                        total_equity_usdt=self._cached_snapshot.total_equity_usdt,
                        wallet_balance_usdt=self._cached_snapshot.wallet_balance_usdt,
                        available_balance_usdt=self._cached_snapshot.available_balance_usdt,
                        used_margin_usdt=self._cached_snapshot.used_margin_usdt,
                        unrealized_pnl_usdt=self._cached_snapshot.unrealized_pnl_usdt,
                        open_positions_count=self._cached_snapshot.open_positions_count,
                        last_sync_utc=self._cached_snapshot.last_sync_utc,
                        last_sync_timestamp_ms=self._cached_snapshot.last_sync_timestamp_ms,
                        is_stale=True,
                        error_message=f"Sync failed ({err_msg}). Displaying cached state from {self._cached_snapshot.last_sync_utc}.",
                        positions=self._cached_snapshot.positions,
                        assets=self._cached_snapshot.assets,
                        account_alias=self._cached_snapshot.account_alias,
                    )

            # Beyond staleness threshold or no cache: return explicit UNAVAILABLE state
            return AccountSnapshot(
                status=AccountConnectionStatus.ACCOUNT_DATA_UNAVAILABLE,
                total_equity_usdt=None,
                wallet_balance_usdt=None,
                available_balance_usdt=None,
                used_margin_usdt=None,
                unrealized_pnl_usdt=None,
                open_positions_count=0,
                last_sync_utc=None,
                last_sync_timestamp_ms=None,
                is_stale=True,
                error_message=f"Aster DEX account API unavailable: {err_msg}",
            )
