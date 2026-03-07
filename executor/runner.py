from __future__ import annotations

import time
from typing import Dict, Tuple

from config.symbols import SYMBOLS
from config.selectors import get_price_symbols, get_strategies_for_symbol
from strategy.base import PositionInfo
from strategy.loader import get_strategy

from .price_reader import read_price_packet
from .engine import EngineState, handle_signal


class ExecutorRunner:
    def __init__(self, mode: str = "MONITOR_ONLY", poll_seconds: float = 0.3):
        self.mode = mode
        self.poll_seconds = poll_seconds
        self._states: Dict[Tuple[str, str], EngineState] = {}
        self._strategies = {}

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

    def process_symbol_strategy(self, symbol: str, strategy_name: str):
        pkt = read_price_packet(symbol)
        if pkt is None:
            return None

        sc = SYMBOLS.get(symbol)
        if sc is None:
            return None

        strategy = self.get_strategy(symbol, strategy_name)
        eng = self.get_state(symbol, strategy_name)

        if eng.last_date_mt5 != pkt.date_mt5:
            eng.last_date_mt5 = pkt.date_mt5
            eng.reset_daily()
            strategy.on_new_day(pkt.start_price)

        pos = PositionInfo(
            in_trade=eng.in_trade,
            side=eng.side,
            entry_price=eng.entry_price,
            entry_time=eng.entry_time,
            entry_mode=eng.entry_mode,
            daily_done=eng.daily_done,
            trades_today=eng.trades_today,
        )

        sig = strategy.on_tick(pkt, pos)

        try:
            strategy_state = strategy.build_state()
            _ = strategy_state
        except Exception:
            pass

        result = handle_signal(self.mode, eng, sig, pkt)
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