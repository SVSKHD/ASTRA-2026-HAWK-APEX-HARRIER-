from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
import os

from config.symbols import SYMBOLS
from strategy.base import StrategyResult
from .price_reader import PricePacket
from .trade import (
    calc_profit,
    close_all_positions_fok,
    get_positions_snapshot,
    get_realized_profit_since,
    place_market_order_fok,
)


DAILY_PROFIT_LOCK_USD = float(os.environ.get("DAILY_PROFIT_LOCK_USD", 50.0))
DAILY_MAX_LOSS_USD = float(os.environ.get("DAILY_MAX_LOSS_USD", -30.0))
CATASTROPHIC_LOSS_USD = float(os.environ.get("CATASTROPHIC_LOSS_USD", -75.0))

TRADE_RETCODE_DONE = 10009


@dataclass
class ExecResult:
    symbol: str
    strategy: str
    decision: str
    action: str
    mode: str

    did_trade: bool = False
    block_reason: Optional[str] = None

    side: Optional[str] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    entry_mode: Optional[str] = None

    profit_usd: float = 0.0
    realized_profit_usd: float = 0.0
    daily_done: bool = False

    zone_id: Optional[int] = None
    now_iso: str = ""
    telemetry: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineState:
    symbol: str
    strategy: str

    in_trade: bool = False
    side: Optional[str] = None
    entry_price: Optional[float] = None
    entry_time: Optional[str] = None
    entry_mode: Optional[str] = None

    daily_done: bool = False
    trades_today: int = 0
    realized_profit_usd: float = 0.0
    order_in_flight: bool = False
    last_date_mt5: Optional[str] = None

    def reset_position(self) -> None:
        self.in_trade = False
        self.side = None
        self.entry_price = None
        self.entry_time = None
        self.entry_mode = None

    def reset_daily(self) -> None:
        self.daily_done = False
        self.trades_today = 0
        self.realized_profit_usd = 0.0
        self.order_in_flight = False


