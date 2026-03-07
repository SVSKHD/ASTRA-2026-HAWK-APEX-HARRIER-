# main.py
"""
Astra-Hawk-2026 — Main Entry Point

Usage:
    # Monitor mode (default) — tracks P&L without real trades
    python main.py

    # Specific symbol
    python main.py --symbol XAUUSD

    # Active trading mode (requires is_trading_enabled=True in config)
    python main.py --mode active

    # Single run (one tick per symbol, then exit)
    python main.py --single

    # Status check
    python main.py --status

Environment Variables:
    LOG_LEVEL=DEBUG|INFO|WARNING|ERROR (default: INFO)
    LOG_DIR=logs (default: logs/)
    DISCORD_WEBHOOK_GENERAL=https://discord.com/api/webhooks/...
    DISCORD_WEBHOOK_CRITICAL=...
    DISCORD_WEBHOOK_ALERTS=...
    DISCORD_WEBHOOK_UPDATES=...
    DISCORD_WEBHOOK_ERRORS=...
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_GENERAL=...
    (etc.)
"""

from __future__ import annotations

import os
import sys
import argparse
import time
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Imports — ordered by dependency
# ---------------------------------------------------------------------------

# Config
from config.symbols import SYMBOLS, get_tradeable_symbols, get_enabled_symbols
from config.risk_lock import RISK_LOCK

# Core
from core.logger import get_logger, init_loggers, log_trade_open, log_trade_close

# Executor
from executor.executor import Executor
from executor.trade import health_check, shutdown as mt5_shutdown

# Notifications (optional)
try:
    from notify.discord import DiscordConfig, init as init_discord, notify_discord

    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False

try:
    from notify.telegram import TelegramConfig, init as init_telegram, notify_telegram

    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = get_logger("main")


# ---------------------------------------------------------------------------
# Discord Setup
# ---------------------------------------------------------------------------

def setup_discord() -> bool:
    """Initialize Discord notifications if configured."""
    if not HAS_DISCORD:
        logger.info("Discord module not available")
        return False

    cfg = DiscordConfig(
        general=os.environ.get("DISCORD_WEBHOOK_GENERAL", ""),
        critical=os.environ.get("DISCORD_WEBHOOK_CRITICAL", ""),
        alerts=os.environ.get("DISCORD_WEBHOOK_ALERTS", ""),
        updates=os.environ.get("DISCORD_WEBHOOK_UPDATES", ""),
        errors=os.environ.get("DISCORD_WEBHOOK_ERRORS", ""),
    )

    # Check if any webhook is configured
    if not any([cfg.webhooks.get(k) for k in cfg.webhooks]):
        logger.info("Discord webhooks not configured")
        return False

    init_discord(cfg)
    logger.info("✅ Discord notifications initialized")
    return True


# ---------------------------------------------------------------------------
# Telegram Setup
# ---------------------------------------------------------------------------

def setup_telegram() -> bool:
    """Initialize Telegram notifications if configured."""
    if not HAS_TELEGRAM:
        logger.info("Telegram module not available")
        return False

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        logger.info("Telegram bot token not configured")
        return False

    cfg = TelegramConfig(
        bot_token=bot_token,
        general=os.environ.get("TELEGRAM_CHAT_GENERAL", ""),
        critical=os.environ.get("TELEGRAM_CHAT_CRITICAL", ""),
        alerts=os.environ.get("TELEGRAM_CHAT_ALERTS", ""),
        updates=os.environ.get("TELEGRAM_CHAT_UPDATES", ""),
        errors=os.environ.get("TELEGRAM_CHAT_ERRORS", ""),
    )

    init_telegram(cfg)
    logger.info("✅ Telegram notifications initialized")
    return True


# ---------------------------------------------------------------------------
# Status Command
# ---------------------------------------------------------------------------

