# executor/executor.py
from __future__ import annotations

"""
Main Executor — Orchestrates strategies, trading, and simulation.

Flow:
    1. Load symbols from config where is_enabled=True
    2. For each symbol, load active strategies from config toggles
    3. Read price packets from pricing module
    4. Run strategy.on_tick() → get StrategyResult
    5. If is_trading_enabled=True  → execute real trades via trade.py (ACTIVE)
    6. If is_trading_enabled=False → simulate profit via calc_profit (MONITOR)
    7. Log results, send alerts

Usage:
    python -m executor.executor --mode loop --interval 0.3
    python -m executor.executor --mode single
    python -m executor.executor --symbol XAUUSD --mode loop
"""

import os
import sys
import time
import json
import signal
import logging
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

# Ensure project root in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.symbols import SYMBOLS, SymbolConfig
from config.selectors import get_trading_symbols, get_price_symbols, get_strategies_for_symbol

from strategy.base import BaseStrategy, StrategyResult
from strategy.loader import load_strategy

from .price_reader import PricePacket, read_price_packet
from .trade import (
    place_market_order_fok,
    close_all_positions_fok,
    close_position_fok,
    get_positions_snapshot,
    get_realized_profit_since,
    calc_profit,
    health_check,
    shutdown as mt5_shutdown,
)

# Optional: notifications
try:
    from notify.discord import notify_discord

    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False


    def notify_discord(channel: str, msg: str) -> bool:
        return False

try:
    from notify.telegram import notify_telegram

    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False


    def notify_telegram(msg: str) -> bool:
        return False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("executor")
logger.setLevel(logging.INFO)

if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S"
    ))
    logger.addHandler(_h)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRADE_RETCODE_DONE = 10009

# Risk limits from environment
DAILY_PROFIT_LOCK_USD = float(os.environ.get("DAILY_PROFIT_LOCK_USD", 50.0))
DAILY_MAX_LOSS_USD = float(os.environ.get("DAILY_MAX_LOSS_USD", -30.0))
CATASTROPHIC_LOSS_USD = float(os.environ.get("CATASTROPHIC_LOSS_USD", -75.0))


# ---------------------------------------------------------------------------
# Execution State (per symbol+strategy pair)
# ---------------------------------------------------------------------------

@dataclass
class ExecutionState:
    """
    Tracks execution state for a symbol+strategy pair.
    Owned by executor, not strategy.
    """
    symbol: str
    strategy: str

    # Position state
    in_trade: bool = False
    side: Optional[str] = None
    entry_price: Optional[float] = None
    entry_time: Optional[str] = None
    ticket: Optional[int] = None

    # Daily tracking
    daily_done: bool = False
    trades_today: int = 0
    realized_profit_usd: float = 0.0

    # Order state
    order_in_flight: bool = False

    # Date tracking for reset
    last_date_mt5: Optional[str] = None

    def reset_daily(self):
        """Reset daily counters (called on new MT5 day)."""
        self.daily_done = False
        self.trades_today = 0
        self.realized_profit_usd = 0.0

    def reset_position(self):
        """Reset position state after close."""
        self.in_trade = False
        self.side = None
        self.entry_price = None
        self.entry_time = None
        self.ticket = None


# ---------------------------------------------------------------------------
# Execution Result
# ---------------------------------------------------------------------------

@dataclass
class ExecResult:
    """Result of executing a strategy signal."""
    symbol: str
    strategy: str
    decision: str
    action: str  # trade_opened | trade_closed | simulated_entry | simulated_exit | blocked_* | waiting
    mode: str  # ACTIVE | MONITOR

    did_trade: bool = False
    did_simulate: bool = False

    side: Optional[str] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None

    profit_usd: float = 0.0
    realized_profit_usd: float = 0.0

    ticket: Optional[int] = None
    block_reason: Optional[str] = None
    error: Optional[str] = None

    timestamp: str = ""
    telemetry: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "decision": self.decision,
            "action": self.action,
            "mode": self.mode,
            "did_trade": self.did_trade,
            "did_simulate": self.did_simulate,
            "side": self.side,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "profit_usd": self.profit_usd,
            "realized_profit_usd": self.realized_profit_usd,
            "ticket": self.ticket,
            "block_reason": self.block_reason,
            "error": self.error,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Strategy Registry
# ---------------------------------------------------------------------------

