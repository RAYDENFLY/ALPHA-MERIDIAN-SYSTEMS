"""Model C — On-Chain Event Parser.

Phase C1: Prospective Collection-Only Pipeline.
Decodes raw Solana JSON-RPC `logsSubscribe` notifications and Borsh-encoded Anchor events.
Strictly read-only; no external dependencies required (implements pure-Python Base58).
"""

import base64
from dataclasses import dataclass
import hashlib
import json
import struct
from typing import Any, Dict, List, Optional, Tuple

from src.model_c.types import (
    EventParseStatus,
    ModelCLifecycleState,
    NormalizedTokenRecord,
    NormalizedTradeRecord,
    PumpFunEventType,
    PumpFunProgramConstants,
    RawEventRecord,
    TokenQualityStatus,
    utc_now_iso,
)

# ---------------------------------------------------------------------------
# Pure-Python Base58 Encoding / Decoding (Solana Public Key Representation)
# ---------------------------------------------------------------------------
B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    """Encode bytes into a Base58 string."""
    n = int.from_bytes(b, "big")
    chars = []
    while n > 0:
        n, r = divmod(n, 58)
        chars.append(B58_ALPHABET[r])
    res = "".join(reversed(chars))
    pad = 0
    for byte in b:
        if byte == 0:
            pad += 1
        else:
            break
    return (B58_ALPHABET[0] * pad) + res


def b58decode(s: str) -> bytes:
    """Decode a Base58 string into bytes."""
    n = 0
    for c in s:
        idx = B58_ALPHABET.find(c)
        if idx == -1:
            raise ValueError(f"Invalid Base58 character: '{c}'")
        n = n * 58 + idx
    res = []
    while n > 0:
        n, r = divmod(n, 256)
        res.append(r)
    pad = 0
    for c in s:
        if c == B58_ALPHABET[0]:
            pad += 1
        else:
            break
    return (b"\x00" * pad) + bytes(reversed(res))


# ---------------------------------------------------------------------------
# Canonical Anchor 8-Byte Event Discriminators: sha256(f"event:{name}")[:8]
# ---------------------------------------------------------------------------
def _compute_discriminator(event_name: str) -> bytes:
    return hashlib.sha256(f"event:{event_name}".encode("utf-8")).digest()[:8]


CREATE_EVENT_DISCRIMINATOR = _compute_discriminator("CreateEvent")       # 1b72a94ddeeb6376
TRADE_EVENT_DISCRIMINATOR = _compute_discriminator("TradeEvent")         # bddb7fd34ee661ee
COMPLETE_EVENT_DISCRIMINATOR = _compute_discriminator("CompleteEvent")   # 5f72619cd42e9808
SET_PARAMS_DISCRIMINATOR = _compute_discriminator("SetParamsEvent")       # dfc39ff63e308f83


@dataclass
class ParsedEventResult:
    """Result of parsing a raw event notification."""
    raw_event: RawEventRecord
    token_record: Optional[NormalizedTokenRecord] = None
    trade_record: Optional[NormalizedTradeRecord] = None
    migration_detected: bool = False
    parse_status: EventParseStatus = EventParseStatus.PARSED
    error_message: Optional[str] = None


