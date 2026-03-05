# notify/discord.py
from __future__ import annotations

import json
import time
import threading
import queue
import urllib.request
import urllib.error
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Discord rate limit facts (webhook tier):
#   - 30 requests / 60 seconds per webhook URL
#   - HTTP 429 response includes Retry-After header (float, seconds)
#   - Safe sustained rate: 1 msg / 2s per webhook = 30/min
#
# Strategy:
#   - One background daemon thread per channel (webhook URL)
#   - Each thread owns a token bucket: capacity=28, refill 1 token per 2s
#   - Queue is unbounded — messages are never dropped, only delayed
#   - On HTTP 429: read Retry-After, sleep exactly that long, retry same msg
#   - On other HTTP errors: exponential backoff, up to MAX_RETRIES
# ---------------------------------------------------------------------------

CHANNEL_GENERAL  = "general"
CHANNEL_CRITICAL = "critical"
CHANNEL_ALERTS   = "alerts"
CHANNEL_UPDATES  = "updates"
CHANNEL_ERRORS   = "errors"

ALL_CHANNELS = (CHANNEL_GENERAL, CHANNEL_CRITICAL, CHANNEL_ALERTS, CHANNEL_UPDATES, CHANNEL_ERRORS)

_BUCKET_CAPACITY  = 28      # stay 2 under Discord's hard limit of 30
_REFILL_RATE      = 2.0     # seconds per token
_MAX_RETRIES      = 5
_RETRY_BASE_SLEEP = 1.0     # doubles each retry
_SEND_TIMEOUT     = 6.0


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class DiscordConfig:
    """
    Webhook URLs for each of the 5 named channels.
    Always pass via environment variables — never hardcode.

    Example:
        import os
        cfg = DiscordConfig(
            general  = os.environ["DISCORD_WEBHOOK_GENERAL"],
            critical = os.environ["DISCORD_WEBHOOK_CRITICAL"],
            alerts   = os.environ["DISCORD_WEBHOOK_ALERTS"],
            updates  = os.environ["DISCORD_WEBHOOK_UPDATES"],
            errors   = os.environ["DISCORD_WEBHOOK_ERRORS"],
        )

    Channel routing:
        general   — lifecycle events: startup, shutdown, day rollover
        critical  — needs immediate human attention: MT5 disconnect, runaway loss
        alerts    — trade events: entry, exit, hedge, SL/TP hit
        updates   — periodic: price snapshots, start-price lock, H/L
        errors    — exceptions, write failures, stale ticks
    """
    def __init__(
        self,
        general:  str = "",
        critical: str = "",
        alerts:   str = "",
        updates:  str = "",
        errors:   str = "",
    ):
        self.webhooks: Dict[str, str] = {
            CHANNEL_GENERAL:  general,
            CHANNEL_CRITICAL: critical,
            CHANNEL_ALERTS:   alerts,
            CHANNEL_UPDATES:  updates,
            CHANNEL_ERRORS:   errors,
        }

    def get_url(self, channel: str) -> str:
        return self.webhooks.get(channel, "")


# ---------------------------------------------------------------------------
# Token bucket — owned exclusively by one sender thread, not thread-safe
# ---------------------------------------------------------------------------

class _TokenBucket:
    def __init__(self, capacity: int, refill_rate_seconds: float):
        self._capacity    = capacity
        self._tokens      = float(capacity)
        self._rate        = refill_rate_seconds
        self._last_refill = time.monotonic()

    def consume(self) -> None:
        """Block until a token is available, then consume one."""
        while True:
            now     = time.monotonic()
            elapsed = now - self._last_refill
            gained  = elapsed / self._rate
            if gained > 0:
                self._tokens      = min(self._capacity, self._tokens + gained)
                self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            sleep_for = (1.0 - self._tokens) * self._rate
            time.sleep(max(0.05, sleep_for))


# ---------------------------------------------------------------------------
# Queue item
# ---------------------------------------------------------------------------

@dataclass
class _QueueItem:
    url:     str
    payload: Dict[str, Any]
    channel: str


# ---------------------------------------------------------------------------
# Per-channel background sender thread
# ---------------------------------------------------------------------------

