from __future__ import annotations

from typing import Any, Dict, Optional

from .base import BaseStrategy, StrategyResult, PricePacket, PositionInfo


EPS = 1e-9


class _ThrState:
    __slots__ = (
        "symbol", "start_price",
        "present_direction",
        "candidate_direction",
        "committed_direction",
        "direction_committed_at",
        "crossed_1x", "crossed_1x_time", "crossed_1x_bias",
        "late_armed", "late_disabled_for_day",
        "window_hit_long", "window_hit_short",
        "missed_jump_long", "missed_jump_short",
    )

    def __init__(self, symbol: str, start_price: float):
        self.symbol = symbol
        self.start_price = start_price

        self.present_direction = "none"
        self.candidate_direction = "none"
        self.committed_direction = "none"
        self.direction_committed_at = None

        self.crossed_1x = False
        self.crossed_1x_time = None
        self.crossed_1x_bias = None

        self.late_armed = False
        self.late_disabled_for_day = False

        self.window_hit_long = 0
        self.window_hit_short = 0

        self.missed_jump_long = False
        self.missed_jump_short = False

    def snapshot(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_snapshot(cls, d: Dict[str, Any]) -> "_ThrState":
        o = cls(d.get("symbol", ""), float(d.get("start_price", 0.0)))
        for k in cls.__slots__:
            if k in d:
                setattr(o, k, d[k])
        return o

    def reset(self, new_start: float) -> None:
        sym = self.symbol
        self.__init__(sym, new_start)


def _levels(sc, start: float) -> Dict[str, float]:
    t = float(sc.threshold) * float(sc.pip_size)
    em = float(sc.entry_min_multiplier)
    ex = float(sc.entry_max_multiplier)
    ca = float(sc.close_multiplier)

    late_at_x = ex
    late_exit_min = ca * 1.45
    late_exit_max = ca * 1.5
    late_rem = 10.0 * float(sc.pip_size)
    buf = 2.0 * float(sc.pip_size)

    return {
        "long_first": start + t * em,
        "long_first_max": start + t * ex,
        "long_second": start + t * ca,
        "long_second_close": start + t * ca - buf,

        "long_late_entry": start + t * late_at_x,
        "long_late_entry_max": start + t * late_exit_max - late_rem,
        "long_late_exit_min": start + t * late_exit_min,

        "short_first": start - t * em,
        "short_first_min": start - t * ex,
        "short_second": start - t * ca,
        "short_second_close": start - t * ca + buf,

        "short_late_entry": start - t * late_at_x,
        "short_late_entry_min": start - t * late_exit_max + late_rem,
        "short_late_exit_min": start - t * late_exit_min,
    }


def _x_values(start, current, high, low, pip_size, threshold):
    if pip_size <= 0 or threshold <= 0:
        return None, None, None, None

    x_up_now = max(0.0, (current - start) / pip_size) / threshold
    x_dn_now = max(0.0, (start - current) / pip_size) / threshold

    probe_up = max(current, high) if high is not None else current
    probe_dn = min(current, low) if low is not None else current

    x_up_ext = max(0.0, (probe_up - start) / pip_size) / threshold
    x_dn_ext = max(0.0, (start - probe_dn) / pip_size) / threshold
    return x_up_now, x_dn_now, x_up_ext, x_dn_ext


def _zone_id(date_mt5: str, direction: str) -> Optional[int]:
    if direction not in ("long", "short"):
        return None
    return int(date_mt5.replace("-", "")) * 10 + (1 if direction == "long" else 2)

def _exit_targets_from_entry(lvl: Dict[str, float], pos: PositionInfo) -> Dict[str, Optional[float]]:
    """
    Convert threshold-based intended move into actual-entry-based exit targets.
    Entry is decided by threshold logic, but exit is anchored to real fill price.
    """
    if pos.entry_price is None:
        return {
            "normal_long_tp": None,
            "normal_short_tp": None,
            "late_long_tp": None,
            "late_short_tp": None,
        }

    # Intended moves from the threshold ladder
    normal_long_move = float(lvl["long_second_close"]) - float(lvl["long_first"])
    normal_short_move = float(lvl["short_first"]) - float(lvl["short_second_close"])

    late_long_move = float(lvl["long_late_exit_min"]) - float(lvl["long_late_entry"])
    late_short_move = float(lvl["short_late_entry"]) - float(lvl["short_late_exit_min"])

    ep = float(pos.entry_price)

    return {
        "normal_long_tp": ep + normal_long_move,
        "normal_short_tp": ep - normal_short_move,
        "late_long_tp": ep + late_long_move,
        "late_short_tp": ep - late_short_move,
    }

class AstraHawkStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "astra_hawk"

    def init(self, symbol: str, sc) -> None:
        super().init(symbol, sc)
        self._thr: Optional[_ThrState] = None
        self._date: Optional[str] = None

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
        except Exception:
            self._thr = None
            self._date = None

    def on_tick(self, pkt: PricePacket, pos: PositionInfo) -> StrategyResult:
        sc = self.sc

        if self._thr is None:
            self._thr = _ThrState(self.symbol, pkt.start_price)

        if self._date is None:
            self._date = pkt.date_mt5
        elif pkt.date_mt5 != self._date:
            self._thr.reset(pkt.start_price)
            self._date = pkt.date_mt5

        thr = self._thr
        lvl = _levels(sc, thr.start_price)
        exit_targets = _exit_targets_from_entry(lvl, pos)
        x_up_now, x_dn_now, x_up_ext, x_dn_ext = _x_values(
            thr.start_price, pkt.mid, pkt.high, pkt.low, sc.pip_size, sc.threshold
        )

        probe_up = max(pkt.mid, pkt.high) if pkt.high is not None else pkt.mid
        probe_dn = min(pkt.mid, pkt.low) if pkt.low is not None else pkt.mid

        thr_price = float(sc.threshold) * float(sc.pip_size)
        late_tol = float(sc.pip_size) * 3.0

        thr.present_direction = (
            "long" if pkt.mid > thr.start_price + EPS
            else "short" if pkt.mid < thr.start_price - EPS
            else "none"
        )

        if x_up_now is not None and x_dn_now is not None:
            if x_up_now > x_dn_now:
                thr.candidate_direction = "long"
            elif x_dn_now > x_up_now:
                thr.candidate_direction = "short"
            else:
                thr.candidate_direction = "none"

        reclaim = thr_price * 0.10
        if reclaim > 0 and abs(pkt.mid - thr.start_price) <= reclaim + EPS:
            if not thr.late_disabled_for_day:
                thr.late_disabled_for_day = True
                thr.late_armed = False

        long_touched = probe_up >= lvl["long_first"] - EPS
        short_touched = probe_dn <= lvl["short_first"] + EPS

        if thr.committed_direction == "none":
            if long_touched and not short_touched:
                thr.committed_direction = "long"
                thr.direction_committed_at = pkt.server_time
            elif short_touched and not long_touched:
                thr.committed_direction = "short"
                thr.direction_committed_at = pkt.server_time
            elif long_touched and short_touched:
                thr.committed_direction = "long" if (x_up_ext or 0.0) >= (x_dn_ext or 0.0) else "short"
                thr.direction_committed_at = pkt.server_time

        crossed_1x_now = False
        if not thr.crossed_1x and thr.committed_direction in ("long", "short"):
            thr.crossed_1x = True
            thr.crossed_1x_time = pkt.server_time
            thr.crossed_1x_bias = thr.committed_direction
            crossed_1x_now = True

        if lvl["long_first"] - EPS <= pkt.mid <= lvl["long_first_max"] + EPS:
            thr.window_hit_long += 1
        if lvl["short_first_min"] - EPS <= pkt.mid <= lvl["short_first"] + EPS:
            thr.window_hit_short += 1

        if thr.committed_direction == "long":
            x_now = x_up_ext
        elif thr.committed_direction == "short":
            x_now = x_dn_ext
        else:
            x_now = max(x_up_ext or 0.0, x_dn_ext or 0.0) or None

        zone_id = _zone_id(pkt.date_mt5, thr.committed_direction)

        opposite_blocked = False
        if thr.committed_direction == "long" and short_touched:
            opposite_blocked = True
        elif thr.committed_direction == "short" and long_touched:
            opposite_blocked = True

        def _telemetry(miss_reason: str, decision: str) -> Dict[str, Any]:
            return {
                "present_direction": thr.present_direction,
                "candidate_direction": thr.candidate_direction,
                "committed_direction": thr.committed_direction,
                "direction_committed_at": thr.direction_committed_at,
                "in_trade": pos.in_trade,
                "side": pos.side,
                "entry_mode": pos.entry_mode,
                "daily_done": pos.daily_done,
                "trades_today": pos.trades_today,
                "crossed_1x": thr.crossed_1x,
                "crossed_1x_now": crossed_1x_now,
                "crossed_1x_time": thr.crossed_1x_time,
                "crossed_1x_bias": thr.crossed_1x_bias,
                "late_armed": thr.late_armed,
                "late_disabled": thr.late_disabled_for_day,
                "x_up_current": x_up_now,
                "x_dn_current": x_dn_now,
                "x_up_extreme": x_up_ext,
                "x_dn_extreme": x_dn_ext,
                "x_now": x_now,
                "probe_up": probe_up,
                "probe_dn": probe_dn,
                "current": pkt.mid,
                "start_price": thr.start_price,
                "window_hit_long": thr.window_hit_long,
                "window_hit_short": thr.window_hit_short,
                "opposite_blocked": opposite_blocked,
                "miss_reason": miss_reason,
                "decision": decision,
            }

        def _res(decision: str, action: str, did_signal: bool = False, **kw) -> StrategyResult:
            miss_reason = kw.pop("miss_reason", "none")
            return StrategyResult(
                strategy=self.name,
                symbol=self.symbol,
                decision=decision,
                action=action,
                did_signal=did_signal,
                in_trade=pos.in_trade,
                daily_done=pos.daily_done,
                zone_id=zone_id,
                now_iso=pkt.server_time,
                telemetry=_telemetry(miss_reason, decision),
                **kw,
            )

        if not sc.is_trading_enabled:
            return _res("HALT_NOT_TRADEABLE", "halt_not_tradeable", miss_reason="not_tradeable")

        if pos.daily_done and not pos.in_trade:
            return _res("WAIT", "blocked_daily_done", miss_reason="daily_done")

        if pos.trades_today >= sc.max_trades_per_day and not pos.in_trade:
            return _res("WAIT", "blocked_max_trades", miss_reason=f"max_trades={sc.max_trades_per_day}")

        if not pos.in_trade and thr.committed_direction == "long" and short_touched:
            return _res("WAIT", "blocked_opposite_direction", miss_reason="opposite_direction_blocked")

        if not pos.in_trade and thr.committed_direction == "short" and long_touched:
            return _res("WAIT", "blocked_opposite_direction", miss_reason="opposite_direction_blocked")

        if not pos.in_trade:
            if thr.committed_direction == "long" and probe_up >= lvl["long_second_close"] - EPS:
                return _res("SKIP_DIRECT_TO_SECOND", "skip", miss_reason="direct_to_second")
            if thr.committed_direction == "short" and probe_dn <= lvl["short_second_close"] + EPS:
                return _res("SKIP_DIRECT_TO_SECOND", "skip", miss_reason="direct_to_second")

            late_entry_at_x = float(sc.entry_max_multiplier)
            late_exit_min_x = float(sc.close_multiplier) * 1.45
            late_exit_max_x = float(sc.close_multiplier) * 1.5

            if thr.late_armed and not thr.late_disabled_for_day and x_now is not None:
                if x_now >= late_exit_max_x - EPS:
                    thr.late_armed = False
                else:
                    if thr.committed_direction == "long":
                        if late_entry_at_x - EPS <= x_now < late_exit_min_x - EPS:
                            lo = lvl["long_late_entry"] - late_tol
                            hi = lvl["long_late_entry_max"]
                            if lo - EPS <= pkt.mid <= hi + EPS:
                                thr.late_armed = False
                                return _res(
                                    "ENTER_LATE_LONG", "entered",
                                    did_signal=True, side="buy",
                                    entry_price=pkt.mid, entry_mode="late",
                                )
                            miss = "late_insufficient_room" if pkt.mid > hi + EPS else "late_price_not_reached"
                            return _res("WAIT", "waiting", miss_reason=miss)

                    elif thr.committed_direction == "short":
                        if late_entry_at_x - EPS <= x_now < late_exit_min_x - EPS:
                            lo = lvl["short_late_entry_min"]
                            hi = lvl["short_late_entry"] + late_tol
                            if lo - EPS <= pkt.mid <= hi + EPS:
                                thr.late_armed = False
                                return _res(
                                    "ENTER_LATE_SHORT", "entered",
                                    did_signal=True, side="sell",
                                    entry_price=pkt.mid, entry_mode="late",
                                )
                            miss = "late_insufficient_room" if pkt.mid < lo - EPS else "late_price_not_reached"
                            return _res("WAIT", "waiting", miss_reason=miss)

            em = float(sc.entry_min_multiplier)
            ex = float(sc.entry_max_multiplier)
            ca = float(sc.close_multiplier)

            if thr.committed_direction == "long" and x_up_ext is not None:
                if em - EPS <= x_up_ext <= ex + EPS:
                    return _res(
                        "ENTER_FIRST_LONG", "entered",
                        did_signal=True, side="buy",
                        entry_price=pkt.mid, entry_mode="normal",
                    )
                if ex + EPS < x_up_ext < ca - EPS:
                    thr.late_armed = not thr.late_disabled_for_day
                    return _res("SKIP_JUMP_OVER_ENTRY", "skip", miss_reason="jumped_over_entry_window")
                if x_up_ext >= ca - EPS:
                    thr.late_armed = not thr.late_disabled_for_day and x_up_ext < late_exit_max_x - EPS
                    return _res("SKIP_DIRECT_TO_SECOND", "skip", miss_reason="direct_to_second")
                return _res("WAIT", "waiting", miss_reason="not_in_entry_window")

            if thr.committed_direction == "short" and x_dn_ext is not None:
                if em - EPS <= x_dn_ext <= ex + EPS:
                    return _res(
                        "ENTER_FIRST_SHORT", "entered",
                        did_signal=True, side="sell",
                        entry_price=pkt.mid, entry_mode="normal",
                    )
                if ex + EPS < x_dn_ext < ca - EPS:
                    thr.late_armed = not thr.late_disabled_for_day
                    return _res("SKIP_JUMP_OVER_ENTRY", "skip", miss_reason="jumped_over_entry_window")
                if x_dn_ext >= ca - EPS:
                    thr.late_armed = not thr.late_disabled_for_day and x_dn_ext < late_exit_max_x - EPS
                    return _res("SKIP_DIRECT_TO_SECOND", "skip", miss_reason="direct_to_second")
                return _res("WAIT", "waiting", miss_reason="not_in_entry_window")

            return _res("WAIT", "waiting", miss_reason="direction_not_committed")

        if pos.side == "buy":
            if pos.entry_mode == "late":
                tp = exit_targets["late_long_tp"]
                if tp is not None and probe_up >= tp - EPS:
                    return _res(
                        "EXIT_LATE_LONG", "exited",
                        did_signal=True, side="buy",
                        entry_price=pos.entry_price,
                        exit_price=pkt.mid,
                    )
            else:
                tp = exit_targets["normal_long_tp"]
                if tp is not None and probe_up >= tp - EPS:
                    return _res(
                        "EXIT_SECOND_LONG", "exited",
                        did_signal=True, side="buy",
                        entry_price=pos.entry_price,
                        exit_price=pkt.mid,
                    )

        elif pos.side == "sell":
            if pos.entry_mode == "late":
                tp = exit_targets["late_short_tp"]
                if tp is not None and probe_dn <= tp + EPS:
                    return _res(
                        "EXIT_LATE_SHORT", "exited",
                        did_signal=True, side="sell",
                        entry_price=pos.entry_price,
                        exit_price=pkt.mid,
                    )
            else:
                tp = exit_targets["normal_short_tp"]
                if tp is not None and probe_dn <= tp + EPS:
                    return _res(
                        "EXIT_SECOND_SHORT", "exited",
                        did_signal=True, side="sell",
                        entry_price=pos.entry_price,
                        exit_price=pkt.mid,
                    )

        return _res("WAIT", "holding", miss_reason="waiting_for_exit_level")