def _safe_parse_iso(dt_text: Optional[str]) -> Optional[datetime]:
    if not dt_text:
        return None
    try:
        dt = datetime.fromisoformat(dt_text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _place_order(symbol: str, side: str, comment: str) -> Dict[str, Any]:
    sc = SYMBOLS.get(symbol)
    return place_market_order_fok(
        symbol=symbol,
        side=side,
        lot=sc.lot_size if sc else None,
        comment=comment,
    )


def _close_positions(symbol: str, comment: str) -> Dict[str, Any]:
    return close_all_positions_fok(symbol=symbol, comment=comment)


def _get_floating_pnl(symbol: str) -> float:
    snap = get_positions_snapshot(symbol)
    return float(snap.get("total_profit_usd") or 0.0)


def _get_realized_pnl(symbol: str, since_iso: Optional[str] = None) -> float:
    since_dt = _safe_parse_iso(since_iso)
    return float(get_realized_profit_since(symbol=symbol, since_dt=since_dt))


def _reconcile_live_position_state(eng: EngineState) -> None:
    snap = get_positions_snapshot(eng.symbol)
    positions = snap.get("positions") or []
    if not positions:
        eng.reset_position()
        return

    pos = positions[0]
    eng.in_trade = True
    eng.side = pos.get("type")
    eng.entry_price = float(pos.get("price_open") or 0.0)
    eng.entry_mode = eng.entry_mode or "normal"
    eng.entry_time = eng.entry_time or pos.get("time")


def _risk_gate(eng: EngineState) -> Tuple[bool, str]:
    realized = float(eng.realized_profit_usd)
    floating = _get_floating_pnl(eng.symbol)
    total = realized + floating

    if realized >= DAILY_PROFIT_LOCK_USD:
        return False, f"profit_lock realized={realized:.2f}>={DAILY_PROFIT_LOCK_USD}"
    if total <= CATASTROPHIC_LOSS_USD:
        return False, f"catastrophic_loss total={total:.2f}<={CATASTROPHIC_LOSS_USD}"
    if total <= DAILY_MAX_LOSS_USD:
        return False, f"daily_max_loss total={total:.2f}<={DAILY_MAX_LOSS_USD}"

    return True, "ok"


ENTRY_DECISIONS = frozenset({
    "ENTER_FIRST_LONG", "ENTER_FIRST_SHORT",
    "ENTER_LATE_LONG", "ENTER_LATE_SHORT",
})

EXIT_DECISIONS = frozenset({
    "EXIT_SECOND_LONG", "EXIT_SECOND_SHORT",
    "EXIT_LATE_LONG", "EXIT_LATE_SHORT",
})


def handle_signal(
    mode: str,          # ACTIVE | MONITOR_ONLY | BACKTEST
    eng: EngineState,   # executor-owned order state
    sig: StrategyResult,
    pkt: PricePacket,
) -> ExecResult:
    current = float(pkt.mid)
    sc = SYMBOLS.get(sig.symbol)
    now_iso = sig.now_iso or datetime.now(timezone.utc).isoformat()

    if mode == "ACTIVE":
        eng.realized_profit_usd = _get_realized_pnl(sig.symbol)
        _reconcile_live_position_state(eng)

    def _r(action: str, did_trade: bool = False,
           block_reason: Optional[str] = None, **kw) -> ExecResult:
        return ExecResult(
            symbol=sig.symbol,
            strategy=sig.strategy,
            decision=sig.decision,
            action=action,
            mode=mode,
            did_trade=did_trade,
            block_reason=block_reason,
            realized_profit_usd=eng.realized_profit_usd,
            daily_done=eng.daily_done,
            zone_id=sig.zone_id,
            now_iso=now_iso,
            telemetry=sig.telemetry or {},
            **kw,
        )

    if sc is None:
        return _r("blocked_unknown_symbol", block_reason=f"unknown_symbol={sig.symbol}")

    if sig.decision in ("WAIT", "HALT_NOT_TRADEABLE") or \
       sig.decision.startswith("SKIP_") or \
       sig.action in (
           "waiting", "holding", "skip", "halt_not_tradeable",
           "blocked_daily_done", "blocked_max_trades",
           "blocked_one_trade_per_day", "blocked_opposite_direction"
       ):
        return _r(sig.action or "none")

    if eng.daily_done and not eng.in_trade:
        return _r("blocked_daily_done", block_reason="daily_done")

    if mode not in ("ACTIVE", "MONITOR_ONLY", "BACKTEST"):
        return _r("blocked_unknown_mode", block_reason=f"mode={mode}")

    if sig.decision in ENTRY_DECISIONS:
        if eng.in_trade:
            return _r("skip_already_in_trade", block_reason="already_in_trade")

        if eng.order_in_flight:
            return _r("blocked_order_in_flight", block_reason="order_in_flight")

        allowed, reason = _risk_gate(eng)
        if not allowed:
            if "daily_max_loss" in reason or "catastrophic" in reason:
                return _force_close(mode, eng, sig.symbol, now_iso, _r, "ASTRA_RISK_FORCE_CLOSE_GATE")
            return _r("blocked_risk", block_reason=reason)

        side = sig.side or ("buy" if "LONG" in sig.decision else "sell")
        entry_mode = sig.entry_mode or ("late" if "LATE" in sig.decision else "normal")
        comment = f"ASTRA_HAWK_{'LATE' if entry_mode == 'late' else '1X'}"

        if mode == "MONITOR_ONLY":
            eng.in_trade = True
            eng.side = side
            eng.entry_price = current
            eng.entry_time = now_iso
            eng.entry_mode = entry_mode
            eng.trades_today += 1
            return _r(
                "monitor_only_entry",
                did_trade=False,
                side=side,
                entry_price=current,
                entry_mode=entry_mode,
            )

        if mode == "BACKTEST":
            eng.in_trade = True
            eng.side = side
            eng.entry_price = current
            eng.entry_time = now_iso
            eng.entry_mode = entry_mode
            eng.trades_today += 1
            return _r(
                "sim_opened",
                did_trade=True,
                side=side,
                entry_price=current,
                entry_mode=entry_mode,
            )

        eng.order_in_flight = True
        try:
            order = _place_order(sig.symbol, side, comment)
        except Exception as e:
            return _r("order_failed", block_reason=f"{type(e).__name__}:{e}")
        finally:
            eng.order_in_flight = False

        ret = order.get("retcode") if isinstance(order, dict) else None
        success = bool(order.get("success", ret == TRADE_RETCODE_DONE)) if isinstance(order, dict) else False
        if ret is None or int(ret) != TRADE_RETCODE_DONE or not success:
            reason = order.get("error") if isinstance(order, dict) else None
            return _r("order_rejected", block_reason=reason or f"retcode={ret}")

        confirmed = float(order.get("price") or current)
        eng.in_trade = True
        eng.side = side
        eng.entry_price = confirmed
        eng.entry_time = now_iso
        eng.entry_mode = entry_mode
        eng.trades_today += 1

        return _r(
            "trade_opened",
            did_trade=True,
            side=side,
            entry_price=confirmed,
            entry_mode=entry_mode,
        )

    if sig.decision in EXIT_DECISIONS:
        if not eng.in_trade:
            return _r("skip_not_in_trade", block_reason="not_in_trade")

        side_before = eng.side
        entry_before = eng.entry_price

        if mode == "MONITOR_ONLY":
            pnl = _sim_pnl(sig.symbol, side_before, entry_before, current)
            eng.realized_profit_usd += pnl
            eng.reset_position()
            eng.daily_done = True
            return _r(
                "monitor_only_exit",
                did_trade=False,
                daily_done=True,
                profit_usd=pnl,
                realized_profit_usd=eng.realized_profit_usd,
                side=side_before,
                entry_price=entry_before,
                exit_price=current,
            )

        if mode == "BACKTEST":
            pnl = _sim_pnl(sig.symbol, side_before, entry_before, current)
            eng.realized_profit_usd += pnl
            eng.reset_position()
            eng.daily_done = True
            return _r(
                "sim_closed",
                did_trade=True,
                daily_done=True,
                profit_usd=pnl,
                realized_profit_usd=eng.realized_profit_usd,
                side=side_before,
                entry_price=entry_before,
                exit_price=current,
            )

        realized_before = float(eng.realized_profit_usd)
        try:
            close_result = _close_positions(sig.symbol, "ASTRA_HAWK_EXIT")
        except Exception as e:
            return _r("close_failed", block_reason=f"{type(e).__name__}:{e}")

        close_ret = close_result.get("retcode") if isinstance(close_result, dict) else None
        close_ok = bool(close_result.get("closed", False)) if isinstance(close_result, dict) else False
        if close_ret is None or int(close_ret) != TRADE_RETCODE_DONE or not close_ok:
            reason = close_result.get("error") if isinstance(close_result, dict) else None
            return _r("close_failed", block_reason=reason or f"retcode={close_ret}")

        realized_after = _get_realized_pnl(sig.symbol)
        trade_profit = float(realized_after - realized_before)
        eng.realized_profit_usd = float(realized_after)
        eng.reset_position()
        eng.daily_done = True

        return _r(
            "trade_closed",
            did_trade=True,
            daily_done=True,
            profit_usd=trade_profit,
            realized_profit_usd=float(realized_after),
            side=side_before,
            entry_price=entry_before,
            exit_price=current,
        )

    return _r("none")


def _force_close(mode, eng: EngineState, symbol, now_iso, _r, comment):
    if mode == "MONITOR_ONLY":
        eng.reset_position()
        eng.daily_done = True
        return _r("monitor_only_force_close", daily_done=True)

    if mode == "BACKTEST":
        eng.reset_position()
        eng.daily_done = True
        return _r("sim_forced_closed", did_trade=True, daily_done=True)

    realized_before = float(eng.realized_profit_usd)
    try:
        close_result = _close_positions(symbol, comment)
    except Exception as e:
        return _r("risk_force_close_failed", block_reason=f"close_exception:{e}")

    close_ret = close_result.get("retcode") if isinstance(close_result, dict) else None
    close_ok = bool(close_result.get("closed", False)) if isinstance(close_result, dict) else False
    if close_ret is None or int(close_ret) != TRADE_RETCODE_DONE or not close_ok:
        reason = close_result.get("error") if isinstance(close_result, dict) else None
        return _r("risk_force_close_failed", block_reason=reason or f"retcode={close_ret}")

    realized_after = _get_realized_pnl(symbol)
    eng.realized_profit_usd = float(realized_after)
    eng.reset_position()
    eng.daily_done = True
    return _r(
        "risk_forced_closed",
        did_trade=True,
        daily_done=True,
        profit_usd=float(realized_after - realized_before),
        realized_profit_usd=float(realized_after),
    )


def _sim_pnl(symbol: str, side, entry, current: float) -> float:
    if entry is None or side not in {"buy", "sell"}:
        return 0.0
    sc = SYMBOLS.get(symbol)
    if sc is None:
        return 0.0
    try:
        return round(float(calc_profit(symbol, side, sc.lot_size, float(entry), float(current))), 2)
    except Exception:
        if sc.pip_size <= 0:
            return 0.0
        pips = ((current - entry) if side == "buy" else (entry - current)) / sc.pip_size
        return round(pips * sc.lot_size * 10.0, 2)