class _ChannelSender(threading.Thread):
    def __init__(self, channel: str):
        super().__init__(name=f"discord-{channel}", daemon=True)
        self.channel  = channel
        self._q       = queue.Queue()           # type: queue.Queue[_QueueItem]
        self._bucket  = _TokenBucket(_BUCKET_CAPACITY, _REFILL_RATE)
        self.dropped  = 0

    def enqueue(self, item: _QueueItem) -> None:
        self._q.put(item)

    def qsize(self) -> int:
        return self._q.qsize()

    def run(self) -> None:
        while True:
            item = self._q.get()
            self._send_with_retry(item)
            self._q.task_done()

    def _send_with_retry(self, item: _QueueItem) -> None:
        retries = 0
        while True:
            self._bucket.consume()  # rate gate

            success, retry_after = _post_webhook(item.url, item.payload)

            if success:
                return

            if retry_after is not None:
                # 429 — Discord told us exactly how long to wait
                wait = retry_after + 0.25
                print(f"[discord:{self.channel}] ⏳ 429 rate limited — waiting {wait:.1f}s")
                time.sleep(wait)
                continue   # retry without counting against MAX_RETRIES

            retries += 1
            if retries > _MAX_RETRIES:
                print(f"[discord:{self.channel}] ❌ dropped after {_MAX_RETRIES} retries")
                self.dropped += 1
                return

            sleep = _RETRY_BASE_SLEEP * (2 ** (retries - 1))
            print(f"[discord:{self.channel}] ⚠️ retry {retries}/{_MAX_RETRIES} in {sleep:.1f}s")
            time.sleep(sleep)


# ---------------------------------------------------------------------------
# Raw HTTP POST — returns (success, retry_after | None)
# ---------------------------------------------------------------------------

def _post_webhook(url: str, payload: Dict[str, Any]) -> Tuple[bool, Optional[float]]:
    try:
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_SEND_TIMEOUT) as resp:
            return 200 <= resp.status < 300, None

    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after: Optional[float] = 2.0
            try:
                body        = json.loads(e.read().decode("utf-8"))
                retry_after = float(body.get("retry_after", 2.0))
            except Exception:
                pass
            return False, retry_after
        print(f"[discord] ❌ HTTP {e.code}: {e.reason}")
        return False, None
    except Exception as e:
        print(f"[discord] ❌ post failed: {e!r}")
        return False, None


# ---------------------------------------------------------------------------
# DiscordClient — the only object callers interact with
# ---------------------------------------------------------------------------

