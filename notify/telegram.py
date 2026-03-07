# notify/telegram.py
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
# Telegram rate limits:
#   - 30 messages / second globally per bot
#   - 20 messages / minute per chat (group/channel)
#   - Exceeding → HTTP 429 with Retry-After header
#
# Strategy:
#   - One background daemon thread per channel (chat_id)
#   - Token bucket: capacity=18, refill 1 token per 3s → safe 20/min per chat
#   - Queue is unbounded — messages never dropped, only delayed
#   - On 429: sleep Retry-After exactly, retry same message
#   - On other errors: exponential backoff up to MAX_RETRIES
# ---------------------------------------------------------------------------

CHANNEL_GENERAL = "general"
CHANNEL_CRITICAL = "critical"
CHANNEL_ALERTS = "alerts"
CHANNEL_UPDATES = "updates"
CHANNEL_ERRORS = "errors"

ALL_CHANNELS = (CHANNEL_GENERAL, CHANNEL_CRITICAL, CHANNEL_ALERTS, CHANNEL_UPDATES, CHANNEL_ERRORS)

_BUCKET_CAPACITY = 18  # safe under Telegram's 20/min per chat
_REFILL_RATE = 3.0  # seconds per token
_MAX_RETRIES = 5
_RETRY_BASE_SLEEP = 1.0
_SEND_TIMEOUT = 6.0

# Telegram HTML entities that must be escaped in user-supplied strings
_HTML_ESCAPE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})


def _esc(s: str) -> str:
    """Escape HTML special chars in dynamic values."""
    return str(s).translate(_HTML_ESCAPE)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TelegramConfig:
    """
    Bot token + chat IDs for each of the 5 named channels.
    Always pass via environment variables.

    Example:
        import os
        cfg = TelegramConfig(
            bot_token = os.environ["TELEGRAM_BOT_TOKEN"],
            general   = os.environ["TELEGRAM_CHAT_GENERAL"],
            critical  = os.environ["TELEGRAM_CHAT_CRITICAL"],
            alerts    = os.environ["TELEGRAM_CHAT_ALERTS"],
            updates   = os.environ["TELEGRAM_CHAT_UPDATES"],
            errors    = os.environ["TELEGRAM_CHAT_ERRORS"],
        )

    How to get bot token:
        @BotFather → /newbot → copy token

    How to get chat_id:
        Add @userinfobot to the group/channel and send any message.
        For personal DM: message your bot then call
        https://api.telegram.org/bot<TOKEN>/getUpdates

    Channel routing:
        general   — lifecycle: startup, shutdown, day rollover
        critical  — immediate attention: MT5 disconnect, runaway loss
        alerts    — trade events: entry, exit, hedge, SL/TP
        updates   — periodic: price snapshots, start-price lock, H/L
        errors    — exceptions, stale ticks, write failures
    """

    def __init__(
            self,
            bot_token: str,
            general: str = "",
            critical: str = "",
            alerts: str = "",
            updates: str = "",
            errors: str = "",
    ):
        self.bot_token = bot_token
        self.chat_ids: Dict[str, str] = {
            CHANNEL_GENERAL: general,
            CHANNEL_CRITICAL: critical,
            CHANNEL_ALERTS: alerts,
            CHANNEL_UPDATES: updates,
            CHANNEL_ERRORS: errors,
        }

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    def get_chat_id(self, channel: str) -> str:
        return self.chat_ids.get(channel, "")


# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------

class _TokenBucket:
    def __init__(self, capacity: int, refill_rate_seconds: float):
        self._capacity = capacity
        self._tokens = float(capacity)
        self._rate = refill_rate_seconds
        self._last_refill = time.monotonic()

    def consume(self) -> None:
        while True:
            now = time.monotonic()
            elapsed = now - self._last_refill
            gained = elapsed / self._rate
            if gained > 0:
                self._tokens = min(self._capacity, self._tokens + gained)
                self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            time.sleep(max(0.05, (1.0 - self._tokens) * self._rate))


# ---------------------------------------------------------------------------
# Queue item
# ---------------------------------------------------------------------------

@dataclass
class _QueueItem:
    base_url: str
    chat_id: str
    text: str
    silent: bool
    channel: str


# ---------------------------------------------------------------------------
# Per-channel background sender
# ---------------------------------------------------------------------------

