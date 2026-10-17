"""Aster DEX Perpetual Futures REST Market Data Adapter.

Connects to https://fapi.asterdex.com using verified endpoints.
Handles pagination, rate limits, retries, and Candle parsing.
"""

import hashlib
import hmac
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


class AsterDexAdapter(BaseExchangeAdapter):
    """Adapter for Aster DEX Perpetual Futures REST API."""

    def __init__(
        self,
        base_url: str = "https://fapi.asterdex.com",
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        request_timeout: int = 15,
        max_retries: int = 4,
        retry_delay_sec: float = 1.0,
        load_env_credentials: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key if api_key is not None else (os.getenv("ASTER_API_KEY") if load_env_credentials else None)
        self.api_secret = api_secret if api_secret is not None else (os.getenv("ASTER_API_SECRET") if load_env_credentials else None)
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.retry_delay_sec = retry_delay_sec
        self._specs_cache: Dict[str, SymbolSpecs] = {}
        self._time_offset_ms: int = 0
        self._time_synced: bool = False

        # Configure session with connection pooling and socket retries
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
                    headers={"User-Agent": "SMC-SNR-Scanner/1.0"},
                )
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", self.retry_delay_sec * attempt))
                    logger.warning(f"Aster DEX rate limit (429). Backing off for {retry_after}s.")
                    time.sleep(retry_after)
                    continue
                elif response.status_code in (500, 502, 503, 504):
                    logger.warning(
                        f"Aster DEX server error ({response.status_code}) on {url}. Attempt {attempt}/{self.max_retries}."
                    )
                    time.sleep(self.retry_delay_sec * attempt)
                    continue
                else:
                    error_msg = f"HTTP {response.status_code} on {url}: {response.text}"
                    logger.error(error_msg)
                    raise ExchangeAPIError(error_msg)
            except requests.RequestException as e:
                logger.warning(f"Network error on {url} (attempt {attempt}/{self.max_retries}): {e}")
                if attempt == self.max_retries:
                    raise ExchangeAPIError(f"Failed to reach Aster DEX after {self.max_retries} attempts: {e}") from e
                time.sleep(self.retry_delay_sec * attempt)

        raise ExchangeAPIError(f"Aster DEX request failed for {endpoint}")

    def ping(self) -> bool:
        """Test exchange connectivity."""
        try:
            res = self._request("/fapi/v1/ping")
            return isinstance(res, dict)
        except Exception as e:
            logger.error(f"Ping failed: {e}")
            return False

    def get_exchange_info(self) -> Dict[str, SymbolSpecs]:
        """Fetch and cache all perpetual contract specifications."""
        data = self._request("/fapi/v1/exchangeInfo")
        symbols_raw = data.get("symbols", [])
        self._specs_cache.clear()
        for item in symbols_raw:
            specs = SymbolSpecs.from_aster_dict(item)
            self._specs_cache[specs.symbol] = specs
        logger.info(f"Loaded {len(self._specs_cache)} symbol specifications from Aster DEX.")
        return self._specs_cache

    def get_symbol_specs(self, symbol: str) -> SymbolSpecs:
        """Retrieve specifications for a given symbol."""
        if not self._specs_cache:
            self.get_exchange_info()
        clean_symbol = symbol.upper().replace(".P", "")
        if clean_symbol not in self._specs_cache:
            raise SymbolNotFoundError(f"Symbol '{clean_symbol}' not found on Aster DEX perpetual market.")
        return self._specs_cache[clean_symbol]

    def get_klines(
        self,
        symbol: str,
        timeframe: str,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 1000,
    ) -> List[Candle]:
        """Fetch candlestick bars for a symbol and timeframe."""
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

        raw_klines = self._request("/fapi/v1/klines", params=params)
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
        """Fetch historical funding rates."""
        clean_symbol = symbol.upper().replace(".P", "")
        params: Dict[str, Any] = {
            "symbol": clean_symbol,
            "limit": min(limit, 1000),
        }
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms

        data = self._request("/fapi/v1/fundingRate", params=params)
        return data if isinstance(data, list) else []

    # =========================================================================
    # Authenticated Execution & Private Account Endpoints (Legacy V1 / HMAC)
    # NOTE: Aster DEX has deprecated new V1 API Key creation since March 25, 2026.
    # Aster recommends Futures API V3 (Pro API) with Web3 API-Wallet (EIP-712).
    # In ExecutionMode.PAPER and ExecutionMode.RESEARCH, these methods are NEVER
    # invoked by the engine (PaperExecutionEngine handles simulation locally).
    # =========================================================================
    def get_server_time(self) -> int:
        """Fetch current exchange server time in milliseconds."""
        data = self._request("/fapi/v1/time")
        if isinstance(data, dict) and "serverTime" in data:
            return int(data["serverTime"])
        raise ExchangeAPIError(f"Unexpected server time response: {data}")

    def sync_server_time(self) -> int:
        """Synchronize local clock offset with Aster DEX server time."""
        try:
            srv_time = self.get_server_time()
            now_ms = int(time.time() * 1000)
            self._time_offset_ms = srv_time - now_ms
            self._time_synced = True
            logger.info(f"Synchronized with Aster DEX clock: offset {self._time_offset_ms}ms")
            return self._time_offset_ms
        except Exception as e:
            logger.warning(f"Could not synchronize server time: {e}. Using local clock.")
            return 0

    def _get_timestamp_ms(self) -> int:
        """Get current timestamp in milliseconds adjusted for server offset."""
        return int(time.time() * 1000) + self._time_offset_ms

    def _generate_signature(self, query_string: str) -> str:
        """Generate HMAC-SHA256 signature for Aster DEX private endpoints."""
        if not self.api_secret:
            raise ExchangeAPIError("Aster DEX API secret (ASTER_API_SECRET) is required for signature.")
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _signed_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute an authenticated request with HMAC-SHA256 signature and API-Key."""
        if not self.api_key or not self.api_secret:
            raise ExchangeAPIError(
                "Aster DEX API key and secret are required for authenticated execution endpoints. "
                "Provide them via arguments or environment variables ASTER_API_KEY / ASTER_API_SECRET."
            )

        if not self._time_synced:
            self.sync_server_time()

        payload = {k: v for k, v in (params or {}).items() if v is not None}
        payload["timestamp"] = self._get_timestamp_ms()
        if "recvWindow" not in payload:
            payload["recvWindow"] = 5000

        query_string = urllib.parse.urlencode(payload)
        signature = self._generate_signature(query_string)
        full_query = f"{query_string}&signature={signature}"
        url = f"{self.base_url}{endpoint}"
        headers = {
            "X-MBX-APIKEY": self.api_key,
            "User-Agent": "SMC-SNR-Scanner/1.0",
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                if method.upper() == "GET":
                    response = self.session.get(f"{url}?{full_query}", headers=headers, timeout=self.request_timeout)
                elif method.upper() == "POST":
                    response = self.session.post(f"{url}?{full_query}", headers=headers, timeout=self.request_timeout)
                elif method.upper() == "DELETE":
                    response = self.session.delete(f"{url}?{full_query}", headers=headers, timeout=self.request_timeout)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 400:
                    res_json = {}
                    try:
                        res_json = response.json()
                    except Exception:
                        pass
                    # Code -4046 means margin type is already what was requested (graceful)
                    if res_json.get("code") == -4046:
                        return res_json
                    error_msg = f"HTTP 400 on {endpoint}: {res_json.get('msg', response.text)} (code={res_json.get('code')})"
                    logger.error(error_msg)
                    raise ExchangeAPIError(error_msg)
                elif response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", self.retry_delay_sec * attempt))
                    logger.warning(f"Aster DEX rate limit (429). Backing off for {retry_after}s.")
                    time.sleep(retry_after)
                    continue
                elif response.status_code in (500, 502, 503, 504):
                    logger.warning(
                        f"Aster DEX server error ({response.status_code}) on {url}. Attempt {attempt}/{self.max_retries}."
                    )
                    time.sleep(self.retry_delay_sec * attempt)
                    continue
                else:
                    error_msg = f"HTTP {response.status_code} on {url}: {response.text}"
                    logger.error(error_msg)
                    raise ExchangeAPIError(error_msg)
            except requests.RequestException as e:
                logger.warning(f"Network error on {url} (attempt {attempt}/{self.max_retries}): {e}")
                if attempt == self.max_retries:
                    raise ExchangeAPIError(f"Failed to reach Aster DEX after {self.max_retries} attempts: {e}") from e
                time.sleep(self.retry_delay_sec * attempt)

        raise ExchangeAPIError(f"Aster DEX signed request failed for {endpoint}")

    def get_account_balance(self) -> List[Dict[str, Any]]:
        """Fetch perpetual account asset balances (e.g. USDT available, wallet balance)."""
        data = self._signed_request("GET", "/fapi/v2/balance")
        return data if isinstance(data, list) else []

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch open perpetual positions and risk metrics."""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper().replace(".P", "")
        data = self._signed_request("GET", "/fapi/v2/positionRisk", params=params)
        return data if isinstance(data, list) else []

    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Set position leverage for a perpetual contract."""
        clean_symbol = symbol.upper().replace(".P", "")
        return self._signed_request(
            "POST",
            "/fapi/v1/leverage",
            params={"symbol": clean_symbol, "leverage": int(leverage)},
        )

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        """Set margin type (ISOLATED or CROSSED) for a symbol."""
        clean_symbol = symbol.upper().replace(".P", "")
        return self._signed_request(
            "POST",
            "/fapi/v1/marginType",
            params={"symbol": clean_symbol, "marginType": margin_type.upper()},
        )

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
        """Submit a new order to Aster DEX."""
        clean_symbol = symbol.upper().replace(".P", "")
        payload: Dict[str, Any] = {
            "symbol": clean_symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
        }
        if price is not None:
            payload["price"] = price
        if stop_price is not None:
            payload["stopPrice"] = stop_price
        if time_in_force is not None and order_type.upper() == "LIMIT":
            payload["timeInForce"] = time_in_force.upper()
        if client_order_id is not None:
            payload["newClientOrderId"] = client_order_id
        if reduce_only:
            payload["reduceOnly"] = "true"
        if close_position:
            payload["closePosition"] = "true"

        return self._signed_request("POST", "/fapi/v1/order", params=payload)

    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[Union[int, str]] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel an existing order by exchange orderId or clientOrderId."""
        clean_symbol = symbol.upper().replace(".P", "")
        payload: Dict[str, Any] = {"symbol": clean_symbol}
        if order_id is not None:
            payload["orderId"] = str(order_id)
        if client_order_id is not None:
            payload["origClientOrderId"] = client_order_id
        return self._signed_request("DELETE", "/fapi/v1/order", params=payload)

    def cancel_all_open_orders(self, symbol: str) -> Dict[str, Any]:
        """Cancel all open orders for a specific symbol."""
        clean_symbol = symbol.upper().replace(".P", "")
        return self._signed_request("DELETE", "/fapi/v1/allOpenOrders", params={"symbol": clean_symbol})

    def get_order(
        self,
        symbol: str,
        order_id: Optional[Union[int, str]] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query execution status of a specific order."""
        clean_symbol = symbol.upper().replace(".P", "")
        payload: Dict[str, Any] = {"symbol": clean_symbol}
        if order_id is not None:
            payload["orderId"] = str(order_id)
        if client_order_id is not None:
            payload["origClientOrderId"] = client_order_id
        return self._signed_request("GET", "/fapi/v1/order", params=payload)

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query currently active open orders."""
        payload: Dict[str, Any] = {}
        if symbol:
            payload["symbol"] = symbol.upper().replace(".P", "")
        data = self._signed_request("GET", "/fapi/v1/openOrders", params=payload)
        return data if isinstance(data, list) else []

