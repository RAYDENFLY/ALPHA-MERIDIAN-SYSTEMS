"""Observer events and read-only Discord webhook notification for Paper Trading.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import time
from typing import Any, Callable, Dict, List, Optional
import requests

from src.core.logger import logger


class PaperEventType(str, Enum):
    """Event types emitted during research scanning and paper execution."""
    SETUP_READY = "SETUP_READY"
    SETUP_REJECTED = "SETUP_REJECTED"
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    POSITION_CLOSED = "POSITION_CLOSED"
    TP_HIT = "TP_HIT"
    TP1_PARTIAL_HIT = "TP1_PARTIAL_HIT"
    TRAILING_STOP_UPDATED = "TRAILING_STOP_UPDATED"
    SL_HIT = "SL_HIT"
    RISK_BLOCKED = "RISK_BLOCKED"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    SCANNER_STATUS = "SCANNER_STATUS"


def mask_webhook_url(url: Optional[str]) -> str:
    """Safely redact webhook URL so webhook secret tokens are never exposed in logs or UI."""
    if not url:
        return "[NOT CONFIGURED]"
    cleaned = url.strip()
    if len(cleaned) <= 15:
        return "***"
    parts = cleaned.split("/")
    if len(parts) >= 4:
        return f"{parts[0]}//{parts[2]}/.../***{cleaned[-4:]}"
    return f"...***{cleaned[-4:]}"


def validate_webhook_url(url: Optional[str]) -> bool:
    """Validate format of Discord webhook URL."""
    if not url:
        return False
    cleaned = url.strip()
    return cleaned.startswith("https://discord.com/api/webhooks/") or cleaned.startswith("https://discordapp.com/api/webhooks/")


def format_price(price: Any) -> str:
    """Format numeric price for mobile display."""
    if price is None:
        return "N/A"
    try:
        p = float(price)
        if abs(p) >= 1.0:
            return f"${p:,.2f}"
        else:
            return f"${p:,.4f}"
    except (ValueError, TypeError):
        return str(price)


def format_usdt(amount: Any, sign: bool = False) -> str:
    """Format USDT currency amount."""
    if amount is None:
        return "N/A"
    try:
        a = float(amount)
        if sign:
            return f"{a:+.4f} USDT"
        return f"{a:.4f} USDT"
    except (ValueError, TypeError):
        return str(amount)


def format_pct(pct: Any, sign: bool = False) -> str:
    """Format percentage value."""
    if pct is None:
        return "N/A"
    try:
        p = float(pct)
        if sign:
            return f"{p:+.2%}"
        return f"{p:.2%}"
    except (ValueError, TypeError):
        return str(pct)


@dataclass
class PaperEvent:
    """Represents a notification event in the scanner and paper trading lifecycle."""
    event_type: PaperEventType
    timestamp_ms: int
    symbol: str
    data: Dict[str, Any]
    created_at_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class PaperEventDispatcher:
    """Dispatches paper trading and scanner events to subscribers and optional Discord webhook.

    Safety Guarantee:
    - Strictly read-only observer pattern.
    - Zero execution authority.
    - Webhook failures never block execution, break transactions, or abort scans.
    - Webhook URL is masked in all logs for security.
    - In-memory deduplication cache suppresses redundant spam.
    - Rate limit (HTTP 429) backoff with bounded single-retry.
    """

    def __init__(
        self,
        discord_webhook_url: Optional[str] = None,
        dedup_ttl_seconds: float = 1800.0,
        request_timeout: float = 5.0,
    ) -> None:
        self.webhook_url = discord_webhook_url.strip() if discord_webhook_url else None
        self.dedup_ttl_seconds = dedup_ttl_seconds
        self.request_timeout = request_timeout
        self._subscribers: List[Callable[[PaperEvent], None]] = []
        self._dedup_cache: Dict[str, float] = {}

    def subscribe(self, callback: Callable[[PaperEvent], None]) -> None:
        """Register an event listener."""
        self._subscribers.append(callback)

    def _generate_dedup_key(self, event: PaperEvent) -> str:
        """Generate deduplication key for an event to avoid alert floods."""
        et = event.event_type
        d = event.data
        if et in (PaperEventType.SETUP_READY, PaperEventType.SETUP_REJECTED):
            cand_id = d.get("candidate_id", "")
            return f"{et.value}:{event.symbol}:{cand_id}"
        elif et in (PaperEventType.ORDER_CREATED, PaperEventType.ORDER_EXPIRED, PaperEventType.ORDER_CANCELLED):
            order_id = d.get("order_id", "")
            return f"{et.value}:{order_id}"
        elif et in (PaperEventType.ORDER_FILLED, PaperEventType.TP_HIT, PaperEventType.TP1_PARTIAL_HIT, PaperEventType.SL_HIT, PaperEventType.POSITION_CLOSED):
            trade_id = d.get("trade_id") or d.get("order_id") or ""
            return f"{et.value}:{trade_id}"
        elif et == PaperEventType.TRAILING_STOP_UPDATED:
            trade_id = d.get("trade_id") or ""
            new_sl = d.get("new_stop_loss") or ""
            return f"{et.value}:{trade_id}:{new_sl}"
        elif et == PaperEventType.RISK_BLOCKED:
            rule = d.get("rule", "")
            reason = d.get("reason", "")
            return f"RISK_BLOCKED:{event.symbol}:{rule}:{reason}"
        elif et == PaperEventType.SYSTEM_ERROR:
            comp = d.get("component", "")
            err = d.get("error_message") or d.get("error", "")
            return f"SYSTEM_ERROR:{comp}:{err}"
        elif et == PaperEventType.SCANNER_STATUS:
            state = d.get("scanner_state", "") or d.get("state", "")
            return f"SCANNER_STATUS:{state}"
        return f"{et.value}:{event.symbol}:{event.timestamp_ms}"

    def is_duplicate(self, event: PaperEvent, now_s: Optional[float] = None) -> bool:
        """Check if event is a duplicate within TTL window."""
        if now_s is None:
            now_s = time.time()
        self._cleanup_dedup_cache(now_s)
        key = self._generate_dedup_key(event)
        last_time = self._dedup_cache.get(key)
        if last_time is not None and (now_s - last_time) < self.dedup_ttl_seconds:
            return True
        self._dedup_cache[key] = now_s
        return False

    def _cleanup_dedup_cache(self, now_s: float) -> None:
        """Prune expired deduplication keys if cache exceeds threshold."""
        if len(self._dedup_cache) > 250:
            expired_keys = [
                k for k, t in self._dedup_cache.items()
                if (now_s - t) >= self.dedup_ttl_seconds
            ]
            for k in expired_keys:
                self._dedup_cache.pop(k, None)

    def dispatch(self, event: PaperEvent, force: bool = False) -> None:
        """Dispatch event to local subscribers and external webhook with safety guarantees."""
        # 1. Local subscribers always notified
        for subscriber in self._subscribers:
            try:
                subscriber(event)
            except Exception as e:
                logger.warning(f"Error in paper event subscriber: {e}")

        # 2. Discord Webhook (optional, non-blocking, safe)
        if self.webhook_url:
            if not force and self.is_duplicate(event):
                logger.debug(
                    f"Discord notification suppressed as duplicate: {event.event_type.value} for {event.symbol}"
                )
                return
            self._send_discord_webhook(event)

    def _build_embed(self, event: PaperEvent) -> Dict[str, Any]:
        """Build compact, mobile-optimized Discord embed based on event type."""
        d = event.data
        et = event.event_type
        sym = event.symbol
        event_time_utc = datetime.fromtimestamp(event.timestamp_ms / 1000.0, tz=timezone.utc).isoformat()

        # Defaults
        title = f"[{et.value}] {sym}"
        description = "SignalGenerator Paper Execution Event"
        color = 0x34495E  # Dark slate
        fields: List[Dict[str, Any]] = []
        footer_text = "SignalGenerator — Paper Execution Mode (Aster DEX Data)"

        if et == PaperEventType.SETUP_READY:
            direction = str(d.get("direction", "NEUTRAL")).upper()
            is_long = direction == "LONG"
            color = 0x2ECC71 if is_long else 0xE74C3C  # Green for LONG, Red for SHORT
            dir_icon = "🟢" if is_long else "🔴"
            title = f"{dir_icon} [SETUP READY] {sym} {direction}"

            entry = d.get("entry_price")
            sl = d.get("stop_loss")
            tp = d.get("take_profit")
            rr = d.get("gross_rr", 0.0)
            fee_be = d.get("break_even_price")
            inv_price = d.get("invalidation_price")
            ref_price = d.get("reference_price")
            ref_age_hours = d.get("reference_data_age_hours")
            ref_age_str = f"{int(ref_age_hours * 60)}m ago" if ref_age_hours is not None else "Current"

            description = (
                f"**{direction} {sym}** at `{format_price(entry)}` | "
                f"SL `{format_price(sl)}` | TP `{format_price(tp)}` | R:R `{rr:.2f}x`"
            )

            fields = [
                {"name": "Direction", "value": f"`{direction}`", "inline": True},
                {"name": "Setup Type", "value": f"`{d.get('setup_type', 'SMC/SnR (1H)')}`", "inline": True},
                {"name": "Planned Entry", "value": f"`{format_price(entry)}`", "inline": True},
                {"name": "Est. Fee Break-Even", "value": f"`{format_price(fee_be)}`", "inline": True},
                {"name": "Stop Loss", "value": f"`{format_price(sl)}`", "inline": True},
                {"name": "Take Profit", "value": f"`{format_price(tp)}`", "inline": True},
                {"name": "Target R:R (Gross)", "value": f"`{rr:.2f}R`", "inline": True},
                {"name": "Invalidation Level", "value": f"`{format_price(inv_price)}`", "inline": True},
                {"name": "Ref Price & Age", "value": f"`{format_price(ref_price)} ({ref_age_str})`", "inline": True},
                {"name": "Candle Time (UTC)", "value": f"`{datetime.fromtimestamp(d.get('candle_timestamp_ms', event.timestamp_ms) / 1000.0, tz=timezone.utc).strftime('%H:%M UTC')}`", "inline": True},
            ]

            # SMC/SnR Evidence Block
            evidence = d.get("evidence")
            bias_4h = d.get("bias_4h", "NEUTRAL")
            struct_1h = d.get("structure_1h", "SIDEWAYS")
            evidence_val = f"**4H Bias**: `{bias_4h}` | **1H Structure**: `{struct_1h}`"
            if evidence:
                evidence_val += f"\n{evidence}"
            fields.append({"name": "SMC & SnR Evidence", "value": evidence_val, "inline": False})

            # Informative ML Score Note
            ml_score = d.get("ml_score", "N/A")
            fields.append({
                "name": "ML Score (Informative)",
                "value": f"`ML Score: {ml_score}` *(Informative only — not an execution gate)*",
                "inline": False,
            })

            if "candidate_id" in d:
                fields.append({"name": "Candidate ID", "value": f"`{d['candidate_id']}`", "inline": False})

            footer_text = "SignalGenerator — Research Scanner | Aster DEX Data"

        elif et == PaperEventType.SETUP_REJECTED:
            title = f"🚫 [NO TRADE] {sym} Setup Rejected"
            color = 0xF39C12  # Amber
            reason = d.get("reason", "Setup does not qualify or was invalidated.")
            status_label = d.get("status", "REJECTED")
            description = f"**[NO TRADE]** {sym} candidate not actionable: {reason}"

            fields = [
                {"name": "Symbol", "value": f"`{sym}`", "inline": True},
                {"name": "Direction", "value": f"`{d.get('direction', 'NEUTRAL')}`", "inline": True},
                {"name": "Status", "value": f"`[NO TRADE] {status_label}`", "inline": True},
                {"name": "Reference Price", "value": f"`{format_price(d.get('reference_price'))}`", "inline": True},
                {"name": "Rejection / Invalidation Reason", "value": f"`{reason}`", "inline": False},
            ]
            if "candidate_id" in d:
                fields.append({"name": "Candidate ID", "value": f"`{d['candidate_id']}`", "inline": False})

            footer_text = "SignalGenerator — Paper Engine Monitoring"

        elif et == PaperEventType.ORDER_CREATED:
            title = f"📝 [ORDER PENDING] {sym}"
            color = 0x3498DB  # Blue
            direction = d.get("direction", "UNKNOWN")
            price = d.get("price")
            description = f"Pending LIMIT order for **{sym} {direction}** at `{format_price(price)}`"

            fields = [
                {"name": "Direction", "value": f"`{direction}`", "inline": True},
                {"name": "Order Type", "value": f"`{d.get('order_type', 'LIMIT')}`", "inline": True},
                {"name": "Target Entry", "value": f"`{format_price(price)}`", "inline": True},
                {"name": "Quantity", "value": f"`{d.get('quantity', 0.0)}`", "inline": True},
                {"name": "Notional", "value": f"`{format_usdt(d.get('notional_usdt'))}`", "inline": True},
                {"name": "Leverage", "value": f"`{d.get('leverage', 10.0):.0f}x`", "inline": True},
                {"name": "Stop Loss", "value": f"`{format_price(d.get('stop_loss'))}`", "inline": True},
                {"name": "Take Profit", "value": f"`{format_price(d.get('take_profit'))}`", "inline": True},
                {"name": "Order ID", "value": f"`{d.get('order_id', 'N/A')}`", "inline": False},
            ]
            if "ttl_bars" in d:
                fields.append({"name": "TTL Expiry", "value": f"`{d['ttl_bars']} bars`", "inline": True})

        elif et == PaperEventType.ORDER_FILLED:
            title = f"🟢 [ENTRY FILLED] {sym}"
            color = 0x2ECC71  # Green
            fill_price = d.get("fill_price")
            direction = d.get("direction", "UNKNOWN")
            description = f"Paper order filled for **{sym} {direction}** at `{format_price(fill_price)}`"

            fields = [
                {"name": "Direction", "value": f"`{direction}`", "inline": True},
                {"name": "Fill Price", "value": f"`{format_price(fill_price)}`", "inline": True},
                {"name": "Planned Entry", "value": f"`{format_price(d.get('planned_price'))}`", "inline": True},
                {"name": "Slippage", "value": f"`{format_usdt(d.get('slippage_usdt', 0.0), sign=True)} ({format_pct(d.get('slippage_pct', 0.0), sign=True)})`", "inline": True},
                {"name": "Quantity", "value": f"`{d.get('quantity', 0.0)}`", "inline": True},
                {"name": "Maker Fee", "value": f"`{format_usdt(d.get('fee', 0.0))}`", "inline": True},
                {"name": "Active SL", "value": f"`{format_price(d.get('stop_loss'))}`", "inline": True},
                {"name": "Active TP", "value": f"`{format_price(d.get('take_profit'))}`", "inline": True},
                {"name": "Trade ID", "value": f"`{d.get('trade_id', d.get('order_id', 'N/A'))}`", "inline": False},
            ]

        elif et in (PaperEventType.ORDER_EXPIRED, PaperEventType.ORDER_CANCELLED):
            is_expired = et == PaperEventType.ORDER_EXPIRED
            title = f"⏱️ [ORDER EXPIRED] {sym}" if is_expired else f"❌ [ORDER CANCELLED] {sym}"
            color = 0x95A5A6  # Gray
            reason = d.get("reason", "Order cancelled/expired without fill.")
            action_name = "expired" if is_expired else "cancelled"
            description = f"Paper order for **{sym}** {action_name}: {reason}"

            fields = [
                {"name": "Symbol", "value": f"`{sym}`", "inline": True},
                {"name": "Direction", "value": f"`{d.get('direction', 'N/A')}`", "inline": True},
                {"name": "Planned Entry", "value": f"`{format_price(d.get('planned_price'))}`", "inline": True},
                {"name": "Reason", "value": f"`{reason}`", "inline": False},
                {"name": "Order ID", "value": f"`{d.get('order_id', 'N/A')}`", "inline": False},
            ]

        elif et in (PaperEventType.TP_HIT, PaperEventType.SL_HIT, PaperEventType.POSITION_CLOSED):
            is_tp = et == PaperEventType.TP_HIT
            is_sl = et == PaperEventType.SL_HIT
            pnl_net = d.get("pnl_net", 0.0)
            exit_price = d.get("exit_price")
            direction = d.get("direction", "UNKNOWN")

            if is_tp:
                title = f"🎯 [TAKE PROFIT HIT] {sym}"
                color = 0x2ECC71  # Green
                description = f"**{sym} {direction}** hit Take Profit! Net PnL: `{format_usdt(pnl_net, sign=True)}`"
            elif is_sl:
                title = f"🛑 [STOP LOSS HIT] {sym}"
                color = 0xE74C3C  # Red
                description = f"**{sym} {direction}** hit Stop Loss. Net PnL: `{format_usdt(pnl_net, sign=True)}`"
            else:
                title = f"🏁 [POSITION CLOSED] {sym}"
                color = 0x9B59B6  # Purple
                description = f"**{sym} {direction}** position closed ({d.get('exit_reason', 'EXIT')}). Net PnL: `{format_usdt(pnl_net, sign=True)}`"

            fields = [
                {"name": "Direction", "value": f"`{direction}`", "inline": True},
                {"name": "Exit Price", "value": f"`{format_price(exit_price)}`", "inline": True},
                {"name": "Gross PnL", "value": f"`{format_usdt(d.get('pnl_gross'), sign=True)}`", "inline": True},
                {"name": "Net PnL", "value": f"`{format_usdt(pnl_net, sign=True)}`", "inline": True},
                {"name": "Realized Fees", "value": f"`{format_usdt(d.get('fee_paid'))}`", "inline": True},
                {"name": "Exit Reason", "value": f"`{d.get('exit_reason', 'NORMAL')}`", "inline": True},
            ]
            if "realized_rr" in d:
                fields.append({"name": "Realized R:R", "value": f"`{d['realized_rr']:.2f}R`", "inline": True})
            if "duration" in d:
                fields.append({"name": "Duration", "value": f"`{d['duration']}`", "inline": True})
            if "trade_id" in d:
                fields.append({"name": "Trade ID", "value": f"`{d['trade_id']}`", "inline": False})

        elif et == PaperEventType.TP1_PARTIAL_HIT:
            direction = d.get("direction", "UNKNOWN")
            color = 0x27AE60  # Emerald Green
            title = f"⚡ [TP1 PARTIAL + SL TO BE] {sym}"
            closed_pct = d.get("closed_pct", 0.5)
            be_sl = d.get("new_stop_loss")
            tp2 = d.get("take_profit_2")
            partial_net = d.get("partial_pnl_net", 0.0)
            description = (
                f"**{sym} {direction}** reached TP1 (`{format_price(d.get('tp1_price'))}`)! "
                f"Closed `{closed_pct:.0%}` (`{format_usdt(partial_net, sign=True)}`). "
                f"Stop-Loss moved to Break-Even (`{format_price(be_sl)}`) & Trailing Stop armed toward TP2 (`{format_price(tp2)}`)."
            )
            fields = [
                {"name": "Direction", "value": f"`{direction}`", "inline": True},
                {"name": "TP1 Fill Price", "value": f"`{format_price(d.get('tp1_price'))}`", "inline": True},
                {"name": "Partial Closed Qty", "value": f"`{d.get('closed_quantity', 0.0)}`", "inline": True},
                {"name": "Remaining Runner Qty", "value": f"`{d.get('remaining_quantity', 0.0)}`", "inline": True},
                {"name": "New SL (Break-Even)", "value": f"`{format_price(be_sl)}`", "inline": True},
                {"name": "Target TP2", "value": f"`{format_price(tp2)}`", "inline": True},
                {"name": "Locked Partial PnL", "value": f"`{format_usdt(partial_net, sign=True)}`", "inline": True},
                {"name": "Trailing Distance", "value": f"`{format_price(d.get('trailing_distance'))}`", "inline": True},
            ]
            if "trade_id" in d:
                fields.append({"name": "Trade ID", "value": f"`{d['trade_id']}`", "inline": False})

        elif et == PaperEventType.TRAILING_STOP_UPDATED:
            direction = d.get("direction", "UNKNOWN")
            color = 0x2980B9  # Blue
            title = f"📈 [TRAILING STOP RATCHETED] {sym}"
            old_sl = d.get("old_stop_loss")
            new_sl = d.get("new_stop_loss")
            description = (
                f"**{sym} {direction}** trailing stop ratcheted from `{format_price(old_sl)}` "
                f"to `{format_price(new_sl)}`."
            )
            fields = [
                {"name": "Direction", "value": f"`{direction}`", "inline": True},
                {"name": "Previous SL", "value": f"`{format_price(old_sl)}`", "inline": True},
                {"name": "New Trailing SL", "value": f"`{format_price(new_sl)}`", "inline": True},
                {"name": "Best Price Reached", "value": f"`{format_price(d.get('favorable_extreme'))}`", "inline": True},
                {"name": "Target TP2", "value": f"`{format_price(d.get('take_profit_2'))}`", "inline": True},
            ]
            if "trade_id" in d:
                fields.append({"name": "Trade ID", "value": f"`{d['trade_id']}`", "inline": False})

        elif et == PaperEventType.RISK_BLOCKED:
            title = f"⚠️ [RISK BLOCKED] {sym}"
            color = 0xE67E22  # Warning Orange
            reason = d.get("reason", "Account risk limit reached.")
            description = f"**Action Blocked** by RiskManager for {sym}: {reason}"

            fields = [
                {"name": "Symbol", "value": f"`{sym}`", "inline": True},
                {"name": "Blocked Action", "value": f"`{d.get('action', 'ORDER_ENTRY')}`", "inline": True},
                {"name": "Violating Rule", "value": f"`{d.get('rule', 'Risk Policy')}`", "inline": True},
                {"name": "Details", "value": f"`{d.get('details', reason)}`", "inline": False},
            ]
            footer_text = "SignalGenerator — Risk Management Gate"

        elif et == PaperEventType.SYSTEM_ERROR:
            comp = d.get("component", "SYSTEM")
            title = f"🚨 [SYSTEM ERROR] {comp}"
            color = 0xC0392B  # Crimson
            err_msg = d.get("error_message", "An unexpected error occurred.")
            description = f"System error detected in **{comp}**: {err_msg}"

            fields = [
                {"name": "Component", "value": f"`{comp}`", "inline": True},
                {"name": "Severity", "value": f"`{d.get('severity', 'ERROR')}`", "inline": True},
                {"name": "Timestamp", "value": f"`{d.get('time_utc', event_time_utc)}`", "inline": True},
                {"name": "Error Details", "value": f"```{err_msg[:900]}```", "inline": False},
            ]
            footer_text = "SignalGenerator — System Monitor"

        elif et == PaperEventType.SCANNER_STATUS:
            title = "💓 [HEARTBEAT] SignalGenerator Status Report"
            color = 0x1ABC9C  # Teal
            description = f"Automated health heartbeat at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"

            capital = d.get("capital", {})
            scanner = d.get("scanner", {})
            paper = d.get("paper", {})

            fields = [
                {"name": "Execution Mode", "value": f"`{d.get('mode', 'RESEARCH')}`", "inline": True},
                {"name": "Scanner State", "value": f"`{scanner.get('state', d.get('scanner_state', 'IDLE'))}`", "inline": True},
                {"name": "Equity", "value": f"`${capital.get('current_equity_usdt', d.get('equity', 0.0)):.2f} USDT`", "inline": True},
                {"name": "Open Positions", "value": f"`{paper.get('open_positions', d.get('open_positions', 0))}`", "inline": True},
                {"name": "Pending Orders", "value": f"`{paper.get('pending_orders', d.get('pending_orders', 0))}`", "inline": True},
                {"name": "Candle Age", "value": f"`{scanner.get('candle_age_seconds', d.get('candle_age_seconds', 'N/A'))}s`", "inline": True},
            ]
            footer_text = "SignalGenerator — Paper Engine Monitoring"

        else:
            # Fallback generic embed
            fields = [{"name": str(k).replace("_", " ").title(), "value": f"`{v}`", "inline": True} for k, v in list(d.items())[:10]]

        return {
            "title": title,
            "description": description,
            "color": color,
            "fields": fields,
            "timestamp": event_time_utc,
            "footer": {"text": footer_text},
        }

    def _send_discord_webhook(self, event: PaperEvent) -> bool:
        """Deliver mobile-optimized event embed to Discord webhook without exposing webhook credentials."""
        if not self.webhook_url:
            return False

        if not validate_webhook_url(self.webhook_url):
            logger.warning("Discord webhook URL format is invalid. Skipping dispatch.")
            return False

        embed = self._build_embed(event)
        payload = {
            "username": "SignalGenerator Bot",
            "embeds": [embed],
        }

        masked_url = mask_webhook_url(self.webhook_url)

        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=self.request_timeout)
            if resp.status_code in (200, 204):
                return True
            elif resp.status_code == 429:
                # HTTP 429 Rate Limited: inspect Retry-After header
                retry_after_hdr = resp.headers.get("Retry-After", "1.0")
                try:
                    retry_after = float(retry_after_hdr)
                except (ValueError, TypeError):
                    retry_after = 1.0

                if retry_after <= 2.0:
                    logger.warning(
                        f"Discord rate limit (HTTP 429) hit. Backing off for {retry_after:.2f}s before retry once."
                    )
                    time.sleep(retry_after)
                    retry_resp = requests.post(self.webhook_url, json=payload, timeout=self.request_timeout)
                    if retry_resp.status_code in (200, 204):
                        return True
                    else:
                        logger.warning(
                            f"Discord webhook retry failed with status {retry_resp.status_code} for {masked_url}."
                        )
                        return False
                else:
                    logger.warning(
                        f"Discord rate limit exceeded (Retry-After: {retry_after:.2f}s). Dropping notification safely to avoid blocking."
                    )
                    return False
            else:
                logger.warning(
                    f"Discord webhook returned status {resp.status_code} for event {event.event_type.value} ({masked_url})."
                )
                return False
        except requests.Timeout:
            logger.warning(f"Discord webhook timeout ({self.request_timeout}s) for {masked_url}.")
            return False
        except requests.RequestException as req_err:
            err_msg = str(req_err)
            if self.webhook_url in err_msg:
                err_msg = err_msg.replace(self.webhook_url, masked_url)
            logger.warning(f"Discord webhook network error: {err_msg} ({masked_url}).")
            return False
        except Exception as err:
            err_msg = str(err)
            if self.webhook_url in err_msg:
                err_msg = err_msg.replace(self.webhook_url, masked_url)
            logger.warning(f"Discord webhook unexpected error: {err_msg} ({masked_url}).")
            return False

    def emit_setup_ready(self, candidate: Any, ref_price: Optional[float] = None) -> None:
        """Helper to emit SETUP_READY event with all candidate metrics and SMC/SnR evidence."""
        now_ms = int(time.time() * 1000)

        # Calculate fee break-even if not pre-populated
        fee_be = getattr(candidate, "break_even_price", None)
        entry_price = getattr(candidate, "entry_price", 0.0)
        direction_val = candidate.direction.value if hasattr(candidate.direction, "value") else str(candidate.direction)
        if fee_be is None and entry_price > 0:
            f = getattr(candidate, "estimated_fee_rate", 0.0005)
            if direction_val == "LONG":
                fee_be = entry_price * (1.0 + f) / (1.0 - f)
            else:
                fee_be = entry_price * (1.0 - f) / (1.0 + f)

        gross_rr = getattr(candidate, "calculated_gross_rr", None) or getattr(candidate, "gross_reward_to_risk", 0.0)
        ref_p = ref_price or getattr(candidate, "reference_price", None) or getattr(candidate, "mark_price", None) or entry_price

        # SMC/SnR evidence text
        evidence_lines = []
        smc_ev = getattr(candidate, "smc_evidence", [])
        snr_ev = getattr(candidate, "snr_evidence", [])
        if smc_ev:
            evidence_lines.extend(smc_ev[:3])
        if snr_ev:
            evidence_lines.extend(snr_ev[:2])
        evidence_str = "\n".join(f"• {line}" for line in evidence_lines) if evidence_lines else "Standard SMC/SnR confluence"

        ml_prob = getattr(candidate, "ml_probability", None)
        ml_score_str = f"{ml_prob:.1%}" if ml_prob is not None else "N/A"

        data = {
            "candidate_id": getattr(candidate, "candidate_id", f"SETUP_{candidate.symbol}_{now_ms}"),
            "direction": direction_val,
            "setup_type": "SMC/SnR (1H)",
            "entry_price": entry_price,
            "stop_loss": getattr(candidate, "stop_loss", 0.0),
            "take_profit": getattr(candidate, "take_profit_1", 0.0),
            "gross_rr": gross_rr,
            "break_even_price": fee_be,
            "invalidation_price": getattr(candidate, "invalidation_price", 0.0),
            "reference_price": ref_p,
            "reference_price_time_utc": getattr(candidate, "reference_price_time_utc", None),
            "reference_data_age_hours": getattr(candidate, "reference_data_age_hours", None),
            "bias_4h": candidate.bias_4h.value if hasattr(candidate.bias_4h, "value") else str(candidate.bias_4h),
            "structure_1h": candidate.structure_1h.value if hasattr(candidate.structure_1h, "value") else str(candidate.structure_1h),
            "evidence": evidence_str,
            "ml_score": ml_score_str,
            "candle_timestamp_ms": getattr(candidate, "timestamp_ms", now_ms),
        }

        self.dispatch(
            PaperEvent(
                event_type=PaperEventType.SETUP_READY,
                timestamp_ms=now_ms,
                symbol=candidate.symbol,
                data=data,
            )
        )

    def emit_setup_rejected(
        self,
        symbol: str,
        reason: str,
        candidate_id: Optional[str] = None,
        direction: Optional[str] = None,
        status: Optional[str] = None,
        ref_price: Optional[float] = None,
    ) -> None:
        """Helper to emit SETUP_REJECTED event with [NO TRADE] styling."""
        now_ms = int(time.time() * 1000)
        data = {
            "candidate_id": candidate_id or f"REJ_{symbol}_{now_ms}",
            "direction": direction or "NEUTRAL",
            "reason": reason,
            "status": status or "REJECTED",
            "reference_price": ref_price,
        }
        self.dispatch(
            PaperEvent(
                event_type=PaperEventType.SETUP_REJECTED,
                timestamp_ms=now_ms,
                symbol=symbol,
                data=data,
            )
        )

    def emit_risk_blocked(
        self,
        symbol: str,
        action: str,
        rule: str,
        reason: str,
        details: Optional[str] = None,
    ) -> None:
        """Helper to emit RISK_BLOCKED event."""
        now_ms = int(time.time() * 1000)
        data = {
            "action": action,
            "rule": rule,
            "reason": reason,
            "details": details or reason,
        }
        self.dispatch(
            PaperEvent(
                event_type=PaperEventType.RISK_BLOCKED,
                timestamp_ms=now_ms,
                symbol=symbol,
                data=data,
            )
        )

    def emit_system_error(
        self,
        component: str,
        error_message: str,
        severity: str = "ERROR",
        symbol: str = "SYSTEM",
    ) -> None:
        """Helper to emit SYSTEM_ERROR event safely."""
        now_ms = int(time.time() * 1000)
        sanitized_msg = str(error_message)
        if self.webhook_url and self.webhook_url in sanitized_msg:
            sanitized_msg = sanitized_msg.replace(self.webhook_url, mask_webhook_url(self.webhook_url))

        data = {
            "component": component,
            "error_message": sanitized_msg,
            "severity": severity,
            "time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
        self.dispatch(
            PaperEvent(
                event_type=PaperEventType.SYSTEM_ERROR,
                timestamp_ms=now_ms,
                symbol=symbol,
                data=data,
            )
        )

    def emit_scanner_status(self, status: Dict[str, Any]) -> None:
        """Helper to emit SCANNER_STATUS event."""
        now_ms = int(time.time() * 1000)
        self.dispatch(
            PaperEvent(
                event_type=PaperEventType.SCANNER_STATUS,
                timestamp_ms=now_ms,
                symbol="SYSTEM",
                data=status,
            )
        )

    def send_heartbeat(self, status: Dict[str, Any]) -> None:
        """Legacy helper for heartbeat dispatch."""
        self.emit_scanner_status(status)

    def send_diagnostic_ping(self) -> bool:
        """Send a harmless diagnostic test notification to verify Discord webhook delivery."""
        if not self.webhook_url:
            logger.info("Cannot send diagnostic ping: Discord webhook is not configured.")
            return False

        if not validate_webhook_url(self.webhook_url):
            logger.warning("Discord webhook URL format is invalid. Cannot send diagnostic ping.")
            return False

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        payload = {
            "username": "SignalGenerator Bot",
            "embeds": [
                {
                    "title": "🔍 [DIAGNOSTIC TEST] Discord Monitoring Active",
                    "description": "SignalGenerator Discord monitoring operational verification.",
                    "color": 0x3498DB,  # Blue
                    "fields": [
                        {"name": "Status", "value": "`OPERATIONAL`", "inline": True},
                        {"name": "Execution Mode", "value": "`PAPER / RESEARCH`", "inline": True},
                        {"name": "Live Trading Lock", "value": "`LOCKED (True)`", "inline": True},
                        {"name": "Timestamp (UTC)", "value": f"`{now_utc}`", "inline": True},
                        {"name": "Safety Invariant", "value": "`Read-Only Notification Channel`", "inline": True},
                        {"name": "Data Source", "value": "`Aster DEX Data Feed`", "inline": True},
                    ],
                    "footer": {"text": "SignalGenerator — Operational Verification Ping"},
                }
            ],
        }

        masked_url = mask_webhook_url(self.webhook_url)
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=self.request_timeout)
            if resp.status_code in (200, 204):
                logger.info(f"Diagnostic ping successfully delivered to Discord ({masked_url}).")
                return True
            logger.warning(f"Diagnostic ping failed with status {resp.status_code} ({masked_url}).")
            return False
        except Exception as err:
            err_msg = str(err)
            if self.webhook_url and self.webhook_url in err_msg:
                err_msg = err_msg.replace(self.webhook_url, masked_url)
            logger.warning(f"Diagnostic ping failed: {err_msg} ({masked_url}).")
            return False