class _ChannelSender(threading.Thread):
    def __init__(self, channel: str):
        super().__init__(name=f"telegram-{channel}", daemon=True)
        self.channel = channel
        self._q = queue.Queue()  # type: queue.Queue[_QueueItem]
        self._bucket = _TokenBucket(_BUCKET_CAPACITY, _REFILL_RATE)
        self.dropped = 0

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
            self._bucket.consume()
            success, retry_after = _post_message(item)

            if success:
                return

            if retry_after is not None:
                wait = retry_after + 0.25
                print(f"[telegram:{self.channel}] ⏳ 429 — waiting {wait:.1f}s")
                time.sleep(wait)
                continue

            retries += 1
            if retries > _MAX_RETRIES:
                print(f"[telegram:{self.channel}] ❌ dropped after {_MAX_RETRIES} retries")
                self.dropped += 1
                return

            sleep = _RETRY_BASE_SLEEP * (2 ** (retries - 1))
            print(f"[telegram:{self.channel}] ⚠️ retry {retries}/{_MAX_RETRIES} in {sleep:.1f}s")
            time.sleep(sleep)


# ---------------------------------------------------------------------------
# Raw HTTP POST
# ---------------------------------------------------------------------------

def _post_message(item: _QueueItem) -> Tuple[bool, Optional[float]]:
    url = f"{item.base_url}/sendMessage"
    payload = {
        "chat_id": item.chat_id,
        "text": item.text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": item.silent,
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_SEND_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False), None

    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after: Optional[float] = 3.0
            try:
                body = json.loads(e.read().decode("utf-8"))
                params = body.get("parameters") or {}
                retry_after = float(params.get("retry_after", 3.0))
            except Exception:
                pass
            return False, retry_after
        try:
            print(f"[telegram] ❌ HTTP {e.code}: {e.read().decode()[:200]}")
        except Exception:
            print(f"[telegram] ❌ HTTP {e.code}: {e.reason}")
        return False, None
    except Exception as e:
        print(f"[telegram] ❌ post failed: {e!r}")
        return False, None


# ---------------------------------------------------------------------------
# Message formatters
# Telegram HTML: <b> <i> <code> <pre> — nothing else
#
# Layout rules for quick-glimpse readability:
#   Line 1  : ICON  TITLE — SYMBOL  |  STATUS
#   Line 2  : ─────────────────────── (divider)
#   Lines 3+: Key    Value (monospace value, left-aligned label)
#   Last    : ─── footer (source module, timestamp)
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _div() -> str:
    return "─────────────────────"


def _row(label: str, value: Any, width: int = 12) -> str:
    """Fixed-width label + monospace value for tabular alignment."""
    return f"<b>{label:<{width}}</b> <code>{_esc(str(value))}</code>"


def _footer(source: str) -> str:
    return f"\n<i>⏱ {_now_utc()}  ·  {source}</i>"


# ---------------------------------------------------------------------------
# TelegramClient
# ---------------------------------------------------------------------------

