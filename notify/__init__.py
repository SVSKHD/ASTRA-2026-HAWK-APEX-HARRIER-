# notify/__init__.py
from .discord import DiscordConfig, send_plain as discord_plain, send_price_update as discord_price
from .telegram import TelegramConfig, send_plain as telegram_plain, send_price_update as telegram_price

__all__ = [
    "DiscordConfig", "discord_plain", "discord_price",
    "TelegramConfig", "telegram_plain", "telegram_price",
]