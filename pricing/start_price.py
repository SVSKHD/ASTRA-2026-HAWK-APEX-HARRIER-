from __future__ import annotations

import os
import time
import threading
from datetime import datetime, timezone

import MetaTrader5 as mt5

from settings import PriceSettings
from clock import tick_time_to_clock, to_server_time, to_local_time, iso_z
from storage import (
    resolve_day_path,
    resolve_start_root_path,
    read_json,
    default_payload,
    build_start_root_payload,
    atomic_write_json,
    resolve_start_emergency_path,
    append_line,
)
from config import get_enabled_symbols
from notify import (
    init_discord,
    init_telegram,
    DiscordConfig,
    TelegramConfig,
    notify_rollover,
    notify_start_locked,
    _safe_broadcast,
    CHANNEL_ERRORS,
    CHANNEL_UPDATES,
)
import os

MIDNIGHT_GRACE_MINUTES = 10
STALE_AFTER_SECONDS = 20  # tune: 10–60


def ensure_mt5(max_retries: int = 10, sleep_s: float = 1.0):
    for _ in range(max_retries):
        if mt5.initialize():
            return
        time.sleep(sleep_s)
    raise RuntimeError(f"MT5 initialize failed after retries: {mt5.last_error()}")


def ensure_symbol_ready(symbol: str) -> bool:
    sinfo = mt5.symbol_info(symbol)
    if sinfo is None:
        return False
    if not getattr(sinfo, "visible", True):
        mt5.symbol_select(symbol, True)
        sinfo = mt5.symbol_info(symbol)
    return sinfo is not None and getattr(sinfo, "visible", True)


def get_tick(symbol: str):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or tick.time == 0:
        return None
    bid = float(tick.bid)
    ask = float(tick.ask)
    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else float(tick.last) if tick.last else 0.0
    if mid <= 0:
        return None
    return tick, bid, ask, mid


def lock_window_ok(cfg: PriceSettings, tick_hhmm: str) -> bool:
    return tick_hhmm >= cfg.lock_hhmm_mt5


def _within_midnight_grace(clk) -> bool:
    if clk.tick_time_utc.hour != 0:
        return False
    return clk.tick_time_utc.minute < MIDNIGHT_GRACE_MINUTES


def _print_no_tick_diagnostics(symbol: str):
    term = mt5.terminal_info()
    acc = mt5.account_info()
    sinfo = mt5.symbol_info(symbol)
    last_err = mt5.last_error()

    print(f"[{symbol}] ⏳ no tick / no quotes yet... last_error={last_err}")

    if term is not None:
        connected = getattr(term, "connected", None)
        trade_allowed = getattr(term, "trade_allowed", None)
        print(f"[{symbol}] terminal: connected={connected} trade_allowed={trade_allowed}")

    if acc is not None:
        print(f"[{symbol}] account: login={getattr(acc,'login',None)} server={getattr(acc,'server',None)}")

    if sinfo is None:
        print(f"[{symbol}] symbol_info: None (symbol name may be wrong / broker suffix?)")
    else:
        visible = getattr(sinfo, "visible", None)
        trade_mode = getattr(sinfo, "trade_mode", None)
        print(f"[{symbol}] symbol_info: visible={visible} trade_mode={trade_mode}")
        if visible is False:
            ok = mt5.symbol_select(symbol, True)
            print(f"[{symbol}] symbol_select({symbol}, True) => {ok}")


def _safe_write_json(path: str, payload: dict, pretty: bool, em_path: str, em_line: str, warn_tag: str) -> bool:
    try:
        ok = atomic_write_json(path, payload, pretty=pretty)
        if ok is None:
            return True
        return bool(ok)
    except Exception as e:
        append_line(em_path, f"{em_line} | WRITE_FAIL {warn_tag} | err={repr(e)} | path={path}")
        return False


def _reset_start_block() -> dict:
    return {
        "status": "PENDING",
        "price": None,
        "source": None,
        "locked_tick_time_utc": None,
        "locked_server_time": None,
        "locked_local_time": None,
    }