class TelegramClient:
    """
    Thread-safe Telegram notifier with per-channel rate-limited queues.

    Quick start:
        cfg    = TelegramConfig(bot_token=..., general=..., critical=..., ...)
        client = TelegramClient(cfg)
        client.start()

        client.send_startup(["XAUUSD", "EURUSD"])
        client.send_start_locked("XAUUSD", price=5140.73, ...)
        client.send_trade_alert("XAUUSD", action="ENTRY", ...)

    All send_* calls return immediately — messages are queued and sent
    in the background with rate limiting and automatic retry.
    """

    def __init__(self, cfg: TelegramConfig):
        self._cfg = cfg
        self._senders = {ch: _ChannelSender(ch) for ch in ALL_CHANNELS}
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        for s in self._senders.values():
            s.start()
        self._started = True
        print(f"[telegram] ✅ senders started — channels: {list(self._senders)}")

    def queue_depth(self) -> Dict[str, int]:
        return {ch: s.qsize() for ch, s in self._senders.items()}

    def dropped_count(self) -> Dict[str, int]:
        return {ch: s.dropped for ch, s in self._senders.items()}

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _enqueue(self, channel: str, text: str, silent: bool = False) -> bool:
        chat_id = self._cfg.get_chat_id(channel)
        if not chat_id:
            print(f"[telegram] ⚠️ no chat_id for channel='{channel}'")
            return False
        if not self._started:
            print(f"[telegram] ⚠️ call client.start() before sending")
            return False
        self._senders[channel].enqueue(_QueueItem(
            base_url=self._cfg.base_url,
            chat_id=chat_id,
            text=text,
            silent=silent,
            channel=channel,
        ))
        return True

    # ------------------------------------------------------------------ #
    # Plain text
    # ------------------------------------------------------------------ #

    def send_plain(self, channel: str, message: str, silent: bool = False) -> bool:
        """Send a plain text message to a channel."""
        return self._enqueue(channel, message, silent=silent)

    # ------------------------------------------------------------------ #
    # general channel                                                      #
    # ------------------------------------------------------------------ #

    def send_startup(self, symbols: List[str]) -> bool:
        syms = "  ".join(f"<code>{_esc(s)}</code>" for s in symbols)
        text = (
            f"🚀 <b>System Started</b>\n"
            f"{_div()}\n"
            f"{_row('Symbols', '  '.join(symbols))}\n"
            f"{_row('Status', 'ONLINE')}\n"
            f"{_footer('astra-hawk-2026 | general')}"
        )
        return self._enqueue(CHANNEL_GENERAL, text)

    def send_shutdown(self, reason: str = "manual") -> bool:
        text = (
            f"🛑 <b>System Shutdown</b>\n"
            f"{_div()}\n"
            f"{_row('Reason', reason)}\n"
            f"{_footer('astra-hawk-2026 | general')}"
        )
        return self._enqueue(CHANNEL_GENERAL, text)

    def send_rollover(self, symbol: str, old_date: str, new_date: str, tick_utc: str) -> bool:
        text = (
            f"🔁 <b>Day Rollover</b>  |  <code>{_esc(symbol)}</code>\n"
            f"{_div()}\n"
            f"{_row('Symbol', symbol)}\n"
            f"{_row('From', old_date)}\n"
            f"{_row('To', new_date)}\n"
            f"{_row('Tick UTC', tick_utc)}\n"
            f"{_footer('astra-hawk-2026 | general')}"
        )
        return self._enqueue(CHANNEL_GENERAL, text)

    # ------------------------------------------------------------------ #
    # critical channel                                                     #
    # ------------------------------------------------------------------ #

    def send_critical(self, title: str, description: str) -> bool:
        text = (
            f"🚨 <b>CRITICAL  —  {_esc(title)}</b>\n"
            f"{_div()}\n"
            f"{_esc(description)}\n"
            f"{_footer('astra-hawk-2026 | critical')}"
        )
        return self._enqueue(CHANNEL_CRITICAL, text, silent=False)

    def send_mt5_disconnected(self, symbol: str, stale_seconds: int) -> bool:
        text = (
            f"🚨 <b>MT5 Disconnected</b>  |  <code>{_esc(symbol)}</code>\n"
            f"{_div()}\n"
            f"{_row('Symbol', symbol)}\n"
            f"{_row('Stale For', f'{stale_seconds}s')}\n"
            f"{_row('Action', 'CHECK MT5 TERMINAL')}\n"
            f"{_footer('astra-hawk-2026 | critical')}"
        )
        return self._enqueue(CHANNEL_CRITICAL, text, silent=False)

    def send_catastrophic_loss(
            self,
            total_loss: float,
            limit: float,
            symbols_closed: List[str] = None,
    ) -> bool:
        """Send catastrophic loss alert."""
        symbols_str = ", ".join(symbols_closed) if symbols_closed else "None"
        text = (
            f"🚨 <b>CATASTROPHIC LOSS — Trading Halted</b>\n"
            f"{_div()}\n"
            f"{_row('Total Loss', f'${total_loss:.2f}')}\n"
            f"{_row('Limit', f'${limit:.2f}')}\n"
            f"{_row('Closed', symbols_str)}\n"
            f"{_footer('astra-hawk-2026 | critical')}"
        )
        return self._enqueue(CHANNEL_CRITICAL, text, silent=False)

    def send_profit_lock(self, total_profit: float, limit: float) -> bool:
        """Send profit lock notification."""
        text = (
            f"🔒 <b>PROFIT LOCKED — Trading Stopped</b>\n"
            f"{_div()}\n"
            f"{_row('Profit', f'+${total_profit:.2f}')}\n"
            f"{_row('Target', f'${limit:.2f}')}\n"
            f"{_footer('astra-hawk-2026 | critical')}"
        )
        return self._enqueue(CHANNEL_CRITICAL, text, silent=False)

    # ------------------------------------------------------------------ #
    # alerts channel                                                       #
    # ------------------------------------------------------------------ #

    def send_trade_alert(
            self,
            symbol: str,
            action: str,  # ENTRY | EXIT | HEDGE | SL_HIT | TP_HIT | MIN_PROFIT_LOCK
            direction: str,  # BUY | SELL
            price: float,
            lots: float,
            reason: str = "",
            profit: Optional[float] = None,
            ticket: Optional[int] = None,
    ) -> bool:
        icon = {
            "ENTRY": "📥", "EXIT": "📤",
            "HEDGE": "🔀", "SL_HIT": "🛑", "TP_HIT": "🎯",
            "MIN_PROFIT_LOCK": "💰",
        }.get(action, "📌")

        profit_line = ""
        if profit is not None:
            sign = "+" if profit >= 0 else ""
            profit_line = f"\n{_row('Profit', f'{sign}{profit:.2f}')}"

        reason_line = f"\n{_row('Reason', reason)}" if reason else ""
        ticket_line = f"\n{_row('Ticket', ticket)}" if ticket else ""

        text = (
            f"{icon} <b>{action}</b>  |  <code>{_esc(symbol)}</code>  |  <b>{_esc(direction)}</b>\n"
            f"{_div()}\n"
            f"{_row('Symbol', symbol)}\n"
            f"{_row('Action', action)}\n"
            f"{_row('Direction', direction)}\n"
            f"{_row('Price', f'{price:.5f}')}\n"
            f"{_row('Lots', lots)}"
            f"{ticket_line}"
            f"{profit_line}"
            f"{reason_line}\n"
            f"{_footer('astra-hawk-2026 | alerts')}"
        )
        return self._enqueue(CHANNEL_ALERTS, text, silent=False)

    def send_min_profit_lock(
            self,
            symbol: str,
            direction: str,
            entry_price: float,
            exit_price: float,
            peak_profit: float,
            locked_profit: float,
            strategy: str = "",
    ) -> bool:
        """Send min profit lock alert."""
        text = (
            f"💰 <b>MIN PROFIT LOCK</b>  |  <code>{_esc(symbol)}</code>\n"
            f"{_div()}\n"
            f"{_row('Direction', direction)}\n"
            f"{_row('Entry', f'{entry_price:.5f}')}\n"
            f"{_row('Exit', f'{exit_price:.5f}')}\n"
            f"{_row('Peak Profit', f'+${peak_profit:.2f}')}\n"
            f"{_row('Locked', f'+${locked_profit:.2f}')}\n"
            f"{_row('Strategy', strategy)}\n"
            f"{_footer('astra-hawk-2026 | alerts')}"
        )
        return self._enqueue(CHANNEL_ALERTS, text, silent=False)

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
        text = (
            f"🔒 <b>Start Price Locked</b>  |  <code>{_esc(symbol)}</code>\n"
            f"{_div()}\n"
            f"{_row('Symbol', symbol)}\n"
            f"{_row('Date MT5', date_mt5)}\n"
            f"{_row('Start Price', f'{price:.5f}')}\n"
            f"{_row('Source', source)}\n"
            f"{_row('Server', locked_server_time)}\n"
            f"{_row('Local', locked_local_time)}\n"
            f"{_footer('astra-hawk-2026 | updates')}"
        )
        return self._enqueue(CHANNEL_UPDATES, text, silent=True)

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
        status = "⏸ STALE" if stale else "✅ LIVE"

        delta_line = ""
        if start_price and start_price > 0:
            d = mid - start_price
            sign = "+" if d >= 0 else ""
            delta_line = f"\n{_row('Δ Start', f'{sign}{d:.5f}')}"

        high_line = f"\n{_row('Day High', f'{high:.5f}')}" if high is not None else ""
        low_line = f"\n{_row('Day Low', f'{low:.5f}')}" if low is not None else ""
        sp_line = f"\n{_row('Start', f'{start_price:.5f}')}" if start_price is not None else ""

        text = (
            f"📊 <b>{_esc(symbol)}</b>  |  {status}\n"
            f"{_div()}\n"
            f"{_row('MID', f'{mid:.5f}')}\n"
            f"{_row('BID', f'{bid:.5f}')}\n"
            f"{_row('ASK', f'{ask:.5f}')}"
            f"{sp_line}"
            f"{delta_line}"
            f"{high_line}"
            f"{low_line}\n"
            f"{_div()}\n"
            f"{_row('Date MT5', date_mt5)}\n"
            f"{_row('Server', server_time)}\n"
            f"{_footer('astra-hawk-2026 | updates')}"
        )
        return self._enqueue(CHANNEL_UPDATES, text, silent=True)

    def send_daily_summary(
            self,
            date_mt5: str,
            total_trades: int,
            realized_pnl: float,
            symbols_traded: List[str],
    ) -> bool:
        """Send end-of-day summary."""
        emoji = "🟢" if realized_pnl >= 0 else "🔴"
        sign = "+" if realized_pnl >= 0 else ""
        symbols_str = ", ".join(symbols_traded) if symbols_traded else "None"

        text = (
            f"📅 <b>Daily Summary</b>  |  <code>{_esc(date_mt5)}</code>\n"
            f"{_div()}\n"
            f"{_row('Date', date_mt5)}\n"
            f"{_row('Trades', total_trades)}\n"
            f"{_row('P&L', f'{emoji} {sign}${realized_pnl:.2f}')}\n"
            f"{_row('Symbols', symbols_str)}\n"
            f"{_footer('astra-hawk-2026 | updates')}"
        )
        return self._enqueue(CHANNEL_UPDATES, text, silent=True)

    # ------------------------------------------------------------------ #
    # errors channel                                                       #
    # ------------------------------------------------------------------ #

    def send_stale_alert(self, symbol: str, stale_seconds: int, last_tick_utc: str) -> bool:
        text = (
            f"⚠️ <b>Stale Tick</b>  |  <code>{_esc(symbol)}</code>\n"
            f"{_div()}\n"
            f"{_row('Symbol', symbol)}\n"
            f"{_row('Stale For', f'{stale_seconds}s')}\n"
            f"{_row('Last Tick', last_tick_utc)}\n"
            f"{_footer('astra-hawk-2026 | errors')}"
        )
        return self._enqueue(CHANNEL_ERRORS, text, silent=False)

    def send_error(self, symbol: str, error: str, context: str = "", source: str = "pricing") -> bool:
        ctx_block = f"\n<pre>{_esc(context[:600])}</pre>" if context else ""
        text = (
            f"❌ <b>Error</b>  |  <code>{_esc(symbol)}</code>\n"
            f"{_div()}\n"
            f"{_row('Symbol', symbol)}\n"
            f"{_row('Source', source)}\n"
            f"<b>Error</b>\n<code>{_esc(error)}</code>"
            f"{ctx_block}\n"
            f"{_footer('astra-hawk-2026 | errors')}"
        )
        return self._enqueue(CHANNEL_ERRORS, text, silent=False)

    def send_write_failure(self, symbol: str, path: str, error: str) -> bool:
        text = (
            f"💾 <b>Write Failure</b>  |  <code>{_esc(symbol)}</code>\n"
            f"{_div()}\n"
            f"{_row('Symbol', symbol)}\n"
            f"{_row('Path', path)}\n"
            f"<b>Error</b>\n<code>{_esc(error)}</code>\n"
            f"{_footer('astra-hawk-2026 | errors')}"
        )
        return self._enqueue(CHANNEL_ERRORS, text, silent=False)

    def send_order_failure(
            self,
            symbol: str,
            action: str,
            error: str,
            retcode: int = 0,
    ) -> bool:
        """Send order failure notification."""
        text = (
            f"❌ <b>Order Failed</b>  |  <code>{_esc(symbol)}</code>\n"
            f"{_div()}\n"
            f"{_row('Symbol', symbol)}\n"
            f"{_row('Action', action)}\n"
            f"{_row('Retcode', retcode)}\n"
            f"<b>Error</b>\n<code>{_esc(error)}</code>\n"
            f"{_footer('astra-hawk-2026 | errors')}"
        )
        return self._enqueue(CHANNEL_ERRORS, text, silent=False)

    # ------------------------------------------------------------------ #
    # Command polling (control panel)                                      #
    # ------------------------------------------------------------------ #

    def get_updates(self, offset: Optional[int] = None, timeout: int = 10) -> List[Dict]:
        """
        Long-poll for bot commands. Use in a control loop:

            offset = None
            while True:
                for upd in client.get_updates(offset=offset):
                    offset = upd["update_id"] + 1
                    text   = (upd.get("message") or {}).get("text", "")
                    if text.startswith("/status"):
                        notify_telegram("general", "✅ Running")
        """
        url = f"{self._cfg.base_url}/getUpdates"
        params: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            data = json.dumps(params).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("ok"):
                    return result.get("result", [])
        except Exception as e:
            print(f"[telegram] ⚠️ get_updates failed: {e!r}")
        return []


