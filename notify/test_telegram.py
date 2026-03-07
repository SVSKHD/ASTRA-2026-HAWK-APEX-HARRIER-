# test_telegram.py
"""
Test Telegram notifications.

Usage:
    python test_telegram.py --test all
    python test_telegram.py --test startup
    python test_telegram.py --test trade
    python test_telegram.py --test critical
    python test_telegram.py --test error
    python test_telegram.py --test plain

Setup:
    1. Create a bot via @BotFather → /newbot → copy token
    2. Add bot to your group/channel
    3. Get chat_id via @userinfobot or getUpdates API
    4. Set environment variables (see below)
"""

import os
import sys
import time
import argparse

# Set environment variables (replace with your actual values)
# You need to set these with real values for testing:
#
# export TELEGRAM_BOT_TOKEN="your_bot_token_here"
# export TELEGRAM_CHAT_GENERAL="-1001234567890"
# export TELEGRAM_CHAT_CRITICAL="-1001234567890"
# export TELEGRAM_CHAT_ALERTS="-1001234567890"
# export TELEGRAM_CHAT_UPDATES="-1001234567890"
# export TELEGRAM_CHAT_ERRORS="-1001234567890"

from notify.telegram import TelegramConfig, TelegramClient, init, notify_telegram, get_client


def get_config() -> TelegramConfig:
    """Get Telegram config from environment."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    if not bot_token:
        print("⚠️  TELEGRAM_BOT_TOKEN not set!")
        print("   Get one from @BotFather on Telegram")
        print("")

    return TelegramConfig(
        bot_token=bot_token,
        general=os.environ.get("TELEGRAM_CHAT_GENERAL", ""),
        critical=os.environ.get("TELEGRAM_CHAT_CRITICAL", ""),
        alerts=os.environ.get("TELEGRAM_CHAT_ALERTS", ""),
        updates=os.environ.get("TELEGRAM_CHAT_UPDATES", ""),
        errors=os.environ.get("TELEGRAM_CHAT_ERRORS", ""),
    )


def check_config():
    """Check if Telegram is configured."""
    cfg = get_config()

    print("\n=== Telegram Configuration ===")
    print(f"Bot Token: {'✅ Set' if cfg.bot_token else '❌ Missing'}")

    for channel in ["general", "critical", "alerts", "updates", "errors"]:
        chat_id = cfg.get_chat_id(channel)
        status = f"✅ {chat_id}" if chat_id else "❌ Missing"
        print(f"  {channel}: {status}")

    if not cfg.bot_token:
        print("\n❌ Cannot run tests without TELEGRAM_BOT_TOKEN")
        return False

    return True


def test_startup():
    """Test startup notification."""
    print("\n=== Testing STARTUP notification ===")

    cfg = get_config()
    client = TelegramClient(cfg)
    client.start()

    result = client.send_startup(["XAUUSD", "EURUSD", "GBPUSD"])
    print(f"send_startup: {'✅ queued' if result else '❌ failed'}")

    time.sleep(3)
    print(f"Queue depths: {client.queue_depth()}")
    print(f"Dropped: {client.dropped_count()}")


def test_shutdown():
    """Test shutdown notification."""
    print("\n=== Testing SHUTDOWN notification ===")

    cfg = get_config()
    client = TelegramClient(cfg)
    client.start()

    result = client.send_shutdown(reason="test shutdown")
    print(f"send_shutdown: {'✅ queued' if result else '❌ failed'}")

    time.sleep(3)


def test_trade_alerts():
    """Test trade alert notifications."""
    print("\n=== Testing TRADE ALERT notifications ===")

    cfg = get_config()
    client = TelegramClient(cfg)
    client.start()

    # Entry
    result = client.send_trade_alert(
        symbol="XAUUSD",
        action="ENTRY",
        direction="BUY",
        price=2650.50,
        lots=0.2,
        reason="astra_hawk threshold entry",
    )
    print(f"send_trade_alert (ENTRY): {'✅ queued' if result else '❌ failed'}")

    time.sleep(2)

    # Exit with profit
    result = client.send_trade_alert(
        symbol="XAUUSD",
        action="EXIT",
        direction="BUY",
        price=2665.75,
        lots=0.2,
        reason="threshold exit",
        profit=15.25,
    )
    print(f"send_trade_alert (EXIT): {'✅ queued' if result else '❌ failed'}")

    time.sleep(2)

    # SL Hit
    result = client.send_trade_alert(
        symbol="EURUSD",
        action="SL_HIT",
        direction="SELL",
        price=1.0850,
        lots=0.1,
        profit=-8.50,
    )
    print(f"send_trade_alert (SL_HIT): {'✅ queued' if result else '❌ failed'}")

    time.sleep(3)


def test_critical():
    """Test critical notifications."""
    print("\n=== Testing CRITICAL notifications ===")

    cfg = get_config()
    client = TelegramClient(cfg)
    client.start()

    # MT5 Disconnected
    result = client.send_mt5_disconnected(symbol="XAUUSD", stale_seconds=45)
    print(f"send_mt5_disconnected: {'✅ queued' if result else '❌ failed'}")

    time.sleep(2)

    # Custom critical
    result = client.send_critical(
        title="Catastrophic Loss",
        description="Daily loss limit reached! All trading stopped.\n\nTotal Loss: -$205.50\nLimit: -$200.00",
    )
    print(f"send_critical: {'✅ queued' if result else '❌ failed'}")

    time.sleep(3)


def test_updates():
    """Test update notifications."""
    print("\n=== Testing UPDATE notifications ===")

    cfg = get_config()
    client = TelegramClient(cfg)
    client.start()

    # Start price locked
    result = client.send_start_locked(
        symbol="XAUUSD",
        price=2645.30,
        date_mt5="2026-03-07",
        source="tick_lock_at_00:00",
        locked_server_time="2026-03-07T00:00:01+03:00",
        locked_local_time="2026-03-07T02:30:01+05:30",
    )
    print(f"send_start_locked: {'✅ queued' if result else '❌ failed'}")

    time.sleep(2)

    # Price update
    result = client.send_price_update(
        symbol="XAUUSD",
        mid=2650.50,
        bid=2650.40,
        ask=2650.60,
        start_price=2645.30,
        high=2658.75,
        low=2642.10,
        stale=False,
        date_mt5="2026-03-07",
        server_time="2026-03-07T14:35:22+03:00",
    )
    print(f"send_price_update: {'✅ queued' if result else '❌ failed'}")

    time.sleep(2)

    # Rollover
    result = client.send_rollover(
        symbol="XAUUSD",
        old_date="2026-03-06",
        new_date="2026-03-07",
        tick_utc="2026-03-07T00:00:01Z",
    )
    print(f"send_rollover: {'✅ queued' if result else '❌ failed'}")

    time.sleep(3)


def test_errors():
    """Test error notifications."""
    print("\n=== Testing ERROR notifications ===")

    cfg = get_config()
    client = TelegramClient(cfg)
    client.start()

    # Stale alert
    result = client.send_stale_alert(
        symbol="XAUUSD",
        stale_seconds=30,
        last_tick_utc="2026-03-07T14:30:00Z",
    )
    print(f"send_stale_alert: {'✅ queued' if result else '❌ failed'}")

    time.sleep(2)

    # Error
    result = client.send_error(
        symbol="EURUSD",
        error="build_price_packet returned None",
        context="tick_time_epoch=0, no valid price data",
        source="price_runner",
    )
    print(f"send_error: {'✅ queued' if result else '❌ failed'}")

    time.sleep(2)

    # Write failure
    result = client.send_write_failure(
        symbol="XAUUSD",
        path="data/price_assembly/XAUUSD.json",
        error="Permission denied",
    )
    print(f"send_write_failure: {'✅ queued' if result else '❌ failed'}")

    time.sleep(3)


def test_plain():
    """Test plain text notifications via module-level function."""
    print("\n=== Testing PLAIN TEXT notifications ===")

    cfg = get_config()
    init(cfg)

    # Test all channels
    channels = ["general", "critical", "alerts", "updates", "errors"]

    for channel in channels:
        result = notify_telegram(channel, f"🧪 Test message to <b>{channel}</b> channel")
        print(f"notify_telegram('{channel}'): {'✅ sent' if result else '❌ failed'}")
        time.sleep(1)

    time.sleep(3)


def test_all():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("  TELEGRAM NOTIFICATION TESTS")
    print("=" * 60)

    if not check_config():
        print("\n❌ Aborting: Telegram not configured")
        return

    test_startup()
    time.sleep(2)

    test_trade_alerts()
    time.sleep(2)

    test_updates()
    time.sleep(2)

    test_errors()
    time.sleep(2)

    test_critical()
    time.sleep(2)

    test_plain()
    time.sleep(2)

    test_shutdown()

    print("\n" + "=" * 60)
    print("  ALL TESTS COMPLETE")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Test Telegram notifications")
    parser.add_argument(
        "--test",
        choices=["all", "startup", "shutdown", "trade", "critical", "updates", "errors", "plain", "config"],
        default="all",
        help="Which test to run",
    )

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print(f"  Running test: {args.test}")
    print("=" * 60)

    if args.test == "config":
        check_config()
    elif args.test == "all":
        test_all()
    elif args.test == "startup":
        if check_config():
            test_startup()
    elif args.test == "shutdown":
        if check_config():
            test_shutdown()
    elif args.test == "trade":
        if check_config():
            test_trade_alerts()
    elif args.test == "critical":
        if check_config():
            test_critical()
    elif args.test == "updates":
        if check_config():
            test_updates()
    elif args.test == "errors":
        if check_config():
            test_errors()
    elif args.test == "plain":
        if check_config():
            test_plain()

    print("\n✅ Test complete! Check Telegram for messages.")


if __name__ == "__main__":
    main()