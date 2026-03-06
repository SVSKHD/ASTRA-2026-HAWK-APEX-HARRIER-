# strategy/astra_hawk.py
from __future__ import annotations

"""
AstraHawk — threshold normal entry + late entry strategy.

PURE DECISION MAKER — no MT5, no files, no persistence.

Decisions returned:
    WAIT
    ENTER_FIRST_LONG  / ENTER_FIRST_SHORT   — normal entry, price in 1x window
    ENTER_LATE_LONG   / ENTER_LATE_SHORT    — late entry, armed after jump-over
    EXIT_SECOND_LONG  / EXIT_SECOND_SHORT   — normal exit at 2x
    EXIT_LATE_LONG    / EXIT_LATE_SHORT     — late exit
    SKIP_JUMP_OVER_ENTRY                    — price jumped past entry window
    SKIP_DIRECT_TO_SECOND                   — price already at 2x on start
    SKIP_LATE_TOO_FAR                       — late armed but price ran past exit
    HALT_NOT_TRADEABLE                      — symbol disabled in config

Internal state (_ThrState):
    bias, crossed_1x, late_armed, window_hit counters, etc.
    Persisted by executor via build_state()/apply_state().

Position state (PositionInfo):
    in_trade, side, entry_price, daily_done, trades_today
    Owned by executor — strategy reads it, never writes it.
"""

from typing import Any, Dict, Optional

from .base import BaseStrategy, StrategyResult, PricePacket, PositionInfo


# ---------------------------------------------------------------------------
# Internal threshold tracking state (strategy-owned, in memory)
# ---------------------------------------------------------------------------