# ---------------------------------------------------------------------------
# Module-level singleton + notify_telegram() convenience function
# ---------------------------------------------------------------------------

_client: Optional[TelegramClient] = None


def init(cfg: TelegramConfig) -> None:
    """
    Initialise the module-level Telegram client. Call once at app startup.

        import notify.telegram as telegram
        telegram.init(TelegramConfig(bot_token=..., general=..., ...))
    """
    global _client
    _client = TelegramClient(cfg)
    _client.start()


def notify_telegram(channel: str, message: str, silent: bool = False) -> bool:
    """
    Send a plain-text (HTML) message to a Telegram channel.
    This is the primary call surface — use this everywhere in the codebase.

        notify_telegram("general",  "✅ System started")
        notify_telegram("critical", "🚨 MT5 disconnected — check terminal")
        notify_telegram("alerts",   "📥 XAUUSD  ENTRY  BUY  @  5129.24")
        notify_telegram("updates",  "🔒 Start price locked: 5140.73")
        notify_telegram("errors",   "❌ build_price_packet returned None")

    For rich structured messages use the client directly:
        from notify.telegram import get_client
        get_client().send_trade_alert(...)
        get_client().send_price_update(...)
    """
    global _client
    if _client is None:
        print(f"[telegram] ⚠️ notify_telegram called before init() — channel='{channel}'")
        return False
    return _client._enqueue(channel, message, silent=silent)


