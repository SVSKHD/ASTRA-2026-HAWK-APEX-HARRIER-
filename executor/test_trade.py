"""
Test suite for trade.py

Run from repo root:
    python -m executor.test_trade
    python -m executor.test_trade --test fok
    python -m executor.test_trade --test close
    python -m executor.test_trade --test sim
"""

from __future__ import annotations

import os
import time

from executor.trade import (
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

from notify.discord import init as init_discord, DiscordConfig
from notify.telegram import init as init_telegram, TelegramConfig


def init_notifiers() -> None:
    """
    Initialise Discord + Telegram from environment variables so trade.py
    can emit success/error notifications during tests.
    """
    try:
        discord_cfg = DiscordConfig(
            general=os.environ.get("DISCORD_WEBHOOK_GENERAL", ""),
            critical=os.environ.get("DISCORD_WEBHOOK_CRITICAL", ""),
            alerts=os.environ.get("DISCORD_WEBHOOK_ALERTS", ""),
            updates=os.environ.get("DISCORD_WEBHOOK_UPDATES", ""),
            errors=os.environ.get("DISCORD_WEBHOOK_ERRORS", ""),
        )
        init_discord(discord_cfg)
        print("✅ Discord initialised")
    except Exception as e:
        print(f"⚠️ Discord init failed: {e}")

    try:
        telegram_cfg = TelegramConfig(
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            general=os.environ.get("TELEGRAM_CHAT_GENERAL", ""),
            critical=os.environ.get("TELEGRAM_CHAT_CRITICAL", ""),
            alerts=os.environ.get("TELEGRAM_CHAT_ALERTS", ""),
            updates=os.environ.get("TELEGRAM_CHAT_UPDATES", ""),
            errors=os.environ.get("TELEGRAM_CHAT_ERRORS", ""),
        )
        init_telegram(telegram_cfg)
        print("✅ Telegram initialised")
    except Exception as e:
        print(f"⚠️ Telegram init failed: {e}")


def print_header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_result(result: dict):
    for k, v in result.items():
        print(f"  {k}: {v}")


def test_health_check():
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
    print_header("TEST: Profit Calculation (order_calc_profit)")

    tests = [
        ("XAUUSD", "buy",  0.1, 5000.00, 5015.00),
        ("XAUUSD", "sell", 0.1, 5015.00, 5000.00),
        ("XAUUSD", "buy",  0.1, 5015.00, 5000.00),
        ("XAUUSD", "sell", 0.1, 5000.00, 5015.00),
    ]

    for symbol, side, lot, open_p, close_p in tests:
        profit = calc_profit(symbol, side, lot, open_p, close_p)
        pips = calc_profit_pips(symbol, side, open_p, close_p)
        print(f"\n  {side.upper()} {symbol} {lot} lots @ {open_p} → {close_p}")
        print(f"    Profit: ${profit:.2f} ({pips:.0f} pips)")

    return True


def test_fok_order(symbol: str = "XAUUSD", lot: float = 0.01):
    print_header(f"TEST: FOK Order ({symbol})")
    print(f"\n⚠️  WARNING: This will place a REAL order!")
    print(f"    Symbol: {symbol}")
    print(f"    Lot: {lot}")

    confirm = input("\n  Type 'yes' to proceed: ")
    if confirm.lower() != "yes":
        print("  Skipped.")
        return False

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

        time.sleep(1)

        print(f"\n  Closing position {ticket}...")
        close_result = close_position_fok(ticket=ticket, comment="TEST_CLOSE")
        print_result(close_result)

        if close_result.get("success"):
            print(f"\n  ✅ Position closed, profit: ${close_result.get('profit', 0):.2f}")
            return True
        else:
            print(f"\n  ❌ Close failed: {close_result.get('error')}")
            return False

    retcode = result.get("retcode")
    error = str(result.get("error", ""))

    if retcode in (10016, 10018) or "market" in error.lower() or "closed" in error.lower():
        print(f"\n  ⚠️ Market is closed: retcode={retcode} error={error}")
        print("  This is expected outside trading hours.")
        return True

    print(f"\n  ❌ Order failed: retcode={retcode} error={error}")
    return False


def test_close_all():
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
    print_header("TEST: Simulator (order_calc_profit)")

    simulator.reset()

    print("\n  Opening simulated BUY XAUUSD 0.1 @ 5000...")
    r1 = simulator.open_position(
        symbol="XAUUSD",
        side="buy",
        lot=0.1,
        price=5000.0,
        comment="SIM_TEST",
    )
    print(f"  Ticket: {r1.get('ticket')}")

    floating = simulator.get_floating_pnl("XAUUSD", current_price=5015.0)
    print(f"\n  Floating P&L @ 5015: ${floating:.2f}")

    print("\n  Closing simulated position @ 5015...")
    r2 = simulator.close_position(r1["ticket"], close_price=5015.0)
    print(f"  Profit: ${r2.get('profit', 0):.2f}")

    realized = simulator.get_realized_pnl()
    print(f"\n  Total realized P&L: ${realized:.2f}")

    return True


def test_realized_pnl():
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

    init_notifiers()

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
        print("\nWaiting 5 seconds for notifier queues to flush...")
        time.sleep(5)
        shutdown()

    print(f"\n{'='*60}")
    print(f"  Results: {tests_passed} passed, {tests_failed} failed")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()