class StrategyRegistry:
    """
    Maps strategy names to strategy instances.
    Loads strategies based on config toggles.
    """

    _instances: Dict[Tuple[str, str], BaseStrategy] = {}

    @classmethod
    def get_or_create(
            cls,
            symbol: str,
            strategy_name: str,
            sc: SymbolConfig,
            base_dir: str = "data",
    ) -> Optional[BaseStrategy]:
        """
        Get existing strategy instance or create new one.
        Returns None if strategy not found.
        """
        key = (symbol, strategy_name)

        if key in cls._instances:
            return cls._instances[key]

        # Load strategy class
        try:
            strategy = load_strategy(strategy_name)
            if strategy is None:
                logger.error(f"Strategy '{strategy_name}' not found in loader")
                return None

            # Initialize
            strategy.init(symbol, sc, base_dir)
            strategy.restore()

            cls._instances[key] = strategy
            logger.info(f"Loaded strategy: {strategy_name} for {symbol}")

            return strategy

        except Exception as e:
            logger.error(f"Failed to load strategy {strategy_name}: {e}")
            return None

    @classmethod
    def get_all_for_symbol(cls, symbol: str) -> List[BaseStrategy]:
        """Get all loaded strategies for a symbol."""
        return [
            s for (sym, _), s in cls._instances.items()
            if sym == symbol
        ]

    @classmethod
    def persist_all(cls):
        """Persist all strategy states."""
        for strategy in cls._instances.values():
            try:
                strategy.persist()
            except Exception as e:
                logger.error(f"Failed to persist {strategy.name}: {e}")


# ---------------------------------------------------------------------------
# Risk Gate
# ---------------------------------------------------------------------------

def check_risk_gate(state: ExecutionState, symbol: str) -> Tuple[bool, str]:
    """
    Check if trading is allowed based on risk limits.
    Returns (allowed, reason).
    """
    # Get current P&L
    floating = 0.0
    snap = get_positions_snapshot(symbol=symbol)
    if snap:
        floating = snap.get("total_profit_usd", 0.0)

    realized = state.realized_profit_usd
    total = realized + floating

    # Check limits
    if realized >= DAILY_PROFIT_LOCK_USD:
        return False, f"profit_lock: realized={realized:.2f} >= {DAILY_PROFIT_LOCK_USD}"

    if total <= DAILY_MAX_LOSS_USD:
        return False, f"daily_max_loss: total={total:.2f} <= {DAILY_MAX_LOSS_USD}"

    if total <= CATASTROPHIC_LOSS_USD:
        return False, f"catastrophic_loss: total={total:.2f} <= {CATASTROPHIC_LOSS_USD}"

    return True, "ok"


# ---------------------------------------------------------------------------
# Signal Handler — Entry/Exit Logic
# ---------------------------------------------------------------------------

ENTRY_DECISIONS = frozenset({
    "ENTER_FIRST_LONG", "ENTER_FIRST_SHORT",
    "ENTER_LATE_LONG", "ENTER_LATE_SHORT",
})

EXIT_DECISIONS = frozenset({
    "EXIT_SECOND_LONG", "EXIT_SECOND_SHORT",
    "EXIT_LATE_LONG", "EXIT_LATE_SHORT",
})