def get_client() -> TelegramClient:
    """Return the module-level client for rich structured messages."""
    if _client is None:
        raise RuntimeError("[telegram] Not initialised — call notify.telegram.init(cfg) first.")
    return _client


# ---------------------------------------------------------------------------
# Example — shows what each message looks like in Telegram
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    cfg = TelegramConfig(
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        general=os.environ.get("TELEGRAM_CHAT_GENERAL", ""),
        critical=os.environ.get("TELEGRAM_CHAT_CRITICAL", ""),
        alerts=os.environ.get("TELEGRAM_CHAT_ALERTS", ""),
        updates=os.environ.get("TELEGRAM_CHAT_UPDATES", ""),
        errors=os.environ.get("TELEGRAM_CHAT_ERRORS", ""),
    )

    init(cfg)

    # Plain text — works anywhere
    notify_telegram("general", "✅ System started")
    notify_telegram("critical", "🚨 MT5 disconnected — check terminal")
    notify_telegram("alerts", "📥 XAUUSD  ENTRY  BUY  @  5129.24")
    notify_telegram("updates", "🔒 Start price locked: 5140.73")
    notify_telegram("errors", "❌ build_price_packet returned None")

    # Rich structured messages
    client = get_client()
    client.send_startup(["XAUUSD", "EURUSD"])
    client.send_start_locked(
        symbol="XAUUSD", price=5140.73, date_mt5="2026-03-05",
        source="tick_lock_existing_dayfile_at_or_after_00:00",
        locked_server_time="2026-03-05T03:00:03+03:00",
        locked_local_time="2026-03-05T05:30:03+05:30",
    )
    client.send_price_update(
        symbol="XAUUSD", mid=5129.245, bid=5129.10, ask=5129.39,
        start_price=5140.73, high=5142.10, low=5118.04,
        stale=False, date_mt5="2026-03-05", server_time="2026-03-05T19:05:00+03:00",
    )
    client.send_trade_alert("XAUUSD", "ENTRY", "BUY", 5129.24, 0.1, reason="chip-A signal")
    client.send_error("EURUSD", "build_price_packet returned None", source="price_runner")

    print("Queue depths:", client.queue_depth())
    time.sleep(15)