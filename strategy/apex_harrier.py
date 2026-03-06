# strategy/apex_harrier.py
from __future__ import annotations

"""
ApexHarrier — XAUUSD-focused, start-price anchored strategy.

Fixed entry/exit levels, pip-based tolerances.
Placeholder — replace on_tick() logic with actual trading algorithm.

Decisions returned:
    WAIT
    ENTER_FIRST_LONG  / ENTER_FIRST_SHORT
    EXIT_SECOND_LONG  / EXIT_SECOND_SHORT
    HALT_NOT_TRADEABLE
"""

from typing import Any, Dict, Optional

from .base import BaseStrategy, StrategyResult, PricePacket, PositionInfo


class ApexHarrierStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "apex_harrier"

    def init(self, symbol: str, sc) -> None:
        super().init(symbol, sc)
        self._date: Optional[str] = None

    def on_new_day(self, new_start_price: float) -> None:
        self._date = None

    def build_state(self) -> Dict[str, Any]:
        return {"date_mt5": self._date}

    def apply_state(self, data: Dict[str, Any]) -> None:
        try:
            self._date = data.get("date_mt5")
        except Exception:
            self._date = None

    def on_tick(self, pkt: PricePacket, pos: PositionInfo) -> StrategyResult:
        sc = self.sc

        if self._date is None:
            self._date = pkt.date_mt5

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
                now_iso    = pkt.server_time,
                telemetry  = {"current": pkt.mid, "start_price": pkt.start_price,
                              "decision": decision,
                              "miss_reason": kw.pop("miss_reason", "none")},
                **kw,
            )

        if not sc.is_trading_enabled:
            return _res("HALT_NOT_TRADEABLE", "halt_not_tradeable")

        if pos.daily_done and not pos.in_trade:
            return _res("WAIT", "blocked_daily_done", miss_reason="daily_done")

        # placeholder — implement actual apex_harrier logic here
        return _res("WAIT", "waiting", miss_reason="not_implemented")