def print_status():
    """Print system status."""
    print("\n" + "=" * 60)
    print("  ASTRA-HAWK-2026 STATUS")
    print("=" * 60)

    # MT5 Health
    print("\n📡 MT5 Connection:")
    health = health_check()
    if health.get("connected"):
        print(f"   ✅ Connected")
        print(f"   Account: {health.get('account')}")
        print(f"   Balance: ${health.get('balance', 0):.2f}")
        print(f"   Equity:  ${health.get('equity', 0):.2f}")
    else:
        print(f"   ❌ Disconnected")

    # Symbols
    print("\n📊 Symbols:")
    enabled = get_enabled_symbols()
    tradeable = get_tradeable_symbols()
    print(f"   Enabled:   {enabled}")
    print(f"   Tradeable: {tradeable}")

    # Symbol Details
    print("\n📋 Symbol Configuration:")
    for name, sc in SYMBOLS.items():
        mode = "ACTIVE" if sc.is_tradeable else "MONITOR" if sc.is_enabled else "DISABLED"
        strategies = ", ".join(sc.strategies) if sc.strategies else "none"
        print(f"   {name}: {mode} | strategies: [{strategies}]")

    # Risk Lock
    print("\n🔒 Global Risk Lock:")
    print(f"   Daily Loss Limit:    ${RISK_LOCK.daily_loss_limit_usd:.2f}")
    print(f"   Catastrophic Loss:   ${RISK_LOCK.catastrophic_loss_usd:.2f}")
    print(f"   Daily Profit Target: ${RISK_LOCK.daily_profit_target_usd:.2f}")
    print(f"   Min Profit Lock:     {'Enabled' if RISK_LOCK.min_profit.enabled else 'Disabled'}")
    if RISK_LOCK.min_profit.enabled:
        print(f"      Trigger:  ${RISK_LOCK.min_profit.trigger_usd:.2f}")
        print(f"      Lock at:  ${RISK_LOCK.min_profit.min_lock_usd:.2f}")

    # Notifications
    print("\n📣 Notifications:")
    print(f"   Discord:  {'✅ Available' if HAS_DISCORD else '❌ Not available'}")
    print(f"   Telegram: {'✅ Available' if HAS_TELEGRAM else '❌ Not available'}")

    print("\n" + "=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Astra-Hawk-2026 Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py                    # Run in monitor mode
    python main.py --symbol XAUUSD    # Run specific symbol
    python main.py --single           # Single run, then exit
    python main.py --status           # Print system status
    python main.py --interval 0.5     # Custom tick interval
        """
    )

    parser.add_argument(
        "--symbol", "-s",
        type=str,
        help="Specific symbol to run (default: all enabled)",
    )

    parser.add_argument(
        "--mode", "-m",
        choices=["monitor", "active"],
        default="monitor",
        help="Trading mode (default: monitor)",
    )

    parser.add_argument(
        "--single",
        action="store_true",
        help="Run once and exit (don't loop)",
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Print system status and exit",
    )

    parser.add_argument(
        "--interval", "-i",
        type=float,
        default=0.3,
        help="Tick interval in seconds (default: 0.3)",
    )

    parser.add_argument(
        "--base-dir", "-d",
        type=str,
        default="data",
        help="Base directory for data files (default: data)",
    )

    args = parser.parse_args()

    # Status command
    if args.status:
        print_status()
        return 0

    # Initialize logging
    init_loggers()
    logger.info("=" * 50)
    logger.info("  ASTRA-HAWK-2026 STARTING")
    logger.info("=" * 50)

    # Initialize notifications
    discord_ok = setup_discord()
    telegram_ok = setup_telegram()

    # Determine symbols to run
    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = get_enabled_symbols()

    if not symbols:
        logger.error("No symbols enabled! Check config/symbols.py")
        return 1

    logger.info(f"Symbols: {symbols}")
    logger.info(f"Mode: {args.mode.upper()}")
    logger.info(f"Interval: {args.interval}s")

    # Send startup notification
    if discord_ok:
        notify_discord("general", f"🚀 Astra-Hawk starting | Symbols: {symbols} | Mode: {args.mode.upper()}")
    if telegram_ok:
        notify_telegram("general", f"🚀 Astra-Hawk starting | Symbols: {symbols} | Mode: {args.mode.upper()}")

    # Create executor
    executor = Executor(base_dir=args.base_dir)

    try:
        if args.single:
            # Single run
            logger.info("Running single tick...")
            results = executor.run_single(symbols=symbols)

            for r in results:
                if r.action not in ("waiting", "none"):
                    logger.info(f"[{r.symbol}:{r.strategy}] {r.decision} → {r.action}")

            logger.info(f"Single run complete. {len(results)} results.")
        else:
            # Continuous loop
            logger.info(f"Starting run loop (interval={args.interval}s)...")
            logger.info("Press Ctrl+C to stop")

            executor.run_loop(
                interval=args.interval,
                symbols=symbols,
            )

    except KeyboardInterrupt:
        logger.info("\n🛑 Shutdown requested...")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)

        if discord_ok:
            notify_discord("critical", f"🚨 Astra-Hawk crashed: {e}")
        if telegram_ok:
            notify_telegram("critical", f"🚨 Astra-Hawk crashed: {e}")

        return 1

    finally:
        # Cleanup
        logger.info("Shutting down executor...")
        executor.shutdown()

        # Final notification
        if discord_ok:
            notify_discord("general", "🛑 Astra-Hawk stopped")
        if telegram_ok:
            notify_telegram("general", "🛑 Astra-Hawk stopped")

        logger.info("✅ Shutdown complete")

    return 0


if __name__ == "__main__":
    sys.exit(main())