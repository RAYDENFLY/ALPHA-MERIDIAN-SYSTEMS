"""Unit and Mock Tests for Aster Futures V3 Signer and Adapter.

Verifies:
1. EVM address validation and sanitization.
2. Signer key pair matching and verification.
3. Microsecond nonce uniqueness.
4. EIP-712 typed structured data formatting and secp256k1 signing.
5. Mocked read-only requests (noop, balance, positions).
6. Hard safety locks against live order execution.
"""

from unittest.mock import MagicMock, patch
import pytest
from eth_account import Account

from src.core.exceptions import ExchangeAPIError
from src.exchange.signer_v3 import (
    AsterV3Signer,
    is_valid_evm_address,
    redact_address,
    EIP712_DOMAIN,
    EIP712_TYPES,
)
from src.exchange.aster_v3 import AsterDexV3Adapter


# Dummy test keys (never used in production)
MOCK_USER_ADDR = "0x1111111111111111111111111111111111111111"
mock_account = Account.create()
MOCK_SIGNER_ADDR = mock_account.address
MOCK_SIGNER_PK = mock_account.key.hex()


class TestAddressValidationAndRedaction:
    def test_valid_evm_address(self):
        assert is_valid_evm_address(MOCK_USER_ADDR) is True
        assert is_valid_evm_address(MOCK_SIGNER_ADDR) is True

    def test_invalid_evm_address(self):
        assert is_valid_evm_address("raydenfly") is False
        assert is_valid_evm_address("0x123") is False
        assert is_valid_evm_address("") is False
        assert is_valid_evm_address(None) is False
        assert is_valid_evm_address("0x" + "g" * 40) is False

    def test_redact_address(self):
        redacted = redact_address(MOCK_USER_ADDR)
        assert redacted.startswith("0x1111")
        assert redacted.endswith("1111")
        assert len(redacted) < len(MOCK_USER_ADDR)
        assert redact_address("") == "[EMPTY]"
        assert redact_address(None) == "[EMPTY]"


class TestAsterV3Signer:
    def test_validate_credentials_success(self):
        signer = AsterV3Signer(
            user_address=MOCK_USER_ADDR,
            signer_address=MOCK_SIGNER_ADDR,
            signer_private_key=MOCK_SIGNER_PK,
        )
        is_valid, msg = signer.validate_credentials()
        assert is_valid is True
        assert "valid" in msg.lower()

    def test_user_identifier_formats(self):
        from src.exchange.signer_v3 import is_valid_user_identifier
        assert is_valid_user_identifier("0x1111111111111111111111111111111111111111") is True
        assert is_valid_user_identifier("raydenfly") is True
        assert is_valid_user_identifier("user_123") is True
        assert is_valid_user_identifier("") is False
        assert is_valid_user_identifier("!?#$") is False

    def test_validate_credentials_invalid_user(self):
        signer = AsterV3Signer(
            user_address="!?#$%",
            signer_address=MOCK_SIGNER_ADDR,
            signer_private_key=MOCK_SIGNER_PK,
        )
        is_valid, msg = signer.validate_credentials()
        assert is_valid is False
        assert "not a valid EVM address or account handle" in msg

    def test_validate_credentials_key_mismatch(self):
        other_account = Account.create()
        signer = AsterV3Signer(
            user_address=MOCK_USER_ADDR,
            signer_address=other_account.address, # mismatch
            signer_private_key=MOCK_SIGNER_PK,
        )
        is_valid, msg = signer.validate_credentials()
        assert is_valid is False
        assert "does not match" in msg

    def test_nonce_generation_monotonic_and_unique(self):
        signer = AsterV3Signer(
            user_address=MOCK_USER_ADDR,
            signer_address=MOCK_SIGNER_ADDR,
            signer_private_key=MOCK_SIGNER_PK,
        )
        n1 = signer.get_nonce()
        n2 = signer.get_nonce()
        n3 = signer.get_nonce()
        assert n1 < n2 < n3
        assert n1 > 1_700_000_000_000_000  # microsecond timestamp

    def test_sign_request_params(self):
        signer = AsterV3Signer(
            user_address=MOCK_USER_ADDR,
            signer_address=MOCK_SIGNER_ADDR,
            signer_private_key=MOCK_SIGNER_PK,
        )
        payload, sig_hex = signer.sign_request_params({"symbol": "BTCUSDT"}, include_user=True)
        assert "nonce" in payload
        assert payload["signer"] == MOCK_SIGNER_ADDR
        assert payload["user"] == MOCK_USER_ADDR
        assert payload["symbol"] == "BTCUSDT"
        assert payload["signature"] == sig_hex
        assert len(sig_hex) >= 130


