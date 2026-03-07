from __future__ import annotations

import time
from typing import Dict, Tuple, Optional, Any

from config.symbols import SYMBOLS
from config.selectors import get_price_symbols, get_strategies_for_symbol
from strategy.base import PositionInfo
from strategy.loader import get_strategy

from executor.price_reader import read_price_packet
from executor.engine import EngineState, handle_signal
from executor.trade import get_positions_snapshot


class ExecutorRunner:
    def __init__(self, mode: str = "MONITOR_ONLY", poll_seconds: float = 0.3):
        self.mode = mode
        self.poll_seconds = poll_seconds
        self._states: Dict[Tuple[str, str], EngineState] = {}
        self._strategies = {}
        self._last_packet_epoch: Dict[Tuple[str, str], Optional[int]] = {}
        self._last_heartbeat_ts: float = 0.0

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

    def _fmt_num(self, value: Any, decimals: int = 2) -> str:
        try:
            if value is None:
                return "None"
            return f"{float(value):.{decimals}f}"
        except Exception:
            return str(value)

    def _safe_get(self, obj: Any, *names: str, default=None):
        for name in names:
            try:
                value = getattr(obj, name, None)
            except Exception:
                value = None
            if value is not None:
                return value
        return default

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

    def _extract_pkt_fields(self, pkt: Any) -> dict:
        return {
            "current": self._safe_get(pkt, "current_price", "price", "current"),
            "start": self._safe_get(pkt, "start_price", "start"),
            "high": self._safe_get(pkt, "high_price", "high"),
            "low": self._safe_get(pkt, "low_price", "low"),
            "date_mt5": self._safe_get(pkt, "date_mt5"),
            "tick_epoch": self._packet_epoch(pkt),
            "stale": self._is_packet_stale(pkt),
        }

    def _extract_signal_view(self, sig: Any) -> tuple[str, str]:
        signal_name = None
        signal_reason = None

        for name in ("signal", "decision", "action", "name"):
            value = getattr(sig, name, None)
            if value is not None:
                signal_name = str(value)
                break

        for name in ("reason", "message", "note", "why"):
            value = getattr(sig, name, None)
            if value is not None:
                signal_reason = str(value)
                break

        if signal_name is None:
            signal_name = str(sig)

        if signal_reason is None:
            signal_reason = "-"

        return signal_name, signal_reason

    def _print_cycle_status(self, symbol: str, strategy_name: str, pkt: Any, sig: Any, result: Any) -> None:
        p = self._extract_pkt_fields(pkt)
        signal_name, signal_reason = self._extract_signal_view(sig)

        if result is None:
            print(
                f"{symbol:<8} | {strategy_name:<32} | "
                f"cur={self._fmt_num(p['current']):>10} | "
                f"start={self._fmt_num(p['start']):>10} | "
                f"high={self._fmt_num(p['high']):>10} | "
                f"low={self._fmt_num(p['low']):>10} | "
                f"stale={str(p['stale']):<5} | "
                f"signal={signal_name:<12} | "
                f"reason={signal_reason} | "
                f"executed=False"
            )
            return

        print(
            f"{symbol:<8} | {strategy_name:<32} | "
            f"cur={self._fmt_num(p['current']):>10} | "
            f"start={self._fmt_num(p['start']):>10} | "
            f"high={self._fmt_num(p['high']):>10} | "
            f"low={self._fmt_num(p['low']):>10} | "
            f"stale={str(p['stale']):<5} | "
            f"signal={signal_name:<12} | "
            f"decision={str(getattr(result, 'decision', None)):<10} | "
            f"action={str(getattr(result, 'action', None)):<10} | "
            f"did_trade={str(getattr(result, 'did_trade', None)):<5} | "
            f"side={str(getattr(result, 'side', None)):<5} | "
            f"entry={self._fmt_num(getattr(result, 'entry_price', None)):>10} | "
            f"exit={self._fmt_num(getattr(result, 'exit_price', None)):>10} | "
            f"mode={str(getattr(result, 'mode', None)):<12} | "
            f"block={str(getattr(result, 'block_reason', None))}"
        )

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
            pass
        eng.entry_time = eng.entry_time or pos.get("time")
        eng.entry_mode = eng.entry_mode or "normal"

    def process_symbol_strategy(self, symbol: str, strategy_name: str):
        pkt = read_price_packet(symbol)
        if pkt is None:
            print(f"{symbol:<8} | {strategy_name:<32} | packet=None")
            return None

        if self._is_packet_stale(pkt):
            p = self._extract_pkt_fields(pkt)
            print(
                f"{symbol:<8} | {strategy_name:<32} | "
                f"cur={self._fmt_num(p['current']):>10} | "
                f"start={self._fmt_num(p['start']):>10} | "
                f"high={self._fmt_num(p['high']):>10} | "
                f"low={self._fmt_num(p['low']):>10} | "
                f"stale=True | skipped"
            )
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

        self._print_cycle_status(symbol, strategy_name, pkt, sig, result)
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
            results = self.run_once()

            now = time.time()
            if now - self._last_heartbeat_ts >= 5:
                print(f"[runner] alive | mode={self.mode} | results_this_cycle={len(results)}")
                self._last_heartbeat_ts = now

            time.sleep(self.poll_seconds)


if __name__ == "__main__":
    # mode = "MONITOR_ONLY" | "ACTIVE"
    # change to ACTIVE only when intentionally live
    mode="ACTIVE"
    print(f"[BOOT] Starting ExecutorRunner | mode={mode}")
    if mode == "ACTIVE":
        print("[BOOT] LIVE TRADING ENABLED")
    runner = ExecutorRunner(mode=mode, poll_seconds=0.3)
    runner.run_loop()