class DiscordClient:
    """
    Thread-safe Discord notifier with per-channel rate-limited send queues.

    Quick start:
        cfg    = DiscordConfig(general=..., critical=..., ...)
        client = DiscordClient(cfg)
        client.start()          # call once at app startup

        client.send_startup(["XAUUSD", "EURUSD"])
        client.send_start_locked("XAUUSD", price=5140.73, ...)
        client.send_trade_alert("XAUUSD", action="ENTRY", ...)
        client.send_error("XAUUSD", error="build_price_packet None")

    All send_* calls return immediately — messages are queued and sent
    in the background, rate-limited, and retried automatically on failure.
    """

    def __init__(self, cfg: DiscordConfig):
        self._cfg     = cfg
        self._senders = {ch: _ChannelSender(ch) for ch in ALL_CHANNELS}
        self._started = False

    def start(self) -> None:
        """Start all background sender threads. Call once at app startup."""
        if self._started:
            return
        for s in self._senders.values():
            s.start()
        self._started = True
        print(f"[discord] ✅ senders started — channels: {list(self._senders)}")

    def queue_depth(self) -> Dict[str, int]:
        """Current pending message count per channel. Use for health monitoring."""
        return {ch: s.qsize() for ch, s in self._senders.items()}

    def dropped_count(self) -> Dict[str, int]:
        """Messages permanently dropped after MAX_RETRIES exhausted."""
        return {ch: s.dropped for ch, s in self._senders.items()}

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _enqueue(self, channel: str, payload: Dict[str, Any]) -> bool:
        url = self._cfg.get_url(channel)
        if not url:
            print(f"[discord] ⚠️ no webhook for channel='{channel}'")
            return False
        if not self._started:
            print(f"[discord] ⚠️ call client.start() before sending")
            return False
        self._senders[channel].enqueue(_QueueItem(url=url, payload=payload, channel=channel))
        return True

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _embed(
        title: str,
        description: str,
        color: int,
        fields: Optional[List[Dict]] = None,
        footer: str = "astra-hawk-2026",
    ) -> Dict[str, Any]:
        e: Dict[str, Any] = {
            "title":       title,
            "description": description,
            "color":       color,
            "timestamp":   DiscordClient._now(),
            "footer":      {"text": footer},
        }
        if fields:
            e["fields"] = fields
        return e

    _C_GREEN  = 0x2ECC71
    _C_RED    = 0xE74C3C
    _C_YELLOW = 0xF39C12
    _C_BLUE   = 0x3498DB
    _C_GREY   = 0x95A5A6
    _C_ORANGE = 0xE67E22

    # ------------------------------------------------------------------ #
    # general channel                                                      #
    # ------------------------------------------------------------------ #

    def send_startup(self, symbols: List[str]) -> bool:
        embed = self._embed(
            title="🚀 System Started",
            description="**astra-hawk-2026** pricing runner is online.",
            color=self._C_GREEN,
            fields=[
                {"name": "Symbols",     "value": " ".join(f"`{s}`" for s in symbols), "inline": False},
                {"name": "Started UTC", "value": f"`{self._now()}`",                  "inline": False},
            ],
            footer="astra-hawk-2026 | general",
        )
        return self._enqueue(CHANNEL_GENERAL, {"embeds": [embed]})

    def send_shutdown(self, reason: str = "manual") -> bool:
        embed = self._embed(
            title="🛑 System Shutdown",
            description=f"Pricing runner stopped. Reason: `{reason}`",
            color=self._C_GREY,
            footer="astra-hawk-2026 | general",
        )
        return self._enqueue(CHANNEL_GENERAL, {"embeds": [embed]})

    def send_rollover(self, symbol: str, old_date: str, new_date: str, tick_utc: str) -> bool:
        embed = self._embed(
            title=f"🔁 Day Rollover — {symbol}",
            description=f"MT5 date rolled over for **{symbol}**",
            color=self._C_YELLOW,
            fields=[
                {"name": "From",     "value": old_date,        "inline": True},
                {"name": "To",       "value": new_date,        "inline": True},
                {"name": "Tick UTC", "value": f"`{tick_utc}`", "inline": False},
            ],
            footer="astra-hawk-2026 | general",
        )
        return self._enqueue(CHANNEL_GENERAL, {"embeds": [embed]})

    # ------------------------------------------------------------------ #
    # critical channel                                                     #
    # ------------------------------------------------------------------ #

    def send_critical(
        self,
        title: str,
        description: str,
        fields: Optional[List[Dict]] = None,
    ) -> bool:
        """Freeform critical alert. Use for anything needing immediate human response."""
        embed = self._embed(
            title=f"🚨 CRITICAL — {title}",
            description=description,
            color=self._C_RED,
            fields=fields,
            footer="astra-hawk-2026 | critical",
        )
        return self._enqueue(CHANNEL_CRITICAL, {"embeds": [embed]})

    def send_mt5_disconnected(self, symbol: str, stale_seconds: int) -> bool:
        embed = self._embed(
            title=f"🚨 MT5 Disconnected — {symbol}",
            description=f"No tick for **{stale_seconds}s** — MT5 may be down.",
            color=self._C_RED,
            fields=[
                {"name": "Symbol",      "value": f"`{symbol}`",          "inline": True},
                {"name": "Stale For",   "value": f"`{stale_seconds}s`",  "inline": True},
                {"name": "Checked UTC", "value": f"`{self._now()}`",     "inline": False},
            ],
            footer="astra-hawk-2026 | critical",
        )
        return self._enqueue(CHANNEL_CRITICAL, {"embeds": [embed]})

    # ------------------------------------------------------------------ #
    # alerts channel                                                       #
    # ------------------------------------------------------------------ #

    def send_trade_alert(
        self,
        symbol: str,
        action: str,         # ENTRY | EXIT | HEDGE | SL_HIT | TP_HIT
        direction: str,      # BUY | SELL
        price: float,
        lots: float,
        reason: str = "",
        profit: Optional[float] = None,
    ) -> bool:
        icon = {"ENTRY": "📥", "EXIT": "📤", "HEDGE": "🔀", "SL_HIT": "🛑", "TP_HIT": "🎯"}.get(action, "📌")
        color = (
            self._C_GREEN if action in ("EXIT", "TP_HIT") else
            self._C_RED   if action == "SL_HIT" else
            self._C_BLUE
        )
        fields = [
            {"name": "Action",    "value": f"`{action}`",    "inline": True},
            {"name": "Direction", "value": f"`{direction}`", "inline": True},
            {"name": "Price",     "value": f"`{price:.5f}`", "inline": True},
            {"name": "Lots",      "value": f"`{lots}`",      "inline": True},
        ]
        if profit is not None:
            sign = "+" if profit >= 0 else ""
            fields.append({"name": "Profit", "value": f"`{sign}{profit:.2f}`", "inline": True})
        if reason:
            fields.append({"name": "Reason", "value": reason, "inline": False})

        embed = self._embed(
            title=f"{icon} {action} — {symbol}",
            description=f"**{symbol}** | {direction} | `{action}`",
            color=color,
            fields=fields,
            footer="astra-hawk-2026 | alerts",
        )
        return self._enqueue(CHANNEL_ALERTS, {"embeds": [embed]})

    # ------------------------------------------------------------------ #
    # updates channel                                                      #
    # ------------------------------------------------------------------ #

    def send_start_locked(
        self,
        symbol: str,
        price: float,
        date_mt5: str,
        source: str,
        locked_server_time: str,
        locked_local_time: str,
    ) -> bool:
        embed = self._embed(
            title=f"🔒 Start Price Locked — {symbol}",
            description=f"**{symbol}** start price locked for `{date_mt5}`",
            color=self._C_BLUE,
            fields=[
                {"name": "Start Price",  "value": f"`{price:.5f}`",  "inline": True},
                {"name": "Date MT5",     "value": date_mt5,           "inline": True},
                {"name": "Source",       "value": f"`{source}`",      "inline": False},
                {"name": "Server Time",  "value": locked_server_time, "inline": True},
                {"name": "Local Time",   "value": locked_local_time,  "inline": True},
            ],
            footer="astra-hawk-2026 | updates",
        )
        return self._enqueue(CHANNEL_UPDATES, {"embeds": [embed]})

    def send_price_update(
        self,
        symbol: str,
        mid: float,
        bid: float,
        ask: float,
        start_price: Optional[float],
        high: Optional[float],
        low: Optional[float],
        stale: bool,
        date_mt5: str,
        server_time: str,
    ) -> bool:
        delta_str = ""
        if start_price and start_price > 0:
            d    = mid - start_price
            sign = "+" if d >= 0 else ""
            delta_str = f"\nΔ from start: **{sign}{d:.5f}**"

        status = "⏸️ STALE" if stale else "✅ LIVE"
        color  = self._C_GREY if stale else self._C_GREEN

        fields = [
            {"name": "MID", "value": f"`{mid:.5f}`", "inline": True},
            {"name": "BID", "value": f"`{bid:.5f}`", "inline": True},
            {"name": "ASK", "value": f"`{ask:.5f}`", "inline": True},
        ]
        if start_price is not None:
            fields.append({"name": "Start",    "value": f"`{start_price:.5f}`", "inline": True})
        if high is not None:
            fields.append({"name": "Day High", "value": f"`{high:.5f}`",        "inline": True})
        if low is not None:
            fields.append({"name": "Day Low",  "value": f"`{low:.5f}`",         "inline": True})
        fields += [
            {"name": "Date MT5",    "value": date_mt5,    "inline": True},
            {"name": "Server Time", "value": server_time, "inline": True},
        ]

        embed = self._embed(
            title=f"📊 {symbol} — {status}",
            description=f"**{symbol}** price update{delta_str}",
            color=color,
            fields=fields,
            footer="astra-hawk-2026 | updates",
        )
        return self._enqueue(CHANNEL_UPDATES, {"embeds": [embed]})

    # ------------------------------------------------------------------ #
    # errors channel                                                       #
    # ------------------------------------------------------------------ #

    def send_stale_alert(self, symbol: str, stale_seconds: int, last_tick_utc: str) -> bool:
        embed = self._embed(
            title=f"⚠️ Stale Tick — {symbol}",
            description=f"**{symbol}** tick has not updated for `{stale_seconds}s`",
            color=self._C_ORANGE,
            fields=[
                {"name": "Stale For",     "value": f"`{stale_seconds}s`", "inline": True},
                {"name": "Last Tick UTC", "value": f"`{last_tick_utc}`",  "inline": True},
                {"name": "Checked UTC",   "value": f"`{self._now()}`",    "inline": False},
            ],
            footer="astra-hawk-2026 | errors",
        )
        return self._enqueue(CHANNEL_ERRORS, {"embeds": [embed]})

    def send_error(self, symbol: str, error: str, context: str = "", source: str = "pricing") -> bool:
        desc = f"**{symbol}** — `{error}`"
        if context:
            desc += f"\n```{context[:800]}```"
        embed = self._embed(
            title=f"❌ Error — {symbol}",
            description=desc,
            color=self._C_RED,
            fields=[
                {"name": "Source",     "value": f"`{source}`",      "inline": True},
                {"name": "Raised UTC", "value": f"`{self._now()}`", "inline": True},
            ],
            footer="astra-hawk-2026 | errors",
        )
        return self._enqueue(CHANNEL_ERRORS, {"embeds": [embed]})

    def send_write_failure(self, symbol: str, path: str, error: str) -> bool:
        embed = self._embed(
            title=f"💾 Write Failure — {symbol}",
            description=f"Failed to persist JSON for **{symbol}**",
            color=self._C_RED,
            fields=[
                {"name": "Path",  "value": f"`{path}`",         "inline": False},
                {"name": "Error", "value": f"`{error}`",        "inline": False},
                {"name": "UTC",   "value": f"`{self._now()}`",  "inline": True},
            ],
            footer="astra-hawk-2026 | errors",
        )
        return self._enqueue(CHANNEL_ERRORS, {"embeds": [embed]})