class _ThrState:
    """
    Tracks bias direction, 1x crossing, late arm, window hits.
    This is STRATEGY-INTERNAL state — not position/trade state.
    """
    __slots__ = (
        "symbol", "start_price",
        "bias",
        "crossed_1x", "crossed_1x_time", "crossed_1x_bias",
        "late_armed", "late_disabled_for_day",
        "window_hit_long", "window_hit_short",
        "missed_jump_long", "missed_jump_short",
    )

    def __init__(self, symbol: str, start_price: float):
        self.symbol              = symbol
        self.start_price         = start_price
        self.bias                = "none"       # "long" | "short" | "none"
        self.crossed_1x          = False
        self.crossed_1x_time     = None
        self.crossed_1x_bias     = None
        self.late_armed          = False
        self.late_disabled_for_day = False
        self.window_hit_long     = 0
        self.window_hit_short    = 0
        self.missed_jump_long    = False
        self.missed_jump_short   = False

    def snapshot(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_snapshot(cls, d: Dict[str, Any]) -> _ThrState:
        o = cls(d.get("symbol", ""), float(d.get("start_price", 0.0)))
        for k in cls.__slots__:
            if k in d:
                setattr(o, k, d[k])
        return o

    def reset(self, new_start: float) -> None:
        sym = self.symbol
        self.__init__(sym, new_start)


# ---------------------------------------------------------------------------
# Level computation from SymbolConfig
# ---------------------------------------------------------------------------

def _levels(sc, start: float) -> Dict[str, float]:
    t   = float(sc.threshold) * float(sc.pip_size)
    em  = sc.entry_min_multiplier       # 1.0
    ex  = sc.entry_max_multiplier       # 1.25
    ca  = sc.close_multiplier           # 2.0

    # late entry derived from multipliers
    late_at_x     = ex                  # arm at entry_max → enter around 2x
    late_exit_min = ca * 1.45           # ~2.9
    late_exit_max = ca * 1.5            # 3.0
    late_rem      = 10.0 * sc.pip_size
    buf           = 2.0  * sc.pip_size

    return {
        # normal long
        "long_first":           start + t * em,
        "long_first_max":       start + t * ex,
        "long_second":          start + t * ca,
        "long_second_close":    start + t * ca - buf,
        # late long
        "long_late_entry":      start + t * late_at_x,
        "long_late_entry_max":  start + t * late_exit_max - late_rem,
        "long_late_exit_min":   start + t * late_exit_min,
        # normal short
        "short_first":          start - t * em,
        "short_first_min":      start - t * ex,
        "short_second":         start - t * ca,
        "short_second_close":   start - t * ca + buf,
        # late short
        "short_late_entry":     start - t * late_at_x,
        "short_late_entry_min": start - t * late_exit_max + late_rem,
        "short_late_exit_min":  start - t * late_exit_min,
    }


def _x_values(start, current, high, low, pip_size, threshold):
    """Compute x-multiples of threshold for current price and extremes."""
    if pip_size <= 0 or threshold <= 0:
        return None, None, None, None
    x_up_now = max(0.0, (current - start) / pip_size) / threshold
    x_dn_now = max(0.0, (start - current) / pip_size) / threshold
    probe_up = max(current, high) if high is not None else current
    probe_dn = min(current, low)  if low  is not None else current
    x_up_ext = (probe_up - start) / pip_size / threshold
    x_dn_ext = (start - probe_dn) / pip_size / threshold
    return x_up_now, x_dn_now, x_up_ext, x_dn_ext


def _zone_id(date_mt5: str, bias: str) -> Optional[int]:
    if bias not in ("long", "short"):
        return None
    return int(date_mt5.replace("-", "")) * 10 + (1 if bias == "long" else 2)


# ---------------------------------------------------------------------------
# AstraHawkStrategy
# ---------------------------------------------------------------------------

class AstraHawkStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "astra_hawk"

    # ── lifecycle ────────────────────────────────────────────────────────

    def init(self, symbol: str, sc) -> None:
        super().init(symbol, sc)
        self._thr:  Optional[_ThrState] = None
        self._date: Optional[str]       = None

    def on_new_day(self, new_start_price: float) -> None:
        if self._thr:
            self._thr.reset(new_start_price)
        self._date = None

    def build_state(self) -> Dict[str, Any]:
        return {
            "date_mt5": self._date,
            "thr_state": self._thr.snapshot() if self._thr else {},
        }

    def apply_state(self, data: Dict[str, Any]) -> None:
        try:
            self._date = data.get("date_mt5")
            thr_d = data.get("thr_state")
            if thr_d and isinstance(thr_d, dict) and thr_d.get("start_price"):
                self._thr = _ThrState.from_snapshot(thr_d)
        except Exception as e:
            print(f"[{self.name}:{getattr(self, 'symbol', '?')}] apply_state failed ({e!r}) — fresh start")
            self._thr  = None
            self._date = None

    # ── main tick ────────────────────────────────────────────────────────

    def on_tick(self, pkt: PricePacket, pos: PositionInfo) -> StrategyResult:
        sc = self.sc

        # ── initialise on first packet ───────────────────────────────────
        if self._thr is None:
            self._thr = _ThrState(self.symbol, pkt.start_price)

        # date change (safety net — executor calls on_new_day, but guard)
        if self._date is None:
            self._date = pkt.date_mt5
        elif pkt.date_mt5 != self._date:
            self._thr.reset(pkt.start_price)
            self._date = pkt.date_mt5

        thr = self._thr

        # ── compute levels ───────────────────────────────────────────────
        lvl = _levels(sc, thr.start_price)

        # ── x values ────────────────────────────────────────────────────
        x_up_now, x_dn_now, x_up_ext, x_dn_ext = _x_values(
            thr.start_price, pkt.mid, pkt.high, pkt.low,
            sc.pip_size, sc.threshold,
        )

        probe_up = max(pkt.mid, pkt.high) if pkt.high is not None else pkt.mid
        probe_dn = min(pkt.mid, pkt.low)  if pkt.low  is not None else pkt.mid

        thr_price = float(sc.threshold) * float(sc.pip_size)
        late_tol  = sc.pip_size * 3.0

        # x relative to current bias direction
        if thr.bias == "long":
            x_now = x_up_ext
        elif thr.bias == "short":
            x_now = x_dn_ext
        else:
            x_now = max(x_up_ext or 0.0, x_dn_ext or 0.0) or None

        zone_id = _zone_id(pkt.date_mt5, thr.bias)

        # ── reclaim detection ────────────────────────────────────────────
        reclaim = thr_price * 0.10
        if reclaim > 0 and abs(pkt.mid - thr.start_price) <= reclaim:
            if not thr.late_disabled_for_day:
                thr.late_disabled_for_day = True
                thr.late_armed            = False

        # ── bias update (pre-1x, not frozen) ────────────────────────────
        bias_frozen = thr.late_armed or pos.in_trade
        if not thr.crossed_1x and not bias_frozen:
            move  = abs(pkt.mid - thr.start_price)
            min_m = thr_price * 0.25
            tent  = ("long"  if pkt.mid > thr.start_price else
                     "short" if pkt.mid < thr.start_price else "none")
            if tent != "none" and move >= min_m:
                thr.bias = tent
            elif thr.bias == "none" and x_up_now and x_dn_now:
                if x_up_now > 0.5 and x_up_now >= x_dn_now:
                    thr.bias = "long"
                elif x_dn_now > 0.5 and x_dn_now > x_up_now:
                    thr.bias = "short"

        zone_id = _zone_id(pkt.date_mt5, thr.bias)

        # ── 1x crossing ─────────────────────────────────────────────────
        crossed_1x_now = False
        if not thr.crossed_1x:
            if thr.bias == "long" and probe_up >= lvl["long_first"]:
                thr.crossed_1x      = True
                thr.crossed_1x_time = pkt.server_time
                thr.crossed_1x_bias = "long"
                crossed_1x_now      = True
            elif thr.bias == "short" and probe_dn <= lvl["short_first"]:
                thr.crossed_1x      = True
                thr.crossed_1x_time = pkt.server_time
                thr.crossed_1x_bias = "short"
                crossed_1x_now      = True

        # window hit counters
        if lvl["long_first"] <= pkt.mid <= lvl["long_first_max"]:
            thr.window_hit_long += 1
        if lvl["short_first_min"] <= pkt.mid <= lvl["short_first"]:
            thr.window_hit_short += 1

        # ── telemetry ────────────────────────────────────────────────────
        tel: Dict[str, Any] = {
            "bias":             thr.bias,
            "in_trade":         pos.in_trade,
            "side":             pos.side,
            "entry_mode":       pos.entry_mode,
            "daily_done":       pos.daily_done,
            "trades_today":     pos.trades_today,
            "crossed_1x":       thr.crossed_1x,
            "crossed_1x_now":   crossed_1x_now,
            "crossed_1x_time":  thr.crossed_1x_time,
            "crossed_1x_bias":  thr.crossed_1x_bias,
            "late_armed":       thr.late_armed,
            "late_disabled":    thr.late_disabled_for_day,
            "x_up_current":     x_up_now,
            "x_dn_current":     x_dn_now,
            "x_up_extreme":     x_up_ext,
            "x_dn_extreme":     x_dn_ext,
            "x_now":            x_now,
            "probe_up":         probe_up,
            "probe_dn":         probe_dn,
            "current":          pkt.mid,
            "start_price":      thr.start_price,
            "window_hit_long":  thr.window_hit_long,
            "window_hit_short": thr.window_hit_short,
        }

        # ── result builder ───────────────────────────────────────────────
        def _res(decision: str, action: str,
                 did_signal: bool = False, **kw) -> StrategyResult:
            return StrategyResult(
                strategy   = self.name,
                symbol     = self.symbol,
                decision   = decision,
                action     = action,
                did_signal = did_signal,
                in_trade   = pos.in_trade,
                daily_done = pos.daily_done,
                zone_id    = zone_id,
                now_iso    = pkt.server_time,
                telemetry  = {**tel, "miss_reason": kw.pop("miss_reason", "none"),
                              "decision": decision},
                **kw,
            )

        # ── guards ──────────────────────────────────────────────────────
        if not sc.is_trading_enabled:
            return _res("HALT_NOT_TRADEABLE", "halt_not_tradeable",
                        miss_reason="not_tradeable")

        if pos.daily_done and not pos.in_trade:
            return _res("WAIT", "blocked_daily_done",
                        miss_reason="daily_done")

        if pos.trades_today >= sc.max_trades_per_day and not pos.in_trade:
            return _res("WAIT", "blocked_max_trades",
                        miss_reason=f"max_trades={sc.max_trades_per_day}")

        # ================================================================
        # NOT IN TRADE — entry logic
        # ================================================================
        if not pos.in_trade:

            # already past 2x?
            if thr.bias == "long" and probe_up >= lvl["long_second_close"]:
                return _res("SKIP_DIRECT_TO_SECOND", "skip",
                            miss_reason="direct_to_second")
            if thr.bias == "short" and probe_dn <= lvl["short_second_close"]:
                return _res("SKIP_DIRECT_TO_SECOND", "skip",
                            miss_reason="direct_to_second")

            # ── LATE ENTRY ───────────────────────────────────────────────
            late_entry_at_x = sc.entry_max_multiplier
            late_exit_min_x = sc.close_multiplier * 1.45
            late_exit_max_x = sc.close_multiplier * 1.5

            if (
                sc.is_trading_enabled
                and thr.late_armed
                and not thr.late_disabled_for_day
                and x_now is not None
            ):
                if x_now >= late_exit_max_x:
                    thr.late_armed = False
                else:
                    if thr.bias == "long":
                        if late_entry_at_x <= x_now < late_exit_min_x:
                            lo = lvl["long_late_entry"] - late_tol
                            hi = lvl["long_late_entry_max"]
                            if lo <= pkt.mid <= hi:
                                thr.late_armed = False
                                return _res(
                                    "ENTER_LATE_LONG", "entered",
                                    did_signal=True, side="buy",
                                    entry_price=pkt.mid, entry_mode="late")
                            miss = "late_insufficient_room" if pkt.mid > hi else "late_price_not_reached"
                            return _res("WAIT", "waiting", miss_reason=miss)
                        elif x_now >= late_exit_max_x:
                            thr.late_armed = False
                            return _res("SKIP_LATE_TOO_FAR", "skip",
                                        miss_reason="late_too_far")

                    elif thr.bias == "short":
                        if late_entry_at_x <= x_now < late_exit_min_x:
                            lo = lvl["short_late_entry_min"]
                            hi = lvl["short_late_entry"] + late_tol
                            if lo <= pkt.mid <= hi:
                                thr.late_armed = False
                                return _res(
                                    "ENTER_LATE_SHORT", "entered",
                                    did_signal=True, side="sell",
                                    entry_price=pkt.mid, entry_mode="late")
                            miss = "late_insufficient_room" if pkt.mid < lo else "late_price_not_reached"
                            return _res("WAIT", "waiting", miss_reason=miss)
                        elif x_now >= late_exit_max_x:
                            thr.late_armed = False
                            return _res("SKIP_LATE_TOO_FAR", "skip",
                                        miss_reason="late_too_far")

            # ── NORMAL ENTRY ─────────────────────────────────────────────
            em = sc.entry_min_multiplier
            ex = sc.entry_max_multiplier
            ca = sc.close_multiplier

            if thr.bias == "long" and x_up_ext is not None:
                if em <= x_up_ext <= ex:
                    return _res(
                        "ENTER_FIRST_LONG", "entered",
                        did_signal=True, side="buy",
                        entry_price=pkt.mid, entry_mode="normal")
                elif ex < x_up_ext < ca:
                    thr.late_armed = not thr.late_disabled_for_day
                    return _res("SKIP_JUMP_OVER_ENTRY", "skip",
                                miss_reason="jumped_over_entry_window")
                elif x_up_ext >= ca:
                    thr.late_armed = (not thr.late_disabled_for_day
                                      and x_up_ext < late_exit_max_x)
                    return _res("SKIP_DIRECT_TO_SECOND", "skip",
                                miss_reason="direct_to_second")
                else:
                    return _res("WAIT", "waiting",
                                miss_reason="not_in_entry_window")

            elif thr.bias == "short" and x_dn_ext is not None:
                if em <= x_dn_ext <= ex:
                    return _res(
                        "ENTER_FIRST_SHORT", "entered",
                        did_signal=True, side="sell",
                        entry_price=pkt.mid, entry_mode="normal")
                elif ex < x_dn_ext < ca:
                    thr.late_armed = not thr.late_disabled_for_day
                    return _res("SKIP_JUMP_OVER_ENTRY", "skip",
                                miss_reason="jumped_over_entry_window")
                elif x_dn_ext >= ca:
                    thr.late_armed = (not thr.late_disabled_for_day
                                      and x_dn_ext < late_exit_max_x)
                    return _res("SKIP_DIRECT_TO_SECOND", "skip",
                                miss_reason="direct_to_second")
                else:
                    return _res("WAIT", "waiting",
                                miss_reason="not_in_entry_window")

            return _res("WAIT", "waiting", miss_reason="bias_not_set")

        # ================================================================
        # IN TRADE — exit logic
        # ================================================================
        if pos.side == "buy":
            if pos.entry_mode == "late":
                if probe_up >= lvl["long_late_exit_min"]:
                    return _res("EXIT_LATE_LONG", "exited",
                                did_signal=True, side="buy",
                                entry_price=pos.entry_price,
                                exit_price=pkt.mid)
            else:
                if probe_up >= lvl["long_second_close"]:
                    return _res("EXIT_SECOND_LONG", "exited",
                                did_signal=True, side="buy",
                                entry_price=pos.entry_price,
                                exit_price=pkt.mid)

        elif pos.side == "sell":
            if pos.entry_mode == "late":
                if probe_dn <= lvl["short_late_exit_min"]:
                    return _res("EXIT_LATE_SHORT", "exited",
                                did_signal=True, side="sell",
                                entry_price=pos.entry_price,
                                exit_price=pkt.mid)
            else:
                if probe_dn <= lvl["short_second_close"]:
                    return _res("EXIT_SECOND_SHORT", "exited",
                                did_signal=True, side="sell",
                                entry_price=pos.entry_price,
                                exit_price=pkt.mid)

        return _res("WAIT", "holding", miss_reason="waiting_for_exit_level")