def run_start_price_loop(symbol: str, cfg: PriceSettings):
    ensure_mt5()

    if not ensure_symbol_ready(symbol):
        print(f"[{symbol}] ❌ symbol not ready (wrong name or not available). Will keep retrying...")

    last_date_mt5: str | None = None
    last_status_print = 0.0

    last_tick_epoch: int | None = None
    last_tick_change_monotonic = time.time()

    last_locked_date: str | None = None
    last_start_error_notify_ts = 0.0
    start_error_notify_interval = 300.0  # 5 min

    em_path = resolve_start_emergency_path(cfg.base_dir, symbol)

    while True:
        ensure_symbol_ready(symbol)

        got = get_tick(symbol)
        if got is None:
            now = time.time()
            if now - last_status_print >= cfg.status_print_seconds:
                _print_no_tick_diagnostics(symbol)
                last_status_print = now
            time.sleep(cfg.poll_seconds)
            continue

        tick, bid, ask, mid = got
        clk = tick_time_to_clock(tick.time)

        server_dt = to_server_time(clk.tick_time_utc, cfg.server_tz)
        local_dt = to_local_time(clk.tick_time_utc, cfg.local_tz)

        date_mt5 = clk.date_mt5
        day_path = resolve_day_path(cfg.base_dir, symbol, date_mt5)

        payload = read_json(day_path)
        day_file_exists = payload is not None
        payload = payload or default_payload(symbol, date_mt5)

        payload.setdefault("start", _reset_start_block())

        payload["tz"]["server"] = str(cfg.server_tz)
        payload["tz"]["local"] = getattr(cfg.local_tz, "key", None) or str(cfg.local_tz)

        if last_date_mt5 is None:
            last_date_mt5 = date_mt5
        elif date_mt5 != last_date_mt5:
            old = last_date_mt5
            new = date_mt5

            print("\n" + "=" * 70)
            print(f"[{symbol}] 🔁 ROLLOVER DETECTED")
            print(f"[{symbol}] OLD MT5 DATE : {old}")
            print(f"[{symbol}] NEW MT5 DATE : {new}")
            print(f"[{symbol}] TICK TIME UTC: {iso_z(clk.tick_time_utc)}")
            print(f"[{symbol}] SERVER TIME  : {server_dt.isoformat()}")
            print(f"[{symbol}] LOCAL TIME   : {local_dt.isoformat()}")
            print("=" * 70 + "\n")

            payload["meta"]["rollover_detected"] = True
            payload["meta"]["last_rollover_from"] = old
            payload["start"] = _reset_start_block()

            last_tick_epoch = None
            last_tick_change_monotonic = time.time()

            append_line(
                em_path,
                f"{new} | ROLLOVER | from={old} -> to={new} | tick={iso_z(clk.tick_time_utc)}"
            )

            try:
                notify_rollover(
                    symbol=symbol,
                    old_date=old,
                    new_date=new,
                    tick_utc=iso_z(clk.tick_time_utc),
                    server_time=server_dt.isoformat(),
                    local_time=local_dt.isoformat(),
                )
            except Exception:
                pass

            last_date_mt5 = new
            last_locked_date = None

        now_mono = time.time()
        if last_tick_epoch is None:
            last_tick_epoch = tick.time
            last_tick_change_monotonic = now_mono
        else:
            if tick.time != last_tick_epoch:
                last_tick_epoch = tick.time
                last_tick_change_monotonic = now_mono

        stale_for = now_mono - last_tick_change_monotonic
        is_stale = stale_for >= STALE_AFTER_SECONDS
        payload["meta"]["market_open"] = not is_stale

        nowu = datetime.now(timezone.utc)
        payload["timestamps"] = {
            "updated_utc": nowu.isoformat().replace("+00:00", "Z"),
            "tick_time_utc": iso_z(clk.tick_time_utc),
            "server_time": server_dt.isoformat(),
            "local_time": local_dt.isoformat(),
        }

        start = payload.get("start") or _reset_start_block()

        allow_lock_now = day_file_exists or _within_midnight_grace(clk) or cfg.allow_bootstrap_lock

        if start.get("status") != "LOCKED" and allow_lock_now and (not is_stale):
            if lock_window_ok(cfg, clk.time_mt5_hhmm):
                start["status"] = "LOCKED"
                start["price"] = mid

                src_prefix = "tick_lock_midnight_window" if _within_midnight_grace(clk) else "tick_lock_existing_dayfile"
                start["source"] = f"{src_prefix}_at_or_after_{cfg.lock_hhmm_mt5}"

                start["locked_tick_time_utc"] = iso_z(clk.tick_time_utc)
                start["locked_server_time"] = server_dt.isoformat()
                start["locked_local_time"] = local_dt.isoformat()

                append_line(
                    em_path,
                    f"{date_mt5} | START_LOCKED | price={mid} | mt5={start['locked_tick_time_utc']} | "
                    f"server={start['locked_server_time']} | local={start['locked_local_time']} | source={start['source']}"
                )

        payload["start"] = start

        em_line = f"{date_mt5} | tick_mt5={iso_z(clk.tick_time_utc)} | mid={mid} | stale={int(stale_for)}s"

        do_write_day = (not is_stale) or (time.time() - last_status_print >= cfg.status_print_seconds)
        if do_write_day:
            _safe_write_json(day_path, payload, cfg.pretty_json, em_path, em_line, "DAY_FILE")

        root_path = None
        if payload["start"]["status"] == "LOCKED":
            root_path = resolve_start_root_path(cfg.base_dir, symbol)
            root_payload = build_start_root_payload(payload)
            _safe_write_json(root_path, root_payload, cfg.pretty_json, em_path, em_line, "ROOT_START")

            if last_locked_date != date_mt5:
                print(
                    f"[{symbol}] ✅ START LOCKED for {date_mt5} | price={payload['start']['price']} | "
                    f"MT5={payload['start']['locked_tick_time_utc']} | "
                    f"SERVER={payload['start']['locked_server_time']} | "
                    f"LOCAL={payload['start']['locked_local_time']} | "
                    f"SOURCE={payload['start']['source']}"
                )

                try:
                    notify_start_locked(
                        symbol=symbol,
                        date_mt5=date_mt5,
                        price=float(payload["start"]["price"]),
                        tick_time_utc=payload["start"]["locked_tick_time_utc"],
                        server_time=payload["start"]["locked_server_time"],
                        local_time=payload["start"]["locked_local_time"],
                        source=payload["start"]["source"],
                    )
                except Exception:
                    pass

                last_locked_date = date_mt5

        now = time.time()
        if now - last_status_print >= cfg.status_print_seconds:
            sp = payload["start"]["price"]
            st = payload["start"]["status"]
            tick_iso = payload["timestamps"]["tick_time_utc"]
            state = "LIVE" if not is_stale else f"STALE({int(stale_for)}s)"

            print(
                f"[{symbol}] {state} | MT5={tick_iso} | SERVER={server_dt.isoformat()} | LOCAL={local_dt.isoformat()} | "
                f"START={st}({sp}) | MID={mid:.5f} bid={bid:.5f} ask={ask:.5f} | "
                f"DAY_FILE={day_path}"
                + (f" | ROOT_START={root_path}" if root_path else "")
            )
            last_status_print = now

        if payload["start"]["status"] != "LOCKED":
            now_err = time.time()
            if now_err - last_start_error_notify_ts >= start_error_notify_interval:
                reason = []
                if is_stale:
                    reason.append(f"stale_tick={int(stale_for)}s")
                if not allow_lock_now:
                    reason.append("lock_window_not_allowed")
                if not lock_window_ok(cfg, clk.time_mt5_hhmm):
                    reason.append(f"before_lock_hhmm={cfg.lock_hhmm_mt5}")
                if payload["start"]["price"] is None:
                    reason.append("start_price_none")

                reason_text = ", ".join(reason) if reason else "pending_lock"

                try:
                    _safe_broadcast(
                        channel=CHANNEL_ERRORS,
                        message=(
                            f"⚠️ START NOT LOCKED — {symbol}\n"
                            f"DATE: {date_mt5}\n"
                            f"MT5: {iso_z(clk.tick_time_utc)}\n"
                            f"SERVER: {server_dt.isoformat()}\n"
                            f"LOCAL: {local_dt.isoformat()}\n"
                            f"STATUS: {payload['start']['status']}\n"
                            f"MID: {mid}\n"
                            f"REASON: {reason_text}"
                        ),
                    )
                except Exception:
                    pass

                last_start_error_notify_ts = now_err

        time.sleep(cfg.poll_seconds)

