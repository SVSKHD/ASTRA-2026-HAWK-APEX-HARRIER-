# notify/__init__.py
from .discord import (
    DiscordConfig,
    DiscordClient,
    init as init_discord,
    notify_discord,
    get_client as get_discord_client,
    # Channel constants
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

__all__ = [
    # Discord
    "DiscordConfig",
    "DiscordClient",
    "init_discord",
    "notify_discord",
    "get_discord_client",

    # Telegram
    "TelegramConfig",
    "TelegramClient",
    "init_telegram",
    "notify_telegram",
    "get_telegram_client",

    # Channel constants (shared)
    "CHANNEL_GENERAL",
    "CHANNEL_CRITICAL",
    "CHANNEL_ALERTS",
    "CHANNEL_UPDATES",
    "CHANNEL_ERRORS",
]