# notify/test_discord.py
from __future__ import annotations

import os
import time
from dotenv import load_dotenv

from discord import DiscordConfig, DiscordClient, notify_discord, init, get_client


# ---------------------------------------------------------------------
# Load .env from project root
# ---------------------------------------------------------------------

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")

load_dotenv(dotenv_path=_ENV_PATH, override=False)
load_dotenv(override=False)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def make_client() -> DiscordClient:
    cfg = DiscordConfig(
        general=os.environ.get("DISCORD_WEBHOOK_GENERAL", ""),
        critical=os.environ.get("DISCORD_WEBHOOK_CRITICAL", ""),
        alerts=os.environ.get("DISCORD_WEBHOOK_ALERTS", ""),
        updates=os.environ.get("DISCORD_WEBHOOK_UPDATES", ""),
        errors=os.environ.get("DISCORD_WEBHOOK_ERRORS", ""),
    )
    client = DiscordClient(cfg)
    client.start()
    return client


def print_state(client: DiscordClient, label: str) -> None:
    print(f"\n--- {label} ---")
    print("Queue depths :", client.queue_depth())
    print("Dropped      :", client.dropped_count())


def wait_and_report(client: DiscordClient, seconds: int = 5) -> None:
    print(f"Waiting {seconds}s for background senders...")
    time.sleep(seconds)
    print_state(client, f"after {seconds}s")


# ---------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------

def test_startup(client: DiscordClient) -> None:
    print("\n=== TEST: STARTUP ===")
    ok = client.send_startup(["XAUUSD", "EURUSD"])
    print("send_startup:", "✅ queued" if ok else "❌ failed")
    print_state(client, "startup queued")
    wait_and_report(client)


def test_shutdown(client: DiscordClient) -> None:
    print("\n=== TEST: SHUTDOWN ===")
    ok = client.send_shutdown("manual test")
    print("send_shutdown:", "✅ queued" if ok else "❌ failed")
    print_state(client, "shutdown queued")
    wait_and_report(client)


def test_rollover(client: DiscordClient) -> None:
    print("\n=== TEST: ROLLOVER ===")
    ok = client.send_rollover(
        symbol="XAUUSD",
        old_date="2026-03-06",
        new_date="2026-03-07",
        tick_utc="2026-03-07T00:00:03Z",
    )
    print("send_rollover:", "✅ queued" if ok else "❌ failed")
    print_state(client, "rollover queued")
    wait_and_report(client)


def test_critical(client: DiscordClient) -> None:
    print("\n=== TEST: CRITICAL ===")
    ok1 = client.send_critical(
        title="Manual Critical Test",
        description="Testing critical channel delivery.",
        fields=[
            {"name": "Reason", "value": "`manual_test`", "inline": True},
            {"name": "UTC", "value": "`2026-03-07T12:00:00Z`", "inline": True},
        ],
    )
    print("send_critical:", "✅ queued" if ok1 else "❌ failed")

    ok2 = client.send_mt5_disconnected("XAUUSD", 45)
    print("send_mt5_disconnected:", "✅ queued" if ok2 else "❌ failed")

    print_state(client, "critical queued")
    wait_and_report(client)


def test_alerts(client: DiscordClient) -> None:
    print("\n=== TEST: ALERTS ===")
    ok1 = client.send_trade_alert(
        symbol="XAUUSD",
        action="ENTRY",
        direction="BUY",
        price=5129.24,
        lots=0.10,
        reason="strategy-chip-A",
    )
    print("send_trade_alert ENTRY:", "✅ queued" if ok1 else "❌ failed")

    ok2 = client.send_trade_alert(
        symbol="XAUUSD",
        action="EXIT",
        direction="BUY",
        price=5138.80,
        lots=0.10,
        reason="target_hit",
        profit=96.45,
        ticket=123456,
    )
    print("send_trade_alert EXIT:", "✅ queued" if ok2 else "❌ failed")

    ok3 = client.send_trade_alert(
        symbol="XAUUSD",
        action="SL_HIT",
        direction="SELL",
        price=5108.10,
        lots=0.10,
        reason="stop_loss",
        profit=-44.20,
        ticket=123457,
    )
    print("send_trade_alert SL_HIT:", "✅ queued" if ok3 else "❌ failed")

    print_state(client, "alerts queued")
    wait_and_report(client)