def init_notifiers():
    try:
        discord_cfg = DiscordConfig(
            general=os.environ.get("DISCORD_WEBHOOK_GENERAL", ""),
            critical=os.environ.get("DISCORD_WEBHOOK_CRITICAL", ""),
            alerts=os.environ.get("DISCORD_WEBHOOK_ALERTS", ""),
            updates=os.environ.get("DISCORD_WEBHOOK_UPDATES", ""),
            errors=os.environ.get("DISCORD_WEBHOOK_ERRORS", ""),
        )

        if any(discord_cfg.webhooks.values()):
            init_discord(discord_cfg)
            print("✅ Discord initialised")
        else:
            print("⚠️ Discord init skipped: no webhook config")
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

        if telegram_cfg.bot_token:
            init_telegram(telegram_cfg)
            print("✅ Telegram initialised")
        else:
            print("⚠️ Telegram init skipped: no bot token")
    except Exception as e:
        print(f"⚠️ Telegram init failed: {e}")

def send_start_runner_boot(symbols: list[str]) -> None:
    try:
        _safe_broadcast(
            channel=CHANNEL_UPDATES,
            message=(
                "🚀 START PRICE RUNNER INITIALISED\n"
                f"SYMBOLS: {', '.join(symbols)}\n"
                f"UTC: {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}"
            ),
        )
        print("✅ Startup notify queued")
    except Exception as e:
        print(f"⚠️ Startup notify failed: {e}")

if __name__ == "__main__":
    init_notifiers()

    cfg = PriceSettings()
    symbols = get_enabled_symbols()

    send_start_runner_boot(symbols)

    print("=== START PRICE RUNNER STARTING ===", symbols)
    for s in symbols:
        t = threading.Thread(target=run_start_price_loop, args=(s, cfg), daemon=True)
        t.start()

    while True:
        time.sleep(5)