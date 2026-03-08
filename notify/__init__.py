from __future__ import annotations

from typing import Optional

from .discord import (
    DiscordConfig,
    DiscordClient,
    init as init_discord,
    notify_discord,
    get_client as get_discord_client,
    CHANNEL_GENERAL,
    CHANNEL_CRITICAL,
    CHANNEL_ALERTS,
    CHANNEL_UPDATES,
    CHANNEL_ERRORS,
)

from .telegram import (
    TelegramConfig,
    TelegramClient,
    init as init_telegram,
    notify_telegram,
    get_client as get_telegram_client,
)


def _safe_broadcast(
    *,
    channel: str,
    message: str,
    discord_method: Optional[str] = None,
    telegram_method: Optional[str] = None,
    discord_kwargs: Optional[dict] = None,
    telegram_kwargs: Optional[dict] = None,
) -> None:
    """
    Reusable fire-and-forget notifier.
    Prefer rich client methods if provided, otherwise fall back to plain notify_*.
    Never raises into caller.
    """
    discord_kwargs = discord_kwargs or {}
    telegram_kwargs = telegram_kwargs or {}

    try:
        dc = get_discord_client()
        if discord_method and hasattr(dc, discord_method):
            getattr(dc, discord_method)(**discord_kwargs)
        else:
            notify_discord(channel, message)
    except Exception:
        try:
            notify_discord(channel, message)
        except Exception:
            pass

    try:
        tc = get_telegram_client()
        if telegram_method and hasattr(tc, telegram_method):
            getattr(tc, telegram_method)(**telegram_kwargs)
        else:
            notify_telegram(channel, message)
    except Exception:
        try:
            notify_telegram(channel, message)
        except Exception:
            pass


def notify_rollover(
    *,
    symbol: str,
    old_date: str,
    new_date: str,
    tick_utc: str,
    server_time: str,
    local_time: str,
) -> None:
    msg = (
        f"🔁 ROLLOVER — {symbol}\n"
        f"OLD MT5 DATE: {old_date}\n"
        f"NEW MT5 DATE: {new_date}\n"
        f"TICK UTC: {tick_utc}\n"
        f"SERVER: {server_time}\n"
        f"LOCAL: {local_time}"
    )
    _safe_broadcast(channel=CHANNEL_UPDATES, message=msg)


def notify_start_locked(
    *,
    symbol: str,
    date_mt5: str,
    price: float,
    tick_time_utc: str,
    server_time: str,
    local_time: str,
    source: str,
) -> None:
    msg = (
        f"✅ START LOCKED — {symbol}\n"
        f"DATE: {date_mt5}\n"
        f"PRICE: {price}\n"
        f"MT5: {tick_time_utc}\n"
        f"SERVER: {server_time}\n"
        f"LOCAL: {local_time}\n"
        f"SOURCE: {source}"
    )
    _safe_broadcast(channel=CHANNEL_ALERTS, message=msg)


def notify_price_heartbeat(
    *,
    symbol: str,
    current: float,
    start: Optional[float],
    high: Optional[float],
    low: Optional[float],
    stale_seconds: int,
    date_mt5: str,
    hhmm_mt5: str,
) -> None:
    state = "STALE" if stale_seconds > 0 else "LIVE"
    msg = (
        f"📈 PRICE UPDATE — {symbol}\n"
        f"MT5: {date_mt5} {hhmm_mt5}\n"
        f"CURRENT: {current}\n"
        f"START: {start}\n"
        f"HIGH: {high}\n"
        f"LOW: {low}\n"
        f"STATE: {state}\n"
        f"STALE_SECONDS: {stale_seconds}"
    )
    _safe_broadcast(channel=CHANNEL_UPDATES, message=msg)


__all__ = [
    "DiscordConfig",
    "DiscordClient",
    "init_discord",
    "notify_discord",
    "get_discord_client",
    "TelegramConfig",
    "TelegramClient",
    "init_telegram",
    "notify_telegram",
    "get_telegram_client",
    "CHANNEL_GENERAL",
    "CHANNEL_CRITICAL",
    "CHANNEL_ALERTS",
    "CHANNEL_UPDATES",
    "CHANNEL_ERRORS",
    "_safe_broadcast",
    "notify_rollover",
    "notify_start_locked",
    "notify_price_heartbeat",
]