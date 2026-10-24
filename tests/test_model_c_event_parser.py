"""Unit tests for Model C event parser.

Covers:
1. Valid creation-event parsing.
2. Valid trade-event parsing.
3. Unknown event types.
4. Malformed payloads.
10. Missing block time.
11. Missing token mint.
12. Unknown migration destination.
"""

import base64
import hashlib
import struct
import pytest

from src.model_c.event_parser import (
    PumpFunEventParser,
    b58encode,
    b58decode,
    CREATE_EVENT_DISCRIMINATOR,
    TRADE_EVENT_DISCRIMINATOR,
    COMPLETE_EVENT_DISCRIMINATOR,
)
from src.model_c.types import (
    EventParseStatus,
    ModelCLifecycleState,
    PumpFunEventType,
    TokenQualityStatus,
)


def _encode_create_event_payload(
    name: str = "PepePump",
    symbol: str = "PEPE",
    uri: str = "https://arweave.net/metadata.json",
    mint_bytes: bytes = b"\x01" * 32,
    curve_bytes: bytes = b"\x02" * 32,
    user_bytes: bytes = b"\x03" * 32,
) -> str:
    """Build a synthetic Borsh-encoded CreateEvent base64 string."""
    name_b = name.encode("utf-8")
    sym_b = symbol.encode("utf-8")
    uri_b = uri.encode("utf-8")

    payload = CREATE_EVENT_DISCRIMINATOR
    for s in [name_b, sym_b, uri_b]:
        payload += struct.pack("<I", len(s)) + s
    payload += mint_bytes + curve_bytes + user_bytes
    return base64.b64encode(payload).decode("ascii")


def _encode_trade_event_payload(
    mint_bytes: bytes = b"\x01" * 32,
    sol_amount: int = 1_500_000_000,       # 1.5 SOL
    token_amount: int = 50_000_000_000,    # 50k tokens
    is_buy: bool = True,
    user_bytes: bytes = b"\x04" * 32,
    timestamp: int = 1729800050,
    v_sol: int = 31_500_000_000,
    v_token: int = 1_023_000_000_000_000,
    r_sol: int = 1_500_000_000,
    r_token: int = 750_000_000_000_000,
) -> str:
    """Build a synthetic Borsh-encoded TradeEvent base64 string."""
    payload = TRADE_EVENT_DISCRIMINATOR
    payload += mint_bytes
    payload += struct.pack("<QQ", sol_amount, token_amount)
    payload += struct.pack("<?", is_buy)
    payload += user_bytes
    payload += struct.pack("<qQQQQ", timestamp, v_sol, v_token, r_sol, r_token)
    return base64.b64encode(payload).decode("ascii")


def _encode_complete_event_payload(
    user_bytes: bytes = b"\x05" * 32,
    mint_bytes: bytes = b"\x01" * 32,
    curve_bytes: bytes = b"\x02" * 32,
    timestamp: int = 1729800500,
) -> str:
    """Build a synthetic Borsh-encoded CompleteEvent base64 string."""
    payload = COMPLETE_EVENT_DISCRIMINATOR
    payload += user_bytes + mint_bytes + curve_bytes
    payload += struct.pack("<q", timestamp)
    return base64.b64encode(payload).decode("ascii")


class TestPumpFunEventParser:
    def setup_method(self):
        self.parser = PumpFunEventParser()

    def test_1_valid_creation_event_parsing(self):
        b64 = _encode_create_event_payload(name="RocketDog", symbol="RDOG")
        notification = {
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 290123456},
                    "value": {
                        "signature": "5UfDTestSignatureCreation111111111111111111111111",
                        "err": None,
                        "logs": [
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                            "Program log: Instruction: Create",
                            f"Program data: {b64}",
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P success",
                        ],
                    },
                }
            },
        }

        results = self.parser.parse_rpc_notification(notification)
        assert len(results) == 1
        res = results[0]

        assert res.parse_status == EventParseStatus.PARSED
        assert res.raw_event.event_type == PumpFunEventType.CREATE.value
        assert res.raw_event.slot == 290123456
        assert res.raw_event.signature.startswith("5UfDTest")

        assert res.token_record is not None
        assert res.token_record.name == "RocketDog"
        assert res.token_record.symbol == "RDOG"
        assert res.token_record.creation_slot == 290123456
        assert res.token_record.lifecycle_state == ModelCLifecycleState.DISCOVERED.value
        assert res.token_record.mint_address == b58encode(b"\x01" * 32)
        assert res.token_record.creator_address == b58encode(b"\x03" * 32)

    def test_2_valid_trade_event_parsing(self):
        b64 = _encode_trade_event_payload(sol_amount=2_000_000_000, is_buy=True)
        notification = {
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 290123480},
                    "value": {
                        "signature": "3AbcTradeSig1111111111111111111111111111111111111",
                        "err": None,
                        "logs": [
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                            f"Program data: {b64}",
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P success",
                        ],
                    },
                }
            },
        }

        results = self.parser.parse_rpc_notification(notification)
        assert len(results) == 1
        res = results[0]

        assert res.parse_status == EventParseStatus.PARSED
        assert res.raw_event.event_type == PumpFunEventType.TRADE.value
        assert res.trade_record is not None
        assert res.trade_record.is_buy is True
        assert res.trade_record.sol_amount_lamports == 2_000_000_000
        assert res.trade_record.block_time == 1729800050
        assert res.trade_record.mint_address == b58encode(b"\x01" * 32)

    def test_3_unknown_event_types_preserved_not_discarded(self):
        # Build an event with an unrecognized 8-byte discriminator
        unknown_disc = hashlib.sha256(b"event:SomeFutureEventV2").digest()[:8]
        fake_payload = unknown_disc + b"arbitrary_future_bytes_here"
        b64 = base64.b64encode(fake_payload).decode("ascii")

        notification = {
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 290123500},
                    "value": {
                        "signature": "9XyzUnknownSig11111111111111111111111111111111111",
                        "err": None,
                        "logs": [
                            f"Program data: {b64}",
                        ],
                    },
                }
            },
        }

        results = self.parser.parse_rpc_notification(notification)
        assert len(results) == 1
        res = results[0]
        # Must be preserved as UNKNOWN and UNSUPPORTED, never silently dropped
        assert res.parse_status == EventParseStatus.UNSUPPORTED
        assert res.raw_event.event_type == PumpFunEventType.UNKNOWN.value
        assert "Unknown Anchor discriminator" in (res.error_message or "")

    def test_4_malformed_payloads_safely_handled(self):
        # Truncated base64 payload (only 4 bytes)
        b64 = base64.b64encode(b"\x01\x02\x03\x04").decode("ascii")

        notification = {
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 290123510},
                    "value": {
                        "signature": "1111MalformedSig111111111111111111111111111111111",
                        "err": None,
                        "logs": [
                            f"Program data: {b64}",
                        ],
                    },
                }
            },
        }

        results = self.parser.parse_rpc_notification(notification)
        assert len(results) == 1
        res = results[0]
        assert res.parse_status == EventParseStatus.MALFORMED
        assert res.raw_event.event_type == PumpFunEventType.MALFORMED.value

    def test_10_missing_block_time_tolerated(self):
        # Text-only logs fallback where block time is not embedded
        notification = {
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 290123520},
                    "value": {
                        "signature": "2222NoBlockTimeSig11111111111111111111111111111111",
                        "err": None,
                        "logs": [
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                            "Program log: Instruction: Create",
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P success",
                        ],
                    },
                }
            },
        }

        results = self.parser.parse_rpc_notification(notification)
        assert len(results) == 1
        res = results[0]
        assert res.parse_status == EventParseStatus.PARTIALLY_PARSED
        assert res.raw_event.block_time is None  # Handled cleanly without inventing fake time
        assert res.raw_event.slot == 290123520

    def test_11_missing_token_mint_handled(self):
        # In a text-only partial log, mint cannot be deduced without RPC getTransaction
        notification = {
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 290123530},
                    "value": {
                        "signature": "3333NoMintTextSig111111111111111111111111111111111",
                        "err": None,
                        "logs": [
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                            "Program log: Instruction: Buy",
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P success",
                        ],
                    },
                }
            },
        }

        results = self.parser.parse_rpc_notification(notification)
        assert len(results) == 1
        res = results[0]
        # Partial parse: raw event recorded, trade_record is None because mint is missing
        assert res.parse_status == EventParseStatus.PARTIALLY_PARSED
        assert res.trade_record is None
        assert res.raw_event.event_type == PumpFunEventType.TRADE.value

    def test_12_migration_event_parsing(self):
        b64 = _encode_complete_event_payload()
        notification = {
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 290123999},
                    "value": {
                        "signature": "4444MigrationSig1111111111111111111111111111111111",
                        "err": None,
                        "logs": [
                            f"Program data: {b64}",
                        ],
                    },
                }
            },
        }

        results = self.parser.parse_rpc_notification(notification)
        assert len(results) == 1
        res = results[0]
        assert res.parse_status == EventParseStatus.PARSED
        assert res.migration_detected is True
        assert res.token_record is not None
        assert res.token_record.lifecycle_state == ModelCLifecycleState.MIGRATED.value
        assert res.token_record.migration_status is True
        assert res.token_record.migration_timestamp == 1729800500
