# test_trade.py
"""
Test suite for trade.py

Run with MT5 terminal open:
    python test_trade.py

Run specific test:
    python test_trade.py --test fok
    python test_trade.py --test close
    python test_trade.py --test sim
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

# Import trade module
from trade import (
    place_market_order_fok,
    close_position_fok,
    close_all_positions_fok,
    get_positions_snapshot,
    get_realized_profit_since,
    calc_profit,
    calc_profit_pips,
    simulator,
    health_check,
    shutdown,
)


def print_header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_result(result: dict):
    for k, v in result.items():
        print(f"  {k}: {v}")


def test_health_check():
    """Test MT5 connection health check."""
    print_header("TEST: Health Check")

    result = health_check()
    print_result(result)

    if result.get("connected"):
        print("\n✅ MT5 connected")
        return True
    else:
        print("\n❌ MT5 not connected")
        return False


def test_positions_snapshot():
    """Test getting positions snapshot."""
    print_header("TEST: Positions Snapshot")

    snap = get_positions_snapshot()
    print(f"  Total P&L: ${snap['total_profit_usd']:.2f}")
    print(f"  Position count: {snap['count']}")

    for pos in snap.get("positions", []):
        print(f"\n  Ticket #{pos['ticket']}:")
        print(f"    {pos['type'].upper()} {pos['symbol']} {pos['volume']} lots")
        print(f"    Open: {pos['price_open']} → Current: {pos['price_current']}")
        print(f"    Profit: ${pos['profit']:.2f}")

    return True


def test_calc_profit():
    """Test profit calculation."""
    print_header("TEST: Profit Calculation (order_calc_profit)")

    # Test cases
    tests = [
        ("XAUUSD", "buy",  0.1, 5000.00, 5015.00),  # +15 price = +$150
        ("XAUUSD", "sell", 0.1, 5015.00, 5000.00),  # -15 price = +$150
        ("XAUUSD", "buy",  0.1, 5015.00, 5000.00),  # -15 price = -$150
        ("XAUUSD", "sell", 0.1, 5000.00, 5015.00),  # +15 price = -$150
    ]

    for symbol, side, lot, open_p, close_p in tests:
        profit = calc_profit(symbol, side, lot, open_p, close_p)
        pips = calc_profit_pips(symbol, side, open_p, close_p)
        print(f"\n  {side.upper()} {symbol} {lot} lots @ {open_p} → {close_p}")
        print(f"    Profit: ${profit:.2f} ({pips:.0f} pips)")

    return True


def test_fok_order(symbol: str = "XAUUSD", lot: float = 0.01):
    """Test FOK order placement (LIVE - will open real position!)."""
    print_header(f"TEST: FOK Order ({symbol})")
    print(f"\n⚠️  WARNING: This will place a REAL order!")
    print(f"    Symbol: {symbol}")
    print(f"    Lot: {lot}")

    confirm = input("\n  Type 'yes' to proceed: ")
    if confirm.lower() != "yes":
        print("  Skipped.")
        return False

    # Place BUY order
    print("\n  Placing BUY order...")
    result = place_market_order_fok(
        symbol=symbol,
        side="buy",
        lot=lot,
        comment="TEST_FOK_BUY",
    )
    print_result(result)

    if result.get("success"):
        ticket = result.get("ticket")
        print(f"\n  ✅ Order placed: ticket={ticket}")

        # Wait a moment
        time.sleep(1)

        # Close it
        print(f"\n  Closing position {ticket}...")
        close_result = close_position_fok(ticket=ticket, comment="TEST_CLOSE")
        print_result(close_result)

        if close_result.get("success"):
            print(f"\n  ✅ Position closed, profit: ${close_result.get('profit', 0):.2f}")
            return True
        else:
            print(f"\n  ❌ Close failed: {close_result.get('error')}")
            return False
    else:
        print(f"\n  ❌ Order failed: {result.get('error')}")
        return False


def test_close_all():
    """Test closing all positions."""
    print_header("TEST: Close All Positions")

    snap = get_positions_snapshot()
    if snap["count"] == 0:
        print("  No positions to close.")
        return True

    print(f"  Found {snap['count']} position(s)")
    confirm = input("  Type 'yes' to close all: ")
    if confirm.lower() != "yes":
        print("  Skipped.")
        return False

    result = close_all_positions_fok(comment="TEST_CLOSE_ALL")
    print_result(result)

    return result.get("closed", False)


def test_simulator():
    """Test simulated trading."""
    print_header("TEST: Simulator (order_calc_profit)")

    simulator.reset()

    # Open simulated BUY
    print("\n  Opening simulated BUY XAUUSD 0.1 @ 5000...")
    r1 = simulator.open_position(
        symbol="XAUUSD",
        side="buy",
        lot=0.1,
        price=5000.0,
        comment="SIM_TEST",
    )
    print(f"  Ticket: {r1.get('ticket')}")

    # Check floating P&L
    floating = simulator.get_floating_pnl("XAUUSD", current_price=5015.0)
    print(f"\n  Floating P&L @ 5015: ${floating:.2f}")

    # Close it
    print("\n  Closing simulated position @ 5015...")
    r2 = simulator.close_position(r1["ticket"], close_price=5015.0)
    print(f"  Profit: ${r2.get('profit', 0):.2f}")

    # Check realized
    realized = simulator.get_realized_pnl()
    print(f"\n  Total realized P&L: ${realized:.2f}")

    return True


def test_realized_pnl():
    """Test getting realized P&L since today."""
    print_header("TEST: Realized P&L Since Today")

    pnl = get_realized_profit_since()
    print(f"  Realized P&L today: ${pnl:.2f}")

    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Test trade.py")
    parser.add_argument(
        "--test",
        choices=["health", "snap", "calc", "fok", "close", "sim", "realized", "all"],
        default="health",
        help="Which test to run",
    )
    parser.add_argument("--symbol", default="XAUUSD", help="Symbol for FOK test")
    parser.add_argument("--lot", type=float, default=0.01, help="Lot size for FOK test")

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  TRADE.PY TEST SUITE")
    print("=" * 60)

    tests_passed = 0
    tests_failed = 0

    try:
        if args.test in ("health", "all"):
            if test_health_check():
                tests_passed += 1
            else:
                tests_failed += 1
                if args.test != "all":
                    print("\n⚠️  MT5 not connected. Exiting.")
                    return

        if args.test in ("snap", "all"):
            if test_positions_snapshot():
                tests_passed += 1
            else:
                tests_failed += 1

        if args.test in ("calc", "all"):
            if test_calc_profit():
                tests_passed += 1
            else:
                tests_failed += 1

        if args.test in ("sim", "all"):
            if test_simulator():
                tests_passed += 1
            else:
                tests_failed += 1

        if args.test in ("realized", "all"):
            if test_realized_pnl():
                tests_passed += 1
            else:
                tests_failed += 1

        if args.test == "fok":
            if test_fok_order(symbol=args.symbol, lot=args.lot):
                tests_passed += 1
            else:
                tests_failed += 1

        if args.test == "close":
            if test_close_all():
                tests_passed += 1
            else:
                tests_failed += 1

    except KeyboardInterrupt:
        print("\n\n  Interrupted by user")
    except Exception as e:
        print(f"\n\n  ❌ Exception: {e}")
        tests_failed += 1
    finally:
        shutdown()

    print(f"\n{'='*60}")
    print(f"  Results: {tests_passed} passed, {tests_failed} failed")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()