# ---------------------------------------------------------------------------
# Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    cfg = DiscordConfig(
        general  = os.environ.get("DISCORD_WEBHOOK_GENERAL",  ""),
        critical = os.environ.get("DISCORD_WEBHOOK_CRITICAL", ""),
        alerts   = os.environ.get("DISCORD_WEBHOOK_ALERTS",   ""),
        updates  = os.environ.get("DISCORD_WEBHOOK_UPDATES",  ""),
        errors   = os.environ.get("DISCORD_WEBHOOK_ERRORS",   ""),
    )

    client = DiscordClient(cfg)
    client.start()

    client.send_startup(["XAUUSD", "EURUSD"])
    client.send_start_locked("XAUUSD", 5140.73, "2026-03-05",
        "tick_lock_existing_dayfile_at_or_after_00:00",
        "2026-03-05T03:00:03+03:00", "2026-03-05T05:30:03+05:30")
    client.send_trade_alert("XAUUSD", "ENTRY", "BUY", 5129.24, 0.1, reason="strategy-chip-A")
    client.send_error("EURUSD", "build_price_packet returned None", source="price_runner")

    print("Queue depths:", client.queue_depth())
    time.sleep(10)


# ---------------------------------------------------------------------------
# Module-level singleton + notify_discord() convenience function
# ---------------------------------------------------------------------------

