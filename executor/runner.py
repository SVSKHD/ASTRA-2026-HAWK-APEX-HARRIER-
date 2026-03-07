from __future__ import annotations

import time
from typing import Dict, Tuple, Optional, Any

from config.symbols import SYMBOLS
from config.selectors import get_price_symbols, get_strategies_for_symbol
from strategy.base import PositionInfo
from strategy.loader import get_strategy

from .price_reader import read_price_packet
from .engine import EngineState, handle_signal
from .trade import get_positions_snapshot


class ExecutorRunner:
    def __init__(self, mode: str = "MONITOR_ONLY", poll_seconds: float = 0.3):
        self.mode = mode
        self.poll_seconds = poll_seconds
        self._states: Dict[Tuple[str, str], EngineState] = {}
        self._strategies = {}
        self._last_packet_epoch: Dict[Tuple[str, str], Optional[int]] = {}

    def get_state(self, symbol: str, strategy_name: str) -> EngineState:
        key = (symbol, strategy_name)
        if key not in self._states:
            self._states[key] = EngineState(symbol=symbol, strategy=strategy_name)
        return self._states[key]

    def get_strategy(self, symbol: str, strategy_name: str):
        key = (symbol, strategy_name)
        if key not in self._strategies:
            sc = SYMBOLS[symbol]
            strategy = get_strategy(strategy_name)
            strategy.init(symbol, sc)
            self._strategies[key] = strategy
        return self._strategies[key]

    def _log(self, level: str, symbol: str, strategy_name: str, message: str) -> None:
        print(f"[executor:{level}] [{symbol}:{strategy_name}] {message}")

    def _packet_epoch(self, pkt: Any) -> Optional[int]:
        for name in (
            "tick_epoch",
            "current_tick_epoch",
            "tick_time_epoch",
            "epoch",
            "ts_epoch",
        ):
            value = getattr(pkt, name, None)
            if value is None:
                continue
            try:
                return int(value)
            except Exception:
                continue
        return None

    def _is_packet_stale(self, pkt: Any) -> bool:
        for name in ("is_stale", "stale"):
            value = getattr(pkt, name, None)
            if isinstance(value, bool):
                return value

        for name in ("age_seconds", "tick_age_seconds", "packet_age_seconds"):
            value = getattr(pkt, name, None)
            if value is None:
                continue
            try:
                return float(value) > 5.0
            except Exception:
                continue
        return False

    def _reconcile_engine_with_broker(self, eng: EngineState) -> None:
        if self.mode != "ACTIVE":
            return

        try:
            snap = get_positions_snapshot(eng.symbol)
        except Exception as e:
            self._log("warn", eng.symbol, eng.strategy, f"broker_reconcile_failed: {type(e).__name__}: {e}")
            return

        positions = snap.get("positions") or []
        if not positions:
            eng.reset_position()
            return

        pos = positions[0]
        side = pos.get("type")
        if side not in {"buy", "sell"}:
            self._log("warn", eng.symbol, eng.strategy, f"unexpected_position_type={side!r}")
            return

        eng.in_trade = True
        eng.side = side
        try:
            eng.entry_price = float(pos.get("price_open") or 0.0)
        except Exception:
            eng.entry_price = eng.entry_price
        eng.entry_time = eng.entry_time or pos.get("time")
        eng.entry_mode = eng.entry_mode or "normal"

    def process_symbol_strategy(self, symbol: str, strategy_name: str):
        pkt = read_price_packet(symbol)
        if pkt is None:
            return None

        if self._is_packet_stale(pkt):
            self._log("info", symbol, strategy_name, "stale price packet skipped")
            return None

        sc = SYMBOLS.get(symbol)
        if sc is None:
            self._log("warn", symbol, strategy_name, "symbol config not found")
            return None

        strategy = self.get_strategy(symbol, strategy_name)
        eng = self.get_state(symbol, strategy_name)
        key = (symbol, strategy_name)

        packet_epoch = self._packet_epoch(pkt)
        if packet_epoch is not None and self._last_packet_epoch.get(key) == packet_epoch:
            return None
        self._last_packet_epoch[key] = packet_epoch

        self._reconcile_engine_with_broker(eng)

        if eng.last_date_mt5 != pkt.date_mt5:
            eng.last_date_mt5 = pkt.date_mt5
            eng.reset_daily()
            try:
                strategy.on_new_day(pkt.start_price)
            except Exception as e:
                self._log("error", symbol, strategy_name, f"on_new_day_failed: {type(e).__name__}: {e}")
                return None

        pos = PositionInfo(
            in_trade=eng.in_trade,
            side=eng.side,
            entry_price=eng.entry_price,
            entry_time=eng.entry_time,
            entry_mode=eng.entry_mode,
            daily_done=eng.daily_done,
            trades_today=eng.trades_today,
        )

        try:
            sig = strategy.on_tick(pkt, pos)
        except Exception as e:
            self._log("error", symbol, strategy_name, f"on_tick_failed: {type(e).__name__}: {e}")
            return None

        try:
            strategy.build_state()
        except Exception as e:
            self._log("warn", symbol, strategy_name, f"build_state_failed: {type(e).__name__}: {e}")

        try:
            result = handle_signal(self.mode, eng, sig, pkt)
        except Exception as e:
            self._log("error", symbol, strategy_name, f"handle_signal_failed: {type(e).__name__}: {e}")
            return None
        return result

    def run_once(self):
        results = []
        for symbol in get_price_symbols():
            for strategy_name in get_strategies_for_symbol(symbol):
                res = self.process_symbol_strategy(symbol, strategy_name)
                if res is not None:
                    results.append(res)
        return results

    def run_loop(self):
        while True:
            self.run_once()
            time.sleep(self.poll_seconds)


if __name__ == "__main__":
    runner = ExecutorRunner(mode="MONITOR_ONLY", poll_seconds=0.3)
    runner.run_loop()
