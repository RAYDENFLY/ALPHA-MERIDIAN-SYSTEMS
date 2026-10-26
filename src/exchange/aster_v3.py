"""Aster DEX Perpetual Futures REST Adapter — Futures API V3 (Pro API).

Connects to https://fapi.asterdex.com using verified Futures V3 endpoints.
Implements EIP-712 structured data signing with AsterV3Signer.
Guarded by live_trading_locked safety constraints.
"""

import os
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Union
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from src.core.exceptions import (
    ExchangeAPIError,
    ExchangeRateLimitError,
    SymbolNotFoundError,
)
from src.core.logger import logger
from src.core.types import Candle
from src.exchange.base import BaseExchangeAdapter
from src.exchange.metadata import SymbolSpecs
from src.exchange.signer_v3 import AsterV3Signer, redact_address


class AsterDexV3Adapter(BaseExchangeAdapter):
    """Adapter for Aster DEX Perpetual Futures API V3 (Pro API)."""

    def __init__(
        self,
        base_url: str = "https://fapi.asterdex.com",
        user_address: Optional[str] = None,
        signer_address: Optional[str] = None,
        signer_private_key: Optional[str] = None,
        chain_id: Optional[int] = None,
        is_testnet: bool = False,
        request_timeout: int = 15,
        max_retries: int = 4,
        retry_delay_sec: float = 1.0,
        live_trading_locked: bool = True,
    ) -> None:
        self.is_testnet = is_testnet
        if is_testnet:
            if base_url == "https://fapi.asterdex.com":
                base_url = "https://fapi.asterdex-testnet.com"
            chain_id = chain_id or int(os.getenv("ASTER_CHAIN_ID", 714))
        else:
            chain_id = chain_id or int(os.getenv("ASTER_CHAIN_ID", 1666))

        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.retry_delay_sec = retry_delay_sec
        self.live_trading_locked = live_trading_locked
        self._specs_cache: Dict[str, SymbolSpecs] = {}

        self.signer = AsterV3Signer(
            user_address=user_address,
            signer_address=signer_address,
            signer_private_key=signer_private_key,
            chain_id=chain_id,
            is_testnet=is_testnet,
        )

        # Profile safety guard: prevent accidental cross-environment execution
        if self.is_testnet and "testnet" not in self.base_url.lower():
            raise ExchangeAPIError(
                f"Configuration safety violation: Testnet profile cannot connect to mainnet URL '{self.base_url}'."
            )
        if self.is_testnet and self.signer.chain_id == 1666:
            raise ExchangeAPIError(
                "Configuration safety violation: Testnet profile cannot use mainnet Chain ID 1666 (expected 714)."
            )

        self.session = requests.Session()
        retries = Retry(
            total=max_retries,
            backoff_factor=retry_delay_sec,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{endpoint}"
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.request_timeout,
                    headers={"User-Agent": "SMC-SNR-Scanner-V3/1.0"},
                )
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", self.retry_delay_sec * attempt))
                    logger.warning(f"Aster DEX V3 rate limit (429). Backing off for {retry_after}s.")
                    time.sleep(retry_after)
                    continue
                elif response.status_code in (500, 502, 503, 504):
                    logger.warning(
                        f"Aster DEX V3 server error ({response.status_code}) on {endpoint}. Attempt {attempt}/{self.max_retries}."
                    )
                    time.sleep(self.retry_delay_sec * attempt)
                    continue
                else:
                    error_msg = f"HTTP {response.status_code} on {endpoint}: {response.text}"
                    logger.error(error_msg)
                    raise ExchangeAPIError(error_msg)
            except requests.RequestException as e:
                logger.warning(f"Network error on {endpoint} (attempt {attempt}/{self.max_retries}): {e}")
                if attempt == self.max_retries:
                    raise ExchangeAPIError(f"Failed to reach Aster DEX V3 after {self.max_retries} attempts: {e}") from e
                time.sleep(self.retry_delay_sec * attempt)

        raise ExchangeAPIError(f"Aster DEX V3 request failed for {endpoint}")

    def _signed_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        include_user: bool = True,
    ) -> Any:
        """Execute authenticated request with EIP-712 signature in URL or Body."""
        is_valid, validation_msg = self.signer.validate_credentials()
        if not is_valid:
            raise ExchangeAPIError(f"V3 authentication validation failed: {validation_msg}")

        signed_params, signature_hex = self.signer.sign_request_params(params=params, include_user=include_user)

        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "SMC-SNR-Scanner-V3/1.0",
        }

        query_string = urllib.parse.urlencode(signed_params)

        for attempt in range(1, self.max_retries + 1):
            try:
                if method.upper() == "GET":
                    response = self.session.get(f"{url}?{query_string}", headers=headers, timeout=self.request_timeout)
                elif method.upper() == "POST":
                    response = self.session.post(url, data=signed_params, headers=headers, timeout=self.request_timeout)
                elif method.upper() == "DELETE":
                    response = self.session.delete(url, data=signed_params, headers=headers, timeout=self.request_timeout)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if response.status_code == 200:
                    return response.json()
                elif response.status_code in (400, 401, 403):
                    res_json = {}
                    try:
                        res_json = response.json()
                    except Exception:
                        pass
                    msg = res_json.get("msg", response.text)
                    code = res_json.get("code", response.status_code)
                    error_msg = f"HTTP {response.status_code} on {endpoint}: {msg} (code={code})"
                    logger.error(error_msg)
                    raise ExchangeAPIError(error_msg)
                elif response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", self.retry_delay_sec * attempt))
                    time.sleep(retry_after)
                    continue
                elif response.status_code in (500, 502, 503, 504):
                    time.sleep(self.retry_delay_sec * attempt)
                    continue
                else:
                    raise ExchangeAPIError(f"HTTP {response.status_code} on {endpoint}: {response.text}")
            except requests.RequestException as e:
                if attempt == self.max_retries:
                    raise ExchangeAPIError(f"Network error on {endpoint}: {e}") from e
                time.sleep(self.retry_delay_sec * attempt)

        raise ExchangeAPIError(f"Signed request failed for {endpoint}")

    # Public Market Data
    def ping(self) -> bool:
        """Test exchange connectivity using V3 ping."""
        try:
            res = self._request("/fapi/v3/ping")
            return isinstance(res, dict)
        except Exception as e:
            logger.error(f"V3 ping failed: {e}")
            return False

    def get_server_time(self) -> int:
        """Fetch current exchange server time in milliseconds."""
        data = self._request("/fapi/v3/time")
        if isinstance(data, dict) and "serverTime" in data:
            return int(data["serverTime"])
        raise ExchangeAPIError(f"Unexpected server time response: {data}")

    def get_exchange_info(self) -> Dict[str, SymbolSpecs]:
        """Fetch and cache all perpetual contract specifications via V3."""
        data = self._request("/fapi/v3/exchangeInfo")
        symbols_raw = data.get("symbols", [])
        self._specs_cache.clear()
        for item in symbols_raw:
            specs = SymbolSpecs.from_aster_dict(item)
            self._specs_cache[specs.symbol] = specs
        logger.info(f"Loaded {len(self._specs_cache)} symbol specifications from Aster DEX V3.")
        return self._specs_cache

    def get_symbol_specs(self, symbol: str) -> SymbolSpecs:
        """Retrieve specifications for a given symbol."""
        if not self._specs_cache:
            self.get_exchange_info()
        clean_symbol = symbol.upper().replace(".P", "")
        if clean_symbol not in self._specs_cache:
            raise SymbolNotFoundError(f"Symbol '{clean_symbol}' not found on Aster DEX V3 perpetual market.")
        return self._specs_cache[clean_symbol]

    def get_klines(
        self,
        symbol: str,
        timeframe: str,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 1000,
    ) -> List[Candle]:
        """Fetch candlestick bars for a symbol and timeframe via V3."""
        clean_symbol = symbol.upper().replace(".P", "")
        params: Dict[str, Any] = {
            "symbol": clean_symbol,
            "interval": timeframe,
            "limit": min(limit, 1000),
        }
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms

        raw_klines = self._request("/fapi/v3/klines", params=params)
        if not isinstance(raw_klines, list):
            raise ExchangeAPIError(f"Invalid klines response type: {type(raw_klines)}")

        candles: List[Candle] = []
        for row in raw_klines:
            try:
                candles.append(Candle.from_raw_kline(row, clean_symbol, timeframe))
            except (ValueError, IndexError) as err:
                logger.warning(f"Corrupted kline row skipped: {row} ({err})")
        return candles

    def get_funding_rates(
        self,
        symbol: str,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch historical funding rates via V3."""
        clean_symbol = symbol.upper().replace(".P", "")
        params: Dict[str, Any] = {
            "symbol": clean_symbol,
            "limit": min(limit, 1000),
        }
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms

        data = self._request("/fapi/v3/fundingRate", params=params)
        return data if isinstance(data, list) else []

    # Authenticated Read-Only Methods
    def noop(self) -> Dict[str, Any]:
        """Call V3 noop endpoint to test signed request connectivity without mutating state."""
        return self._signed_request("POST", "/fapi/v3/noop", include_user=True)

    def get_account_info(self) -> Dict[str, Any]:
        """Fetch comprehensive perpetual account information via V3 (GET /fapi/v3/account)."""
        data = self._signed_request("GET", "/fapi/v3/account", include_user=True)
        return data if isinstance(data, dict) else {}

    def get_account_balance(self) -> List[Dict[str, Any]]:
        """Fetch perpetual account asset balances via V3."""
        data = self._signed_request("GET", "/fapi/v3/balance", include_user=True)
        return data if isinstance(data, list) else []

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch open perpetual positions and risk metrics via V3."""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper().replace(".P", "")
        data = self._signed_request("GET", "/fapi/v3/positionRisk", params=params, include_user=True)
        return data if isinstance(data, list) else []

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query currently active open orders via V3."""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper().replace(".P", "")
        data = self._signed_request("GET", "/fapi/v3/openOrders", params=params, include_user=True)
        return data if isinstance(data, list) else []

    def get_order(
        self,
        symbol: str,
        order_id: Optional[Union[str, int]] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query specific order status via V3 (GET /fapi/v3/order)."""
        params: Dict[str, Any] = {"symbol": symbol.upper().replace(".P", "")}
        if order_id:
            params["orderId"] = int(order_id)
        if client_order_id:
            params["origClientOrderId"] = str(client_order_id)
        return self._signed_request("GET", "/fapi/v3/order", params=params, include_user=True)

    def get_open_order(
        self,
        symbol: str,
        order_id: Optional[Union[str, int]] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query specific open order via V3 (GET /fapi/v3/openOrder)."""
        params: Dict[str, Any] = {"symbol": symbol.upper().replace(".P", "")}
        if order_id:
            params["orderId"] = int(order_id)
        if client_order_id:
            params["origClientOrderId"] = str(client_order_id)
        return self._signed_request("GET", "/fapi/v3/openOrder", params=params, include_user=True)

    def get_position_mode(self) -> Dict[str, Any]:
        """Query position mode via V3 (GET /fapi/v3/positionSide/dual)."""
        return self._signed_request("GET", "/fapi/v3/positionSide/dual", include_user=True)

    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Set position leverage via V3 (POST /fapi/v3/leverage)."""
        clean_symbol = symbol.upper().replace(".P", "")
        return self._signed_request(
            "POST",
            "/fapi/v3/leverage",
            params={"symbol": clean_symbol, "leverage": int(leverage)},
            include_user=True,
        )

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        """Set position margin type (ISOLATED or CROSSED) via V3 (POST /fapi/v3/marginType)."""
        clean_symbol = symbol.upper().replace(".P", "")
        try:
            return self._signed_request(
                "POST",
                "/fapi/v3/marginType",
                params={"symbol": clean_symbol, "marginType": margin_type.upper()},
                include_user=True,
            )
        except ExchangeAPIError as err:
            if "-4046" in str(err) or "No need to change margin type" in str(err):
                return {"code": 200, "msg": "Margin type already configured", "marginType": margin_type.upper()}
            raise

    # Execution Guards & Order Placement
    def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: Optional[str] = "GTC",
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit new order to Aster DEX Futures V3 (Testnet or Mainnet when unlocked)."""
        if self.live_trading_locked:
            raise ExchangeAPIError(
                "Live trading is locked (live_trading_locked=True). "
                "Real-money orders are forbidden until unlocked."
            )

        if self.is_testnet:
            if "testnet" not in self.base_url.lower():
                raise ExchangeAPIError(f"Safety violation: Testnet order routing to non-testnet URL '{self.base_url}' is blocked.")
            if self.signer.chain_id != 714:
                raise ExchangeAPIError(f"Safety violation: Testnet order requires Chain ID 714, found {self.signer.chain_id}.")
            mainnet_user = os.getenv("ASTER_USER_ADDRESS", "").strip()
            mainnet_signer = os.getenv("ASTER_SIGNER_ADDRESS", "").strip()
            if (mainnet_user and self.signer.user_address.lower() == mainnet_user.lower()) or (
                mainnet_signer and self.signer.signer_address.lower() == mainnet_signer.lower()
            ):
                raise ExchangeAPIError(
                    "Safety violation: Mainnet credentials detected on testnet profile! "
                    "To protect mainnet balance, configure dedicated ASTER_TESTNET_* credentials."
                )

        clean_symbol = symbol.upper().replace(".P", "")
        params: Dict[str, Any] = {
            "symbol": clean_symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(quantity),
        }
        if price is not None:
            params["price"] = str(price)
        if stop_price is not None:
            params["stopPrice"] = str(stop_price)
        if time_in_force and order_type.upper() == "LIMIT":
            params["timeInForce"] = time_in_force
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            params["newClientOrderId"] = client_order_id

        venue_label = "TESTNET" if self.is_testnet else "MAINNET LIVE"
        logger.info(f"[{venue_label}] Submitting {order_type} {side} order for {quantity} {clean_symbol} on Aster DEX V3...")
        return self._signed_request("POST", "/fapi/v3/order", params=params, include_user=True)

    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[Union[str, int]] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel an open order on Aster Futures."""
        if self.live_trading_locked:
            raise ExchangeAPIError("Live trading is locked (live_trading_locked=True).")
        if self.is_testnet and ("testnet" not in self.base_url.lower() or self.signer.chain_id != 714):
            raise ExchangeAPIError("Safety violation: Testnet order cancellation requires testnet environment.")

        params: Dict[str, Any] = {"symbol": symbol.upper().replace(".P", "")}
        if order_id:
            params["orderId"] = int(order_id)
        if client_order_id:
            params["origClientOrderId"] = str(client_order_id)

        logger.info(f"Cancelling order {order_id or client_order_id} on {symbol}...")
        return self._signed_request("DELETE", "/fapi/v3/order", params=params, include_user=True)

    def cancel_all_open_orders(self, symbol: str) -> Dict[str, Any]:
        """Cancel all open orders on symbol strictly isolated to Aster Futures Testnet only."""
        if not self.is_testnet:
            raise ExchangeAPIError("Live trading is permanently locked (live_trading_locked=True).")
        if "testnet" not in self.base_url.lower() or self.signer.chain_id != 714:
            raise ExchangeAPIError("Safety violation: Testnet order cancellation requires testnet environment.")

        params: Dict[str, Any] = {"symbol": symbol.upper().replace(".P", "")}
        logger.info(f"[TESTNET] Cancelling ALL open orders on {symbol}...")
        return self._signed_request("DELETE", "/fapi/v3/allOpenOrders", params=params, include_user=True)
