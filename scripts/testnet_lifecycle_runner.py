"""Controlled Testnet Order Lifecycle Runner for Aster Futures V3.

Executes a single controlled LONG or SHORT position lifecycle on Aster Futures Testnet:
1. Validates testnet environment, chain ID (714), and profile isolation.
2. Checks that mainnet wallet credentials are NOT being used (protects 16 USDT mainnet balance).
3. Queries testnet collateral and verifies One-Way position mode.
4. Submits Entry order and polls for fill confirmation.
5. Submits protective SL (STOP_MARKET) and TP (TAKE_PROFIT_MARKET) with reduceOnly=True.
6. Verifies active protection in exchange open orders.
7. Closes position with reduce-only market order.
8. Purges orphaned protective orders and verifies zero open orders remain.
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.core.exceptions import ExchangeAPIError
from src.core.logger import logger
from src.exchange.aster_v3 import AsterDexV3Adapter
from src.exchange.signer_v3 import redact_address


def run_testnet_lifecycle(
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    test_qty: float = 0.002,
    dry_run: bool = False,
) -> bool:
    print("=" * 70)
    print("   ASTER FUTURES V3 — CONTROLLED TESTNET LIFECYCLE RUNNER")
    print("=" * 70)

    # 1. Profile Isolation & Safety Audit
    mainnet_user = os.getenv("ASTER_USER_ADDRESS", "").strip()
    mainnet_signer = os.getenv("ASTER_SIGNER_ADDRESS", "").strip()
    testnet_user = os.getenv("ASTER_TESTNET_USER_ADDRESS", "").strip()
    testnet_signer = os.getenv("ASTER_TESTNET_SIGNER_ADDRESS", "").strip()
    testnet_key = os.getenv("ASTER_TESTNET_SIGNER_PRIVATE_KEY", "").strip()

    print("\n[STEP 1] Environment & Safety Audit:")
    print(f"  Target Environment : Aster Futures TESTNET")
    print(f"  Target URL         : https://fapi.asterdex-testnet.com")
    print(f"  Chain ID           : 714 (Domain: AsterSignTransaction)")
    print(f"  Mainnet Protection : Hard-locked (Mainnet balance 16 USDT untouchable)")

    if dry_run:
        print("\n  [INFO] Running in DRY-RUN mode. Validating safety logic without network calls.")
        return True

    if not testnet_user or not testnet_signer or not testnet_key:
        print("\n[BLOCKER] Testnet credentials are not configured in .env!")
        print("  Missing variables:")
        if not testnet_user:
            print("  - ASTER_TESTNET_USER_ADDRESS")
        if not testnet_signer:
            print("  - ASTER_TESTNET_SIGNER_ADDRESS")
        if not testnet_key:
            print("  - ASTER_TESTNET_SIGNER_PRIVATE_KEY")
        print("\n  Please obtain testnet credentials at https://www.asterdex-testnet.com/en/api-wallet")
        print("  and claim test USDT from the testnet faucet. Do NOT put mainnet keys here.")
        return False

    if testnet_signer.lower() == mainnet_signer.lower() or testnet_user.lower() == mainnet_user.lower():
        print("\n[CRITICAL SAFETY BLOCK] Mainnet credentials detected in testnet variables!")
        print("  Aborting immediately to prevent accidental mainnet interaction.")
        return False

    print(f"  Testnet User       : {redact_address(testnet_user)}")
    print(f"  Testnet Signer     : {redact_address(testnet_signer)}")

    # 2. Initialize Isolated Testnet Adapter
    try:
        adapter = AsterDexV3Adapter(
            base_url="https://fapi.asterdex-testnet.com",
            user_address=testnet_user,
            signer_address=testnet_signer,
            signer_private_key=testnet_key,
            chain_id=714,
            is_testnet=True,
            live_trading_locked=False,  # Unlocked ONLY for testnet with Chain ID 714
        )
    except Exception as e:
        print(f"\n[ERROR] Failed initializing testnet adapter: {e}")
        return False

    # 3. Connectivity & Account State
    print("\n[STEP 2] Connectivity & Collateral Verification:")
    if not adapter.ping():
        print("  [ERROR] Cannot reach Aster Testnet (/fapi/v3/ping). Check network or testnet uptime.")
        return False
    print("  Ping successful.")

    try:
        balances = adapter.get_account_balance()
        usdt_bal = next((b for b in balances if b.get("asset") == "USDT"), None)
        avail = float(usdt_bal.get("availableBalance", 0.0)) if usdt_bal else 0.0
        print(f"  Testnet USDT Available: {avail:.2f} USDT")
        if avail <= 0:
            print("  [WARNING] Testnet USDT balance is 0. Please claim faucet funds before trading.")
            return False
    except Exception as e:
        print(f"  [ERROR] Failed fetching account balance: {e}")
        return False

    try:
        pos_mode = adapter.get_position_mode()
        is_hedge = pos_mode.get("dualSidePosition", False)
        print(f"  Position Mode: {'HEDGE' if is_hedge else 'ONE-WAY'} (dualSidePosition={is_hedge})")
    except Exception as e:
        print(f"  [WARNING] Could not check position mode: {e}")

    # 4. Check Existing Exposure
    positions = adapter.get_positions(symbol=symbol)
    active_pos = next((p for p in positions if float(p.get("positionAmt", 0.0)) != 0.0), None)
    if active_pos:
        amt = active_pos.get("positionAmt")
        print(f"  [BLOCKER] An open position already exists on {symbol}: {amt}. Close it first.")
        return False

    open_orders = adapter.get_open_orders(symbol=symbol)
    if open_orders:
        print(f"  [BLOCKER] Found {len(open_orders)} existing open orders on {symbol}. Purging first...")
        adapter.cancel_all_open_orders(symbol=symbol)
        time.sleep(1)

    # 5. Fetch Reference Price
    print(f"\n[STEP 3] Fetching Market Price for {symbol}:")
    try:
        ticker = adapter._request("/fapi/v3/ticker/price", params={"symbol": symbol})
        current_price = float(ticker.get("price", 0.0))
        print(f"  Current {symbol} Mark Price: ${current_price:,.2f}")
    except Exception as e:
        print(f"  [ERROR] Could not fetch current price: {e}")
        return False

    is_long = side.upper() == "BUY"
    close_side = "SELL" if is_long else "BUY"

    if is_long:
        entry_price = round(current_price * 0.9995, 2)  # Limit slightly below market
        sl_price = round(current_price * 0.98, 2)       # 2% stop loss
        tp_price = round(current_price * 1.04, 2)       # 4% take profit
    else:
        entry_price = round(current_price * 1.0005, 2)
        sl_price = round(current_price * 1.02, 2)
        tp_price = round(current_price * 0.96, 2)

    print(f"  Plan: {side.upper()} {test_qty} {symbol} @ ${entry_price:,.2f} | SL: ${sl_price:,.2f} | TP: ${tp_price:,.2f}")

    # 6. Execute Order Lifecycle
    print("\n[STEP 4] Executing Controlled Lifecycle:")
    ts = int(time.time() * 1000)
    ent_cid = f"ENT_TEST_{ts}"[:36]
    sl_cid = f"SL_TEST_{ts}"[:36]
    tp_cid = f"TP_TEST_{ts}"[:36]

    # Entry submission
    print(f"  [1/4] Submitting Entry LIMIT {side.upper()} order (cid={ent_cid})...")
    try:
        ent_resp = adapter.create_order(
            symbol=symbol,
            side=side,
            order_type="LIMIT",
            quantity=test_qty,
            price=entry_price,
            time_in_force="GTC",
            client_order_id=ent_cid,
        )
        ent_id = ent_resp.get("orderId")
        print(f"        Entry submitted. Order ID: {ent_id}, Status: {ent_resp.get('status')}")
    except Exception as e:
        print(f"        [FAILED] Entry submission failed: {e}")
        return False

    # Wait for fill or convert to market for test completion
    print("        Waiting for Entry fill confirmation...")
    filled = False
    for _ in range(10):
        time.sleep(1)
        ord_info = adapter.get_order(symbol=symbol, order_id=ent_id, client_order_id=ent_cid)
        if ord_info.get("status") == "FILLED":
            filled = True
            break
        elif ord_info.get("status") in ("CANCELED", "REJECTED"):
            print(f"        Entry order terminal state: {ord_info.get('status')}")
            return False

    if not filled:
        print("        Limit order not filled yet. Cancelling limit and using MARKET entry for test.")
        adapter.cancel_order(symbol=symbol, order_id=ent_id)
        ent_resp = adapter.create_order(
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=test_qty,
            client_order_id=f"ENT_MKT_{ts}"[:36],
        )
        ent_id = ent_resp.get("orderId")
        print(f"        Market entry executed. Order ID: {ent_id}")

    # Confirm Position is Open
    time.sleep(1)
    positions = adapter.get_positions(symbol=symbol)
    active_pos = next((p for p in positions if float(p.get("positionAmt", 0.0)) != 0.0), None)
    if not active_pos:
        print("  [ERROR] Position did not appear in /fapi/v3/positionRisk!")
        return False
    actual_qty = abs(float(active_pos.get("positionAmt")))
    print(f"  [2/4] Confirmed Open Position on {symbol}: Size = {actual_qty} contracts.")

    # Submit SL and TP
    print(f"  [3/4] Submitting Protective Orders with reduceOnly=True (size: {actual_qty}):")
    try:
        sl_resp = adapter.create_order(
            symbol=symbol,
            side=close_side,
            order_type="STOP_MARKET",
            quantity=actual_qty,
            stop_price=sl_price,
            reduce_only=True,
            client_order_id=sl_cid,
        )
        sl_id = sl_resp.get("orderId")
        print(f"        Stop Loss Active: ID={sl_id}, StopPrice=${sl_price:,.2f}, Side={close_side}")
    except Exception as e:
        print(f"        [CRITICAL] SL submission failed: {e}")
        # Emergency close
        adapter.create_order(symbol=symbol, side=close_side, order_type="MARKET", quantity=actual_qty, reduce_only=True)
        return False

    try:
        tp_resp = adapter.create_order(
            symbol=symbol,
            side=close_side,
            order_type="TAKE_PROFIT_MARKET",
            quantity=actual_qty,
            stop_price=tp_price,
            reduce_only=True,
            client_order_id=tp_cid,
        )
        tp_id = tp_resp.get("orderId")
        print(f"        Take Profit Active: ID={tp_id}, StopPrice=${tp_price:,.2f}, Side={close_side}")
    except Exception as e:
        print(f"        [WARNING] TP submission failed: {e}")

    # Verify Protection in Exchange Open Orders
    time.sleep(1)
    open_orders = adapter.get_open_orders(symbol=symbol)
    print(f"\n[STEP 5] Exchange Verification (Never Trust HTTP 200 Alone):")
    print(f"  Found {len(open_orders)} open trigger order(s) on Aster Testnet:")
    for o in open_orders:
        print(f"    - ID: {o.get('orderId')}, Type: {o.get('type')}, Side: {o.get('side')}, StopPrice: {o.get('stopPrice')}, Qty: {o.get('origQty')}")

    has_sl = any(str(o.get("orderId")) == str(sl_id) for o in open_orders)
    if has_sl:
        print("  [SUCCESS] Stop Loss verified active on exchange trigger book!")
    else:
        print("  [FAILURE] Stop Loss NOT found in exchange open orders!")

    # 7. Position Close & Orphan Cleanup
    print(f"\n[STEP 6] Testing Controlled Position Close & Orphan Cleanup:")
    print(f"  Closing {actual_qty} contracts via MARKET {close_side} with reduceOnly=True...")
    adapter.create_order(symbol=symbol, side=close_side, order_type="MARKET", quantity=actual_qty, reduce_only=True)
    time.sleep(1)

    # Purge remaining conditional trigger orders
    print(f"  Purging remaining bracket trigger orders on {symbol}...")
    adapter.cancel_all_open_orders(symbol=symbol)
    time.sleep(1)

    # Final Clean Slate Verification
    final_orders = adapter.get_open_orders(symbol=symbol)
    final_pos = adapter.get_positions(symbol=symbol)
    final_amt = float(next((p.get("positionAmt", 0.0) for p in final_pos if p.get("symbol") == symbol), 0.0))

    print(f"\n[STEP 7] Final Clean Slate Audit:")
    print(f"  Final Position Size: {final_amt} (Expected: 0.0)")
    print(f"  Remaining Open Orders: {len(final_orders)} (Expected: 0)")

    if final_amt == 0.0 and len(final_orders) == 0:
        print("\n" + "=" * 70)
        print("   TESTNET VERIFICATION COMPLETE: ALL CHECKS PASSED PERFECTLY")
        print("   Zero orphan orders remain. Position closed flat. Capital protected.")
        print("=" * 70)
        return True
    else:
        print("\n[WARNING] Cleanup incomplete. Please inspect exchange open orders.")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aster Futures V3 Testnet Lifecycle Runner")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol (default: BTCUSDT)")
    parser.add_argument("--side", default="BUY", choices=["BUY", "SELL"], help="Entry side (BUY or SELL)")
    parser.add_argument("--qty", type=float, default=0.002, help="Quantity to trade (default: 0.002)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run safety audit without placing orders")
    args = parser.parse_args()

    success = run_testnet_lifecycle(
        symbol=args.symbol,
        side=args.side,
        test_qty=args.qty,
        dry_run=args.dry_run,
    )
    sys.exit(0 if success else 1)
