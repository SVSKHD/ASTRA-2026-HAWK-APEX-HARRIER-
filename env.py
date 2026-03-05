# env.py
"""
Loads environment variables from a .env file and exposes typed config objects
for use across the entire astra-hawk-2026 system.

Usage:
    from env import Env

    Env.discord   → DiscordConfig
    Env.telegram  → TelegramConfig
    Env.pricing   → PriceSettings
    Env.mt5       → Mt5Config
"""
from __future__ import annotations

import os
from pathlib import Path
from datetime import timezone, timedelta


# ---------------------------------------------------------------------------
# .env loader (no third-party deps — pure stdlib)
# ---------------------------------------------------------------------------

def _load_env(path: str = ".env") -> None:
    """
    Parse KEY=VALUE pairs from a .env file into os.environ.
    - Strips surrounding quotes (" or ')
    - Ignores blank lines and lines starting with #
    - Does NOT override already-set environment variables
    """
    env_path = Path(path)
    if not env_path.exists():
        print(f"[env] ⚠️  No .env file found at '{path}' — using system environment only.")
        return

    with open(env_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _require(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"[env] ❌ Required environment variable '{key}' is not set.")
    return val


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _get_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _get_bool(key: str, default: bool) -> bool:
    return os.environ.get(key, str(default)).lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Mt5Config
# ---------------------------------------------------------------------------

class Mt5Config:
    def __init__(self):
        self.login    = _get("MT5_LOGIN")
        self.password = _get("MT5_PASSWORD")
        self.server   = _get("MT5_SERVER")

    def __repr__(self) -> str:
        return f"Mt5Config(login={self.login!r}, server={self.server!r})"


# ---------------------------------------------------------------------------
# Lazy imports for DiscordConfig / TelegramConfig / PriceSettings
# (avoids circular imports if env.py is imported early)
# ---------------------------------------------------------------------------

def _make_discord_config():
    from notify.discord import DiscordConfig
    return DiscordConfig(
        general  = _get("DISCORD_WEBHOOK_GENERAL"),
        critical = _get("DISCORD_WEBHOOK_CRITICAL"),
        alerts   = _get("DISCORD_WEBHOOK_ALERTS"),
        updates  = _get("DISCORD_WEBHOOK_UPDATES"),
        errors   = _get("DISCORD_WEBHOOK_ERRORS"),
    )


def _make_telegram_config():
    from notify.telegram import TelegramConfig
    return TelegramConfig(
        bot_token = _get("TELEGRAM_BOT_TOKEN"),
        general   = _get("TELEGRAM_CHAT_GENERAL"),
        critical  = _get("TELEGRAM_CHAT_CRITICAL"),
        alerts    = _get("TELEGRAM_CHAT_ALERTS"),
        updates   = _get("TELEGRAM_CHAT_UPDATES"),
        errors    = _get("TELEGRAM_CHAT_ERRORS"),
    )


def _make_price_settings():
    from settings import PriceSettings
    return PriceSettings(
        base_dir              = _get("BASE_DIR", "data"),
        poll_seconds          = _get_float("POLL_SECONDS", 0.3),
        status_print_seconds  = _get_float("STATUS_PRINT_SECONDS", 5.0),
        lock_hhmm_mt5         = _get("LOCK_HHMM_MT5", "00:00"),
        stale_after_seconds   = _get_int("STALE_AFTER_SECONDS", 20),
        pretty_json           = _get_bool("PRETTY_JSON", False),
        allow_bootstrap_lock  = True,
    )


# ---------------------------------------------------------------------------
# Env — single access point
# ---------------------------------------------------------------------------

class _Env:
    """
    Singleton config loader. Access via the module-level `Env` instance.

    On first attribute access, the .env file is loaded and configs built.
    """

    def __init__(self):
        self._loaded   = False
        self._discord  = None
        self._telegram = None
        self._pricing  = None
        self._mt5      = None

    def _ensure_loaded(self):
        if not self._loaded:
            _load_env()
            self._loaded = True

    @property
    def discord(self):
        self._ensure_loaded()
        if self._discord is None:
            self._discord = _make_discord_config()
        return self._discord

    @property
    def telegram(self):
        self._ensure_loaded()
        if self._telegram is None:
            self._telegram = _make_telegram_config()
        return self._telegram

    @property
    def pricing(self):
        self._ensure_loaded()
        if self._pricing is None:
            self._pricing = _make_price_settings()
        return self._pricing

    @property
    def mt5(self) -> Mt5Config:
        self._ensure_loaded()
        if self._mt5 is None:
            self._mt5 = Mt5Config()
        return self._mt5

    @property
    def symbols(self) -> list[str]:
        self._ensure_loaded()
        raw = _get("SYMBOLS", "XAUUSD")
        return [s.strip() for s in raw.split(",") if s.strip()]


Env = _Env()


# ---------------------------------------------------------------------------
# Quick validation — run directly to check your .env is complete
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== astra-hawk-2026 env check ===\n")

    _load_env()

    checks = {
        "MT5_LOGIN":                  _get("MT5_LOGIN"),
        "MT5_SERVER":                 _get("MT5_SERVER"),
        "DISCORD_WEBHOOK_GENERAL":    _get("DISCORD_WEBHOOK_GENERAL"),
        "DISCORD_WEBHOOK_CRITICAL":   _get("DISCORD_WEBHOOK_CRITICAL"),
        "DISCORD_WEBHOOK_ALERTS":     _get("DISCORD_WEBHOOK_ALERTS"),
        "DISCORD_WEBHOOK_UPDATES":    _get("DISCORD_WEBHOOK_UPDATES"),
        "DISCORD_WEBHOOK_ERRORS":     _get("DISCORD_WEBHOOK_ERRORS"),
        "TELEGRAM_BOT_TOKEN":         _get("TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_CHAT_GENERAL":      _get("TELEGRAM_CHAT_GENERAL"),
        "TELEGRAM_CHAT_CRITICAL":     _get("TELEGRAM_CHAT_CRITICAL"),
        "TELEGRAM_CHAT_ALERTS":       _get("TELEGRAM_CHAT_ALERTS"),
        "TELEGRAM_CHAT_UPDATES":      _get("TELEGRAM_CHAT_UPDATES"),
        "TELEGRAM_CHAT_ERRORS":       _get("TELEGRAM_CHAT_ERRORS"),
        "SYMBOLS":                    _get("SYMBOLS", "XAUUSD"),
    }

    all_ok = True
    for key, val in checks.items():
        status = "✅" if val else "❌ MISSING"
        preview = (val[:40] + "...") if val and len(val) > 40 else val
        print(f"  {status}  {key:<35} {preview}")
        if not val:
            all_ok = False

    print()
    if all_ok:
        print("✅ All variables set.")
    else:
        print("❌ Some variables are missing — fill them in .env")