_client = None  # type: Optional[DiscordClient]


def init(cfg: "DiscordConfig") -> None:
    """
    Initialise the module-level Discord client. Call once at app startup.

        import notify.discord as discord
        discord.init(DiscordConfig(general=..., critical=..., ...))
    """
    global _client
    _client = DiscordClient(cfg)
    _client.start()


def notify_discord(channel: str, message: str) -> bool:
    """
    Send a plain-text message to a Discord channel.
    This is the primary call surface — use this everywhere in the codebase.

        notify_discord("general",  "✅ System started")
        notify_discord("critical", "🚨 MT5 disconnected")
        notify_discord("alerts",   "📥 XAUUSD ENTRY BUY @ 5129.24")
        notify_discord("updates",  "🔒 Start price locked: 5140.73")
        notify_discord("errors",   "❌ build_price_packet returned None")

    For rich embeds call the client directly:
        from notify.discord import get_client
        get_client().send_trade_alert(...)
    """
    global _client
    if _client is None:
        print(f"[discord] ⚠️ notify_discord called before init() — channel='{channel}' msg='{message[:60]}'")
        return False
    return _client.send_plain(channel, message)


def get_client() -> "DiscordClient":
    """
    Return the module-level client for rich embed calls.
    Raises RuntimeError if init() has not been called.
    """
    if _client is None:
        raise RuntimeError("[discord] Not initialised — call notify.discord.init(cfg) first.")
    return _client