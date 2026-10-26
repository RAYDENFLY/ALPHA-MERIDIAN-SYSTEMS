"""Exchange symbol metadata, specifications, and precision filters.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import math
from typing import Any, Dict, Optional
from src.risk.live_micro_sizing import normalize_price_to_tick


@dataclass
class SymbolSpecs:
    """Exchange specifications and filter rules for a perpetual contract."""
    symbol: str
    base_asset: str
    quote_asset: str
    contract_type: str
    status: str
    tick_size: float
    price_precision: int
    step_size: float
    quantity_precision: int
    min_qty: float
    max_qty: float
    min_notional: float
    maint_margin_percent: float

    def round_price(self, price: float, rounding: str = "NEAREST") -> float:
        """Round price to tick size and price precision using specified rounding mode."""
        if price <= 0:
            return 0.0
        if self.tick_size <= 0:
            return round(price, self.price_precision)
        return normalize_price_to_tick(price, self.tick_size, rounding=rounding)

    def round_quantity(self, quantity: float) -> float:
        """Round quantity down to step size and quantity precision to avoid exceeding budget."""
        if self.step_size <= 0:
            return round(quantity, self.quantity_precision)
        cleaned_qty = round(quantity, 8)
        d_qty = Decimal(str(cleaned_qty))
        d_step = Decimal(str(self.step_size))
        rounded = (d_qty / d_step).quantize(Decimal("1"), rounding=ROUND_DOWN) * d_step
        return float(rounded)

    def validate_notional(self, price: float, quantity: float) -> bool:
        """Check whether price * quantity satisfies min_notional."""
        notional = price * quantity
        return notional >= self.min_notional

    @classmethod
    def from_aster_dict(cls, data: Dict[str, Any]) -> "SymbolSpecs":
        """Parse Aster DEX / Binance perpetual exchangeInfo symbol item."""
        symbol = data["symbol"]
        base_asset = data.get("baseAsset", "")
        quote_asset = data.get("quoteAsset", "USDT")
        contract_type = data.get("contractType", "PERPETUAL")
        status = data.get("status", "TRADING")
        price_prec = int(data.get("pricePrecision", 2))
        qty_prec = int(data.get("quantityPrecision", 3))
        maint_margin = float(data.get("maintMarginPercent", 2.5))

        tick_size = 0.01
        step_size = 0.001
        min_qty = 0.001
        max_qty = 1000000.0
        min_notional = 5.0

        for f in data.get("filters", []):
            ft = f.get("filterType")
            if ft == "PRICE_FILTER":
                tick_size = float(f.get("tickSize", tick_size))
            elif ft == "LOT_SIZE":
                step_size = float(f.get("stepSize", step_size))
                min_qty = float(f.get("minQty", min_qty))
                max_qty = float(f.get("maxQty", max_qty))
            elif ft == "MIN_NOTIONAL":
                min_notional = float(f.get("notional", min_notional))

        return cls(
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            contract_type=contract_type,
            status=status,
            tick_size=tick_size,
            price_precision=price_prec,
            step_size=step_size,
            quantity_precision=qty_prec,
            min_qty=min_qty,
            max_qty=max_qty,
            min_notional=min_notional,
            maint_margin_percent=maint_margin,
        )