class TestAsterDexV3AdapterMocked:
    @pytest.fixture
    def adapter(self):
        return AsterDexV3Adapter(
            user_address=MOCK_USER_ADDR,
            signer_address=MOCK_SIGNER_ADDR,
            signer_private_key=MOCK_SIGNER_PK,
            live_trading_locked=True,
        )

    def test_mock_noop(self, adapter):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 200, "msg": "success"}

        with patch.object(adapter.session, "post", return_value=mock_resp) as mock_post:
            res = adapter.noop()
            assert res["code"] == 200
            assert mock_post.called
            data_sent = mock_post.call_args[1]["data"]
            assert data_sent["signer"] == MOCK_SIGNER_ADDR
            assert data_sent["user"] == MOCK_USER_ADDR
            assert "signature" in data_sent

    def test_mock_get_account_balance(self, adapter):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"asset": "USDT", "balance": "100.0"}]

        with patch.object(adapter.session, "get", return_value=mock_resp) as mock_get:
            res = adapter.get_account_balance()
            assert len(res) == 1
            assert res[0]["asset"] == "USDT"
            assert mock_get.called

    def test_mock_get_positions(self, adapter):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"symbol": "BTCUSDT", "positionAmt": "0.0"}]

        with patch.object(adapter.session, "get", return_value=mock_resp):
            res = adapter.get_positions(symbol="BTCUSDT")
            assert len(res) == 1
            assert res[0]["symbol"] == "BTCUSDT"

    def test_live_trading_locked_prevents_order(self, adapter):
        with pytest.raises(ExchangeAPIError, match="Live trading is permanently locked"):
            adapter.create_order(symbol="BTCUSDT", side="BUY", order_type="LIMIT", quantity=1)

        with pytest.raises(ExchangeAPIError, match="Live trading is permanently locked"):
            adapter.cancel_order(symbol="BTCUSDT", order_id=123)

        with pytest.raises(ExchangeAPIError, match="Live trading is permanently locked"):
            adapter.cancel_all_open_orders(symbol="BTCUSDT")

    def test_testnet_signer_chain_id(self):
        signer = AsterV3Signer(is_testnet=True)
        assert signer.chain_id == 714
        typed_data = signer.build_typed_data("test_msg")
        assert typed_data["domain"]["chainId"] == 714

    def test_testnet_adapter_safety_guards(self):
        # 1. Automatic base_url switch and chain_id 714
        t_adapter = AsterDexV3Adapter(
            user_address=MOCK_USER_ADDR,
            signer_address=MOCK_SIGNER_ADDR,
            signer_private_key=MOCK_SIGNER_PK,
            is_testnet=True,
        )
        assert t_adapter.base_url == "https://fapi.asterdex-testnet.com"
        assert t_adapter.signer.chain_id == 714

        # 2. Prevent using Mainnet Chain ID 1666 on testnet profile
        with pytest.raises(ExchangeAPIError, match="cannot use mainnet Chain ID 1666"):
            AsterDexV3Adapter(
                base_url="https://fapi.asterdex-testnet.com",
                user_address=MOCK_USER_ADDR,
                signer_address=MOCK_SIGNER_ADDR,
                signer_private_key=MOCK_SIGNER_PK,
                chain_id=1666,
                is_testnet=True,
            )

    def test_mock_get_order_and_position_mode(self, adapter):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"orderId": 101, "status": "NEW", "symbol": "BTCUSDT"}

        with patch.object(adapter.session, "get", return_value=mock_resp) as mock_get:
            res_order = adapter.get_order(symbol="BTCUSDT", order_id=101)
            assert res_order["orderId"] == 101
            assert "/fapi/v3/order" in mock_get.call_args[0][0]

            res_open = adapter.get_open_order(symbol="BTCUSDT", client_order_id="SL_001")
            assert "/fapi/v3/openOrder" in mock_get.call_args[0][0]

            mock_resp.json.return_value = {"dualSidePosition": False}
            res_mode = adapter.get_position_mode()
            assert res_mode["dualSidePosition"] is False
            assert "/fapi/v3/positionSide/dual" in mock_get.call_args[0][0]