def handle_signal(
        sc: SymbolConfig,
        state: ExecutionState,
        sig: StrategyResult,
        pkt: PricePacket,
) -> ExecResult:
    """
    Handle strategy signal — execute trade or simulate.

    Mode is determined by sc.is_trading_enabled:
        True  → ACTIVE (real trades via trade.py)
        False → MONITOR (simulate via calc_profit)
    """
    mode = "ACTIVE" if sc.is_trading_enabled else "MONITOR"
    current_price = pkt.mid
    now_iso = datetime.now(timezone.utc).isoformat()

    def _result(action: str, **kwargs) -> ExecResult:
        return ExecResult(
            symbol=sig.symbol,
            strategy=sig.strategy,
            decision=sig.decision,
            action=action,
            mode=mode,
            realized_profit_usd=state.realized_profit_usd,
            timestamp=now_iso,
            telemetry=sig.telemetry or {},
            **kwargs,
        )

    # ── Pass-through non-actionable decisions ─────────────────────────────
    if sig.decision == "WAIT":
        return _result("waiting")

    if sig.decision == "HALT_NOT_TRADEABLE":
        return _result("halted", block_reason="not_tradeable")

    if sig.decision.startswith("SKIP_"):
        return _result("skipped", block_reason=sig.decision)

    # ── Check guards ──────────────────────────────────────────────────────
    if state.daily_done and not state.in_trade:
        return _result("blocked_daily_done", block_reason="daily_done")

    if state.trades_today >= sc.max_trades_per_day and not state.in_trade:
        return _result("blocked_max_trades", block_reason="max_trades_per_day")

    # ========================================================================
    # ENTRY
    # ========================================================================
    if sig.decision in ENTRY_DECISIONS:

        if state.in_trade:
            return _result("skip_already_in_trade", block_reason="already_in_trade")

        if state.order_in_flight:
            return _result("blocked_order_in_flight", block_reason="order_in_flight")

        # Risk gate
        allowed, reason = check_risk_gate(state, sig.symbol)
        if not allowed:
            return _result("blocked_risk", block_reason=reason)

        # Determine side and entry mode
        side = "buy" if "LONG" in sig.decision else "sell"
        entry_mode = "late" if "LATE" in sig.decision else "normal"
        comment = f"{sig.strategy.upper()}_{entry_mode.upper()}"

        # ── ACTIVE MODE (real trade) ─────────────────────────────────────
        if mode == "ACTIVE":
            state.order_in_flight = True

            try:
                result = place_market_order_fok(
                    symbol=sig.symbol,
                    side=side,
                    lot=sc.lot_size,
                    comment=comment,
                )
            except Exception as e:
                state.order_in_flight = False
                return _result("order_exception", error=str(e))
            finally:
                state.order_in_flight = False

            if not result.get("success"):
                return _result(
                    "order_rejected",
                    error=result.get("error"),
                    block_reason=f"retcode={result.get('retcode')}",
                )

            # Update state
            confirmed_price = result.get("price", current_price)
            state.in_trade = True
            state.side = side
            state.entry_price = confirmed_price
            state.entry_time = now_iso
            state.ticket = result.get("ticket")
            state.trades_today += 1

            logger.info(
                f"✅ [{sig.symbol}] {side.upper()} opened @ {confirmed_price:.2f} | "
                f"ticket={state.ticket}"
            )

            # Notify
            _notify_trade_open(sig.symbol, side, confirmed_price, sig.strategy)

            return _result(
                "trade_opened",
                did_trade=True,
                side=side,
                entry_price=confirmed_price,
                ticket=state.ticket,
            )

        # ── MONITOR MODE (simulate entry) ─────────────────────────────────
        else:
            state.in_trade = True
            state.side = side
            state.entry_price = current_price
            state.entry_time = now_iso
            state.trades_today += 1

            logger.info(
                f"📊 [{sig.symbol}] SIM {side.upper()} entry @ {current_price:.2f}"
            )

            return _result(
                "simulated_entry",
                did_simulate=True,
                side=side,
                entry_price=current_price,
            )

    # ========================================================================
    # EXIT
    # ========================================================================
    if sig.decision in EXIT_DECISIONS:

        if not state.in_trade:
            return _result("skip_not_in_trade", block_reason="not_in_trade")

        side_before = state.side
        entry_before = state.entry_price
        is_late = "LATE" in sig.decision
        comment = f"{sig.strategy.upper()}_EXIT_{'LATE' if is_late else 'NORMAL'}"

        # ── ACTIVE MODE (real close) ──────────────────────────────────────
        if mode == "ACTIVE":
            try:
                if state.ticket:
                    result = close_position_fok(ticket=state.ticket, comment=comment)
                else:
                    result = close_all_positions_fok(symbol=sig.symbol, comment=comment)
            except Exception as e:
                return _result("close_exception", error=str(e))

            if not result.get("success") and not result.get("closed"):
                return _result(
                    "close_failed",
                    error=result.get("error"),
                )

            # Get realized P&L
            profit = result.get("profit", 0.0)
            if not profit:
                profit = result.get("total_profit", 0.0)

            state.realized_profit_usd += profit
            state.daily_done = True
            state.reset_position()

            logger.info(
                f"✅ [{sig.symbol}] Position closed @ {current_price:.2f} | "
                f"profit=${profit:.2f}"
            )

            # Notify
            _notify_trade_close(sig.symbol, side_before, current_price, profit, sig.strategy)

            return _result(
                "trade_closed",
                did_trade=True,
                side=side_before,
                entry_price=entry_before,
                exit_price=current_price,
                profit_usd=profit,
                realized_profit_usd=state.realized_profit_usd,
            )

        # ── MONITOR MODE (simulate exit) ──────────────────────────────────
        else:
            # Calculate simulated profit using MT5 order_calc_profit
            profit = calc_profit(
                symbol=sig.symbol,
                side=side_before,
                lot=sc.lot_size,
                open_price=entry_before,
                close_price=current_price,
            )

            state.realized_profit_usd += profit
            state.daily_done = True
            state.reset_position()

            logger.info(
                f"📊 [{sig.symbol}] SIM exit @ {current_price:.2f} | "
                f"profit=${profit:.2f} (calc_profit)"
            )

            return _result(
                "simulated_exit",
                did_simulate=True,
                side=side_before,
                entry_price=entry_before,
                exit_price=current_price,
                profit_usd=profit,
                realized_profit_usd=state.realized_profit_usd,
            )

    # ── Fallback ──────────────────────────────────────────────────────────
    return _result("none")


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _notify_trade_open(symbol: str, side: str, price: float, strategy: str):
    """Send notification for trade open."""
    msg = f"📥 {symbol} {side.upper()} @ {price:.2f} | {strategy}"

    if HAS_DISCORD:
        notify_discord("alerts", msg)
    if HAS_TELEGRAM:
        notify_telegram(msg)


