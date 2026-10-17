"""Abstract Base Class for Exchange Market Data and Execution Adapters.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union
from src.core.types import Candle
from src.exchange.metadata import SymbolSpecs


class BaseExchangeAdapter(ABC):
    """Abstract interface for perpetual futures exchange adapters."""

    @abstractmethod
    def ping(self) -> bool:
        """Test exchange connectivity."""
        pass

    @abstractmethod
    def get_exchange_info(self) -> Dict[str, SymbolSpecs]:
        """Fetch and cache trading rules and metadata for all available symbols."""
        pass

    @abstractmethod
    def get_symbol_specs(self, symbol: str) -> SymbolSpecs:
        """Retrieve specification filters for a specific symbol."""
        pass

    @abstractmethod
    def get_klines(
        self,
        symbol: str,
        timeframe: str,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 500,
    ) -> List[Candle]:
        """Fetch historical candlesticks from exchange."""
        pass

    @abstractmethod
    def get_funding_rates(
        self,
        symbol: str,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch historical funding rates."""
        pass

    # Execution & Account Management Interface
    def get_server_time(self) -> int:
        """Fetch current exchange server time in milliseconds."""
        raise NotImplementedError("Server time query not implemented.")

    def get_account_balance(self) -> List[Dict[str, Any]]:
        """Fetch perpetual account asset balances (e.g. USDT available, wallet balance)."""
        raise NotImplementedError("Account balance query not implemented.")

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch open perpetual positions and risk metrics."""
        raise NotImplementedError("Position risk query not implemented.")

    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Set position leverage for a perpetual contract."""
        raise NotImplementedError("Leverage configuration not implemented.")

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        """Set margin type (ISOLATED or CROSSED) for a symbol."""
        raise NotImplementedError("Margin type configuration not implemented.")

    def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: Optional[str] = "GTC",
        client_order_id: Optional[str] = None,
        reduce_only: bool = False,
        close_position: bool = False,
    ) -> Dict[str, Any]:
        """Submit a new order to the exchange."""
        raise NotImplementedError("Order creation not implemented.")

    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[Union[int, str]] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel an existing order by exchange orderId or clientOrderId."""
        raise NotImplementedError("Order cancellation not implemented.")

    def cancel_all_open_orders(self, symbol: str) -> Dict[str, Any]:
        """Cancel all open orders for a specific symbol."""
        raise NotImplementedError("Cancel all orders not implemented.")

    def get_order(
        self,
        symbol: str,
        order_id: Optional[Union[int, str]] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query execution status of a specific order."""
        raise NotImplementedError("Order status query not implemented.")

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query currently active open orders."""
        raise NotImplementedError("Open orders query not implemented.")