def test_updates(client: DiscordClient) -> None:
    print("\n=== TEST: UPDATES ===")
    ok1 = client.send_start_locked(
        symbol="XAUUSD",
        price=5140.73,
        date_mt5="2026-03-07",
        source="tick_lock_existing_dayfile_at_or_after_00:00",
        locked_server_time="2026-03-07T03:00:03+03:00",
        locked_local_time="2026-03-07T05:30:03+05:30",
    )
    print("send_start_locked:", "✅ queued" if ok1 else "❌ failed")

    ok2 = client.send_price_update(
        symbol="XAUUSD",
        mid=5148.25,
        bid=5147.90,
        ask=5148.60,
        start_price=5140.73,
        high=5155.20,
        low=5134.10,
        stale=False,
        date_mt5="2026-03-07",
        server_time="2026-03-07T12:15:09+03:00",
    )
    print("send_price_update:", "✅ queued" if ok2 else "❌ failed")

    print_state(client, "updates queued")
    wait_and_report(client)


def test_errors(client: DiscordClient) -> None:
    print("\n=== TEST: ERRORS ===")
    ok1 = client.send_stale_alert(
        symbol="EURUSD",
        stale_seconds=31,
        last_tick_utc="2026-03-07T08:10:15Z",
    )
    print("send_stale_alert:", "✅ queued" if ok1 else "❌ failed")

    ok2 = client.send_error(
        symbol="EURUSD",
        error="build_price_packet returned None",
        context="tick missing during packet assembly",
        source="price_runner",
    )
    print("send_error:", "✅ queued" if ok2 else "❌ failed")

    ok3 = client.send_write_failure(
        symbol="XAUUSD",
        path="data/price_assembly/XAUUSD.json",
        error="PermissionError: file locked by another process",
    )
    print("send_write_failure:", "✅ queued" if ok3 else "❌ failed")

    print_state(client, "errors queued")
    wait_and_report(client)


def test_plain_messages_with_singleton() -> None:
    print("\n=== TEST: PLAIN TEXT VIA MODULE SINGLETON ===")
    cfg = DiscordConfig(
        general=os.environ.get("DISCORD_WEBHOOK_GENERAL", ""),
        critical=os.environ.get("DISCORD_WEBHOOK_CRITICAL", ""),
        alerts=os.environ.get("DISCORD_WEBHOOK_ALERTS", ""),
        updates=os.environ.get("DISCORD_WEBHOOK_UPDATES", ""),
        errors=os.environ.get("DISCORD_WEBHOOK_ERRORS", ""),
    )
    init(cfg)

    ok1 = notify_discord("general", "✅ Plain message test — general")
    print("notify_discord('general'):", "✅ queued" if ok1 else "❌ failed")

    ok2 = notify_discord("critical", "🚨 Plain message test — critical")
    print("notify_discord('critical'):", "✅ queued" if ok2 else "❌ failed")

    ok3 = notify_discord("alerts", "📥 Plain message test — alerts")
    print("notify_discord('alerts'):", "✅ queued" if ok3 else "❌ failed")

    ok4 = notify_discord("updates", "📊 Plain message test — updates")
    print("notify_discord('updates'):", "✅ queued" if ok4 else "❌ failed")

    ok5 = notify_discord("errors", "❌ Plain message test — errors")
    print("notify_discord('errors'):", "✅ queued" if ok5 else "❌ failed")

    client = get_client()
    print_state(client, "plain-text queued")
    wait_and_report(client)


# ---------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("DISCORD NOTIFIER TEST SUITE")
    print("=" * 70)

    client = make_client()

    test_startup(client)
    test_alerts(client)
    test_updates(client)
    test_errors(client)
    test_critical(client)
    test_rollover(client)
    test_shutdown(client)

    # singleton / module-level API test separately
    test_plain_messages_with_singleton()

    print("\n" + "=" * 70)
    print("ALL TESTS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()