class PumpFunEventParser:
    """Deterministic parser for Pump.fun on-chain program logs."""

    def __init__(self, target_program_id: str = PumpFunProgramConstants.PROGRAM_ID):
        self.target_program_id = target_program_id

    def parse_rpc_notification(self, notification: Dict[str, Any]) -> List[ParsedEventResult]:
        """Parse a Solana `logsNotification` JSON-RPC message into structured records.
        
        Preserves raw payloads and never discards unrecognized events.
        """
        results: List[ParsedEventResult] = []
        recv_time = utc_now_iso()

        # 1. Structural validation of JSON-RPC container
        params = notification.get("params", {})
        result = params.get("result", {})
        value = result.get("value", {})
        context = result.get("context", {})

        signature = value.get("signature") or ""
        slot = int(context.get("slot") or 0)
        logs = value.get("logs", [])
        tx_err = value.get("err")

        # Skip failed transactions if error is present, but record if desired
        if tx_err is not None:
            # Transaction reverted on-chain
            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.UNKNOWN.value,
                raw_payload=json.dumps({"error": tx_err, "logs": logs}),
                parse_status=EventParseStatus.UNSUPPORTED.value,
            )
            raw.compute_event_id()
            results.append(ParsedEventResult(raw_event=raw, parse_status=EventParseStatus.UNSUPPORTED))
            return results

        if not logs or not isinstance(logs, list):
            # Empty or malformed log array
            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.MALFORMED.value,
                raw_payload=json.dumps(notification),
                parse_status=EventParseStatus.MALFORMED.value,
            )
            raw.compute_event_id()
            results.append(ParsedEventResult(
                raw_event=raw,
                parse_status=EventParseStatus.MALFORMED,
                error_message="Missing or invalid logs array",
            ))
            return results

        # 2. Iterate through logs looking for Program data (Anchor binary events)
        has_program_data = False
        for line in logs:
            if not isinstance(line, str):
                continue

            if line.startswith("Program data: "):
                has_program_data = True
                b64_payload = line[len("Program data: "):].strip()
                parsed = self._parse_program_data_event(
                    b64_payload=b64_payload,
                    signature=signature,
                    slot=slot,
                    recv_time=recv_time,
                )
                results.append(parsed)

        # 3. Fallback: If no binary event was found, inspect text logs
        if not has_program_data:
            fallback = self._parse_text_logs_fallback(
                logs=logs,
                signature=signature,
                slot=slot,
                recv_time=recv_time,
            )
            if fallback:
                results.append(fallback)

        return results

    def _parse_program_data_event(
        self,
        b64_payload: str,
        signature: str,
        slot: int,
        recv_time: str,
    ) -> ParsedEventResult:
        """Decode a Base64 Anchor event emitted via `sol_log_data`."""
        try:
            data = base64.b64decode(b64_payload)
        except Exception as e:
            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.MALFORMED.value,
                raw_payload=b64_payload,
                parse_status=EventParseStatus.MALFORMED.value,
            )
            raw.compute_event_id()
            return ParsedEventResult(
                raw_event=raw,
                parse_status=EventParseStatus.MALFORMED,
                error_message=f"Base64 decode failed: {e}",
            )

        if len(data) < 8:
            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.MALFORMED.value,
                raw_payload=b64_payload,
                parse_status=EventParseStatus.MALFORMED.value,
            )
            raw.compute_event_id()
            return ParsedEventResult(
                raw_event=raw,
                parse_status=EventParseStatus.MALFORMED,
                error_message="Event payload shorter than 8-byte discriminator",
            )

        discriminator = data[:8]

        # Dispatch based on Anchor event discriminator
        if discriminator == CREATE_EVENT_DISCRIMINATOR:
            return self._decode_create_event(data, b64_payload, signature, slot, recv_time)
        elif discriminator == TRADE_EVENT_DISCRIMINATOR:
            return self._decode_trade_event(data, b64_payload, signature, slot, recv_time)
        elif discriminator == COMPLETE_EVENT_DISCRIMINATOR:
            return self._decode_complete_event(data, b64_payload, signature, slot, recv_time)
        elif discriminator == SET_PARAMS_DISCRIMINATOR:
            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.SET_PARAMS.value,
                raw_payload=b64_payload,
                parse_status=EventParseStatus.PARSED.value,
            )
            raw.compute_event_id()
            return ParsedEventResult(raw_event=raw, parse_status=EventParseStatus.PARSED)
        else:
            # Unrecognized event discriminator; MUST NOT be silently discarded
            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.UNKNOWN.value,
                raw_payload=b64_payload,
                parse_status=EventParseStatus.UNSUPPORTED.value,
            )
            raw.compute_event_id()
            return ParsedEventResult(
                raw_event=raw,
                parse_status=EventParseStatus.UNSUPPORTED,
                error_message=f"Unknown Anchor discriminator: {discriminator.hex()}",
            )

    def _decode_create_event(
        self,
        data: bytes,
        raw_b64: str,
        signature: str,
        slot: int,
        recv_time: str,
    ) -> ParsedEventResult:
        """Decode Anchor `CreateEvent`:
        name (string), symbol (string), uri (string), mint (32B), bonding_curve (32B), user (32B)
        """
        offset = 8
        try:
            name, offset = self._read_borsh_string(data, offset)
            symbol, offset = self._read_borsh_string(data, offset)
            uri, offset = self._read_borsh_string(data, offset)

            if len(data) < offset + 96:
                raise ValueError(f"Payload underflow for CreateEvent pubkeys: {len(data)} < {offset + 96}")

            mint_bytes = data[offset:offset + 32]
            offset += 32
            bonding_curve_bytes = data[offset:offset + 32]
            offset += 32
            user_bytes = data[offset:offset + 32]
            offset += 32

            mint = b58encode(mint_bytes)
            curve = b58encode(bonding_curve_bytes)
            user = b58encode(user_bytes)

            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.CREATE.value,
                raw_payload=raw_b64,
                parse_status=EventParseStatus.PARSED.value,
            )
            raw.compute_event_id()

            token = NormalizedTokenRecord(
                mint_address=mint,
                creation_signature=signature,
                creation_slot=slot,
                creation_timestamp=None,  # Populated when block_time is provided
                creator_address=user,
                bonding_curve_pda=curve,
                name=name,
                symbol=symbol,
                metadata_uri=uri,
                first_seen_at=recv_time,
                last_seen_at=recv_time,
                lifecycle_state=ModelCLifecycleState.DISCOVERED.value,
                data_quality_status=TokenQualityStatus.VALID.value,
            )

            return ParsedEventResult(
                raw_event=raw,
                token_record=token,
                parse_status=EventParseStatus.PARSED,
            )

        except Exception as e:
            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.CREATE.value,
                raw_payload=raw_b64,
                parse_status=EventParseStatus.MALFORMED.value,
            )
            raw.compute_event_id()
            return ParsedEventResult(
                raw_event=raw,
                parse_status=EventParseStatus.MALFORMED,
                error_message=f"Borsh decode error in CreateEvent: {e}",
            )

    def _decode_trade_event(
        self,
        data: bytes,
        raw_b64: str,
        signature: str,
        slot: int,
        recv_time: str,
    ) -> ParsedEventResult:
        """Decode Anchor `TradeEvent`:
        mint (32B), sol_amount (u64), token_amount (u64), is_buy (bool), user (32B),
        timestamp (i64), virtual_sol_reserves (u64), virtual_token_reserves (u64),
        real_sol_reserves (u64), real_token_reserves (u64)
        """
        offset = 8
        expected_len = 8 + 32 + 8 + 8 + 1 + 32 + 8 + 8 + 8 + 8 + 8  # 137 bytes
        if len(data) < expected_len:
            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.TRADE.value,
                raw_payload=raw_b64,
                parse_status=EventParseStatus.MALFORMED.value,
            )
            raw.compute_event_id()
            return ParsedEventResult(
                raw_event=raw,
                parse_status=EventParseStatus.MALFORMED,
                error_message=f"Payload underflow for TradeEvent: {len(data)} < {expected_len}",
            )

        try:
            mint_bytes = data[offset:offset + 32]
            offset += 32
            sol_amount, token_amount = struct.unpack_from("<QQ", data, offset)
            offset += 16
            is_buy = bool(data[offset])
            offset += 1
            user_bytes = data[offset:offset + 32]
            offset += 32
            trade_ts, v_sol, v_token, r_sol, r_token = struct.unpack_from("<qQQQQ", data, offset)

            mint = b58encode(mint_bytes)
            user = b58encode(user_bytes)

            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                block_time=trade_ts,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.TRADE.value,
                raw_payload=raw_b64,
                parse_status=EventParseStatus.PARSED.value,
            )
            event_id = raw.compute_event_id()

            trade = NormalizedTradeRecord(
                event_id=event_id,
                signature=signature,
                slot=slot,
                block_time=trade_ts,
                mint_address=mint,
                trader_address=user,
                is_buy=is_buy,
                sol_amount_lamports=sol_amount,
                token_amount_units=token_amount,
                virtual_sol_reserves_lamports=v_sol,
                virtual_token_reserves_units=v_token,
                real_sol_reserves_lamports=r_sol,
                real_token_reserves_units=r_token,
                received_at=recv_time,
            )

            return ParsedEventResult(
                raw_event=raw,
                trade_record=trade,
                parse_status=EventParseStatus.PARSED,
            )

        except Exception as e:
            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.TRADE.value,
                raw_payload=raw_b64,
                parse_status=EventParseStatus.MALFORMED.value,
            )
            raw.compute_event_id()
            return ParsedEventResult(
                raw_event=raw,
                parse_status=EventParseStatus.MALFORMED,
                error_message=f"Borsh decode error in TradeEvent: {e}",
            )

    def _decode_complete_event(
        self,
        data: bytes,
        raw_b64: str,
        signature: str,
        slot: int,
        recv_time: str,
    ) -> ParsedEventResult:
        """Decode Anchor `CompleteEvent`:
        user (32B), mint (32B), bonding_curve (32B), timestamp (i64)
        """
        offset = 8
        expected_len = 8 + 32 + 32 + 32 + 8  # 112 bytes
        if len(data) < expected_len:
            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.COMPLETE.value,
                raw_payload=raw_b64,
                parse_status=EventParseStatus.MALFORMED.value,
            )
            raw.compute_event_id()
            return ParsedEventResult(
                raw_event=raw,
                parse_status=EventParseStatus.MALFORMED,
                error_message=f"Payload underflow for CompleteEvent: {len(data)} < {expected_len}",
            )

        try:
            user_bytes = data[offset:offset + 32]
            offset += 32
            mint_bytes = data[offset:offset + 32]
            offset += 32
            curve_bytes = data[offset:offset + 32]
            offset += 32
            compl_ts = struct.unpack_from("<q", data, offset)[0]

            mint = b58encode(mint_bytes)
            user = b58encode(user_bytes)
            curve = b58encode(curve_bytes)

            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                block_time=compl_ts,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.COMPLETE.value,
                raw_payload=raw_b64,
                parse_status=EventParseStatus.PARSED.value,
            )
            raw.compute_event_id()

            # Represents a confirmed graduation event
            token_update = NormalizedTokenRecord(
                mint_address=mint,
                creation_signature="",
                creation_slot=slot,
                creator_address=user,
                bonding_curve_pda=curve,
                first_seen_at=recv_time,
                last_seen_at=recv_time,
                lifecycle_state=ModelCLifecycleState.MIGRATED.value,
                migration_status=True,
                migration_timestamp=compl_ts,
                migration_destination="RAYDIUM",
                data_quality_status=TokenQualityStatus.VALID.value,
            )

            return ParsedEventResult(
                raw_event=raw,
                token_record=token_update,
                migration_detected=True,
                parse_status=EventParseStatus.PARSED,
            )

        except Exception as e:
            raw = RawEventRecord(
                signature=signature,
                slot=slot,
                received_at=recv_time,
                program_id=self.target_program_id,
                event_type=PumpFunEventType.COMPLETE.value,
                raw_payload=raw_b64,
                parse_status=EventParseStatus.MALFORMED.value,
            )
            raw.compute_event_id()
            return ParsedEventResult(
                raw_event=raw,
                parse_status=EventParseStatus.MALFORMED,
                error_message=f"Borsh decode error in CompleteEvent: {e}",
            )

    def _parse_text_logs_fallback(
        self,
        logs: List[str],
        signature: str,
        slot: int,
        recv_time: str,
    ) -> Optional[ParsedEventResult]:
        """Fallback for logs that lack Base64 `Program data:` entries."""
        event_type = PumpFunEventType.UNKNOWN.value
        detected = False

        for line in logs:
            if "Instruction: Create" in line:
                event_type = PumpFunEventType.CREATE.value
                detected = True
                break
            elif "Instruction: Buy" in line or "Instruction: Sell" in line:
                event_type = PumpFunEventType.TRADE.value
                detected = True
                break
            elif "Instruction: Complete" in line:
                event_type = PumpFunEventType.COMPLETE.value
                detected = True
                break

        if not detected:
            return None

        raw = RawEventRecord(
            signature=signature,
            slot=slot,
            received_at=recv_time,
            program_id=self.target_program_id,
            event_type=event_type,
            raw_payload=json.dumps(logs),
            parse_status=EventParseStatus.PARTIALLY_PARSED.value,
        )
        raw.compute_event_id()
        return ParsedEventResult(
            raw_event=raw,
            parse_status=EventParseStatus.PARTIALLY_PARSED,
            error_message="Instruction detected in text log, but binary event payload omitted by RPC node",
        )

    def _read_borsh_string(self, data: bytes, offset: int) -> Tuple[str, int]:
        """Read a 4-byte little-endian length prefixed UTF-8 string."""
        if len(data) < offset + 4:
            raise ValueError(f"String length prefix underflow at offset {offset}")
        str_len = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if len(data) < offset + str_len:
            raise ValueError(f"String data underflow: expected {str_len} bytes at {offset}, got {len(data) - offset}")
        val = data[offset:offset + str_len].decode("utf-8", errors="replace")
        offset += str_len
        return val, offset
