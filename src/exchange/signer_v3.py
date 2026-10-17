"""Aster DEX Futures API V3 (Pro API) Signer.

Implements Web3 API-Wallet authentication with EIP-712 typed structured data
signing on secp256k1 curve and microsecond replay-protection nonce.
Specification: https://github.com/asterdex/api-docs/blob/master/V3(Recommended)/EN/aster-finance-futures-api-v3.md
"""

import os
import re
import threading
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from eth_account import Account
try:
    from eth_account.messages import encode_typed_data
except ImportError:
    # Older eth-account versions fallback
    from eth_account.messages import encode_structured_data as encode_typed_data

from src.core.exceptions import ExchangeAPIError
from src.core.logger import logger


EIP712_DOMAIN = {
    "name": "AsterSignTransaction",
    "version": "1",
    "chainId": 1666,
    "verifyingContract": "0x0000000000000000000000000000000000000000",
}

EIP712_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Message": [
        {"name": "msg", "type": "string"},
    ],
}

ETH_ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
USER_IDENTIFIER_PATTERN = re.compile(r"^(0x[0-9a-fA-F]{40}|[a-zA-Z0-9_\-\.]{3,64})$")


def is_valid_evm_address(address: Optional[str]) -> bool:
    """Validate that address conforms to standard 42-char EVM hex format."""
    if not address or not isinstance(address, str):
        return False
    return bool(ETH_ADDRESS_PATTERN.match(address.strip()))


def is_valid_user_identifier(identifier: Optional[str]) -> bool:
    """Validate that user identifier is either an EVM address (0x...) or an Aster account handle."""
    if not identifier or not isinstance(identifier, str):
        return False
    return bool(USER_IDENTIFIER_PATTERN.match(identifier.strip()))


def redact_address(address: Optional[str]) -> str:
    """Return safe redacted representation of an address or username."""
    if not address:
        return "[EMPTY]"
    addr = address.strip()
    if addr.startswith("0x") and len(addr) >= 10:
        return f"{addr[:6]}...{addr[-4:]}"
    elif len(addr) > 6:
        return f"{addr[:3]}...{addr[-2:]}"
    return f"{addr[:2]}***"


class AsterV3Signer:
    """EIP-712 Signer for Aster DEX Futures V3 REST requests."""

    def __init__(
        self,
        user_address: Optional[str] = None,
        signer_address: Optional[str] = None,
        signer_private_key: Optional[str] = None,
        chain_id: Optional[int] = None,
        is_testnet: bool = False,
    ) -> None:
        self.is_testnet = is_testnet
        
        # Testnet profile credential isolation
        if is_testnet:
            default_user = os.getenv("ASTER_TESTNET_USER_ADDRESS") or os.getenv("ASTER_USER_ADDRESS", "")
            default_signer = os.getenv("ASTER_TESTNET_SIGNER_ADDRESS") or os.getenv("ASTER_SIGNER_ADDRESS", "")
            default_key = os.getenv("ASTER_TESTNET_SIGNER_PRIVATE_KEY") or os.getenv("ASTER_SIGNER_PRIVATE_KEY", "")
            default_chain = int(os.getenv("ASTER_CHAIN_ID", 714))
        else:
            default_user = os.getenv("ASTER_USER_ADDRESS", "")
            default_signer = os.getenv("ASTER_SIGNER_ADDRESS", "")
            default_key = os.getenv("ASTER_SIGNER_PRIVATE_KEY", "")
            default_chain = int(os.getenv("ASTER_CHAIN_ID", 1666))

        self.user_address = (user_address or default_user).strip()
        self.signer_address = (signer_address or default_signer).strip()
        self._private_key = (signer_private_key or default_key).strip()
        self.chain_id = chain_id or default_chain

        self._last_sec = 0
        self._seq = 0
        self._nonce_lock = threading.Lock()

    def get_nonce(self) -> int:
        """Generate unique microsecond-precision timestamp nonce with thread-safe sequence."""
        with self._nonce_lock:
            now_sec = int(time.time())
            if now_sec == self._last_sec:
                self._seq += 1
            else:
                self._last_sec = now_sec
                self._seq = 0
            return now_sec * 1_000_000 + self._seq

    def validate_credentials(self) -> Tuple[bool, str]:
        """Validate format of user address, signer address, and signer private key."""
        if not self.user_address:
            return False, "ASTER_USER_ADDRESS is missing."
        if not is_valid_user_identifier(self.user_address):
            return False, f"ASTER_USER_ADDRESS '{redact_address(self.user_address)}' is not a valid EVM address or account handle."

        if not self.signer_address:
            return False, "ASTER_SIGNER_ADDRESS is missing."
        if not is_valid_evm_address(self.signer_address):
            return False, f"ASTER_SIGNER_ADDRESS '{redact_address(self.signer_address)}' is not a valid 42-char EVM address."

        if not self._private_key:
            return False, "ASTER_SIGNER_PRIVATE_KEY is missing."

        try:
            derived_address = Account.from_key(self._private_key).address
            if derived_address.lower() != self.signer_address.lower():
                return False, (
                    f"Derived address '{redact_address(derived_address)}' from private key does not match "
                    f"ASTER_SIGNER_ADDRESS '{redact_address(self.signer_address)}'."
                )
        except Exception as err:
            return False, f"Invalid signer private key format: {type(err).__name__}"

        return True, "All V3 credentials valid."

    def build_typed_data(self, msg_param_str: str) -> Dict[str, Any]:
        """Build the EIP-712 structured data dict matching Aster V3 specification."""
        domain = dict(EIP712_DOMAIN)
        domain["chainId"] = self.chain_id
        return {
            "types": EIP712_TYPES,
            "primaryType": "Message",
            "domain": domain,
            "message": {"msg": msg_param_str},
        }

    def sign_request_params(
        self,
        params: Optional[Dict[str, Any]] = None,
        include_user: bool = True,
    ) -> Tuple[Dict[str, Any], str]:
        """Sign request parameters dict using EIP-712 structured data.

        Returns:
            Tuple of (signed_params_dict, signature_hex)
        """
        if not self._private_key:
            raise ExchangeAPIError("Signer private key is required for signing requests.")

        payload = {k: v for k, v in (params or {}).items() if v is not None}
        payload["nonce"] = str(self.get_nonce())
        payload["signer"] = self.signer_address
        if include_user and self.user_address:
            payload["user"] = self.user_address

        # Encode query string for signing message
        msg_str = urllib.parse.urlencode(payload)

        # EIP-712 structured data encoding
        typed_data = self.build_typed_data(msg_str)
        encoded_message = encode_typed_data(full_message=typed_data)

        # secp256k1 sign
        signed = Account.sign_message(encoded_message, private_key=self._private_key)
        sig_hex = signed.signature.hex()
        if not sig_hex.startswith("0x"):
            # Ensure hex is clean
            pass

        payload_with_sig = dict(payload)
        payload_with_sig["signature"] = sig_hex
        return payload_with_sig, sig_hex