def _notify_trade_close(symbol: str, side: str, price: float, profit: float, strategy: str):
    """Send notification for trade close."""
    emoji = "🟢" if profit >= 0 else "🔴"
    msg = f"📤 {symbol} {side.upper()} closed @ {price:.2f} | {emoji} ${profit:.2f} | {strategy}"

    if HAS_DISCORD:
        notify_discord("alerts", msg)
    if HAS_TELEGRAM:
        notify_telegram(msg)


# ---------------------------------------------------------------------------
# Main Executor Class
# ---------------------------------------------------------------------------

class Executor:
    """
    Main executor that orchestrates everything.
    """

    def __init__(self, base_dir: str = "data"):
        self.base_dir = base_dir
        self.running = False

        # State per (symbol, strategy) pair
        self.states: Dict[Tuple[str, str], ExecutionState] = {}

        # Track last processed date for day rollover
        self.last_date_mt5: Dict[str, str] = {}

    def get_state(self, symbol: str, strategy: str) -> ExecutionState:
        """Get or create execution state for symbol+strategy."""
        key = (symbol, strategy)
        if key not in self.states:
            self.states[key] = ExecutionState(symbol=symbol, strategy=strategy)
        return self.states[key]

    def check_day_rollover(self, symbol: str, date_mt5: str):
        """Check for MT5 day rollover and reset states."""
        if symbol in self.last_date_mt5:
            if self.last_date_mt5[symbol] != date_mt5:
                logger.info(f"[{symbol}] Day rollover: {self.last_date_mt5[symbol]} → {date_mt5}")

                # Reset all states for this symbol
                for (sym, strat), state in self.states.items():
                    if sym == symbol:
                        state.reset_daily()

                # Notify strategies of new day
                for strategy in StrategyRegistry.get_all_for_symbol(symbol):
                    try:
                        pkt = read_price_packet(symbol)
                        if pkt and pkt.start_price:
                            strategy.on_new_day(pkt.start_price)
                    except Exception as e:
                        logger.error(f"on_new_day failed for {strategy.name}: {e}")

        self.last_date_mt5[symbol] = date_mt5

    def process_symbol(self, symbol: str) -> List[ExecResult]:
        """
        Process one symbol — run all active strategies.
        Returns list of ExecResults.
        """
        sc = SYMBOLS.get(symbol)
        if sc is None:
            logger.warning(f"Symbol {symbol} not in config")
            return []

        if not sc.is_enabled:
            return []

        # Read price packet
        pkt = read_price_packet(symbol)
        if pkt is None:
            logger.debug(f"[{symbol}] No price packet")
            return []

        if pkt.is_stale:
            logger.debug(f"[{symbol}] Stale packet, skipping")
            return []

        # Check day rollover
        self.check_day_rollover(symbol, pkt.date_mt5)

        results = []

        # Get active strategies from config
        strategy_names = sc.strategies
        if not strategy_names:
            logger.debug(f"[{symbol}] No active strategies")
            return []

        # Process each strategy
        for strategy_name in strategy_names:
            try:
                # Load strategy
                strategy = StrategyRegistry.get_or_create(
                    symbol=symbol,
                    strategy_name=strategy_name,
                    sc=sc,
                    base_dir=self.base_dir,
                )

                if strategy is None:
                    continue

                # Get execution state
                state = self.get_state(symbol, strategy_name)

                # Run strategy
                sig = strategy.on_tick(pkt)

                # Persist strategy state
                strategy.persist()

                # Handle signal
                result = handle_signal(sc, state, sig, pkt)
                results.append(result)

                # Log if actionable
                if result.action not in ("waiting", "none"):
                    logger.info(
                        f"[{symbol}:{strategy_name}] {result.decision} → {result.action}"
                    )

            except Exception as e:
                logger.error(f"[{symbol}:{strategy_name}] Error: {e}")
                results.append(ExecResult(
                    symbol=symbol,
                    strategy=strategy_name,
                    decision="ERROR",
                    action="exception",
                    mode="ACTIVE" if sc.is_trading_enabled else "MONITOR",
                    error=str(e),
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ))

        return results

    def run_single(self, symbols: List[str] = None) -> List[ExecResult]:
        """Run single check for all (or specified) symbols."""
        if symbols is None:
            symbols = get_price_symbols()

        all_results = []

        for symbol in symbols:
            results = self.process_symbol(symbol)
            all_results.extend(results)

        return all_results

    def run_loop(self, interval: float = 0.3, symbols: List[str] = None):
        """Run continuous loop."""
        if symbols is None:
            symbols = get_price_symbols()

        self.running = True
        logger.info(f"Starting executor loop | symbols={symbols} | interval={interval}s")

        # MT5 health check
        health = health_check()
        if health.get("connected"):
            logger.info(f"MT5 connected | account={health.get('account')} | balance={health.get('balance')}")
        else:
            logger.warning(f"MT5 not connected: {health.get('error')}")

        try:
            while self.running:
                cycle_start = time.time()

                for symbol in symbols:
                    if not self.running:
                        break

                    try:
                        self.process_symbol(symbol)
                    except Exception as e:
                        logger.error(f"[{symbol}] Cycle error: {e}")

                # Sleep remaining time
                elapsed = time.time() - cycle_start
                sleep_time = max(0, interval - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            self.shutdown()

    def shutdown(self):
        """Clean shutdown."""
        self.running = False

        # Persist all strategies
        logger.info("Persisting strategy states...")
        StrategyRegistry.persist_all()

        # Shutdown MT5
        mt5_shutdown()

        logger.info("Executor shutdown complete")

    def print_status(self):
        """Print current status of all states."""
        print("\n" + "=" * 60)
        print("  EXECUTOR STATUS")
        print("=" * 60)

        for (symbol, strategy), state in self.states.items():
            sc = SYMBOLS.get(symbol)
            mode = "ACTIVE" if sc and sc.is_trading_enabled else "MONITOR"

            print(f"\n  [{symbol}:{strategy}] mode={mode}")
            print(f"    in_trade:    {state.in_trade}")
            print(f"    side:        {state.side}")
            print(f"    entry_price: {state.entry_price}")
            print(f"    ticket:      {state.ticket}")
            print(f"    daily_done:  {state.daily_done}")
            print(f"    trades_today: {state.trades_today}")
            print(f"    realized_pnl: ${state.realized_profit_usd:.2f}")

        print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Astra-Hawk Executor")
    parser.add_argument(
        "--mode",
        choices=["single", "loop", "status"],
        default="single",
        help="Run mode",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.3,
        help="Poll interval in seconds (for loop mode)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Process specific symbol only",
    )
    parser.add_argument(
        "--base-dir",
        type=str,
        default="data",
        help="Base directory for data files",
    )

    args = parser.parse_args()

    # Setup signal handlers
    executor = Executor(base_dir=args.base_dir)

    def signal_handler(sig, frame):
        logger.info("Shutdown signal received")
        executor.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Determine symbols
    symbols = [args.symbol] if args.symbol else None

    # Print config
    print("\n" + "=" * 60)
    print("  ASTRA-HAWK EXECUTOR")
    print("=" * 60)
    print(f"\n  Mode: {args.mode}")
    print(f"  Interval: {args.interval}s")
    print(f"  Base dir: {args.base_dir}")
    print(f"\n  Symbols:")

    for sym, sc in SYMBOLS.items():
        if not sc.is_enabled:
            continue
        mode = "ACTIVE" if sc.is_trading_enabled else "MONITOR"
        strategies = ", ".join(sc.strategies) or "none"
        print(f"    {sym}: {mode} | strategies=[{strategies}]")

    print("\n" + "=" * 60 + "\n")

    # Run
    if args.mode == "single":
        results = executor.run_single(symbols=symbols)

        print("\n  Results:")
        for r in results:
            if r.action not in ("waiting", "none"):
                print(f"    [{r.symbol}:{r.strategy}] {r.decision} → {r.action}")

        executor.shutdown()

    elif args.mode == "loop":
        executor.run_loop(interval=args.interval, symbols=symbols)

    elif args.mode == "status":
        # Quick status check
        health = health_check()
        print(f"  MT5: {'connected' if health.get('connected') else 'disconnected'}")
        print(f"  Account: {health.get('account')}")
        print(f"  Balance: ${health.get('balance', 0):.2f}")
        print(f"  Equity: ${health.get('equity', 0):.2f}")

        snap = get_positions_snapshot()
        print(f"\n  Open positions: {snap.get('count', 0)}")
        print(f"  Floating P&L: ${snap.get('total_profit_usd', 0):.2f}")


if __name__ == "__main__":
    main()