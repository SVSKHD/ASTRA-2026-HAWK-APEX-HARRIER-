# config/symbols.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, Optional


# =====================================
# P&L Lock Configuration
# =====================================

@dataclass
class PnLLock:
    """
    Profit and Loss lock configuration per symbol.
    Controls when trading should stop based on P&L thresholds.

    Usage:
        pnl = PnLLock(daily_profit_lock_usd=100, daily_max_loss_usd=-50)
        should_stop, reason = pnl.should_stop_trading(realized=75.0, floating=10.0)
    """

    # --------------------------
    # Profit Locks
    # --------------------------
    daily_profit_lock_usd: float = 50.0
    """Stop trading after realizing this much profit in a day. Set to 0 to disable."""

    lock_on_profit: bool = True
    """If True, stop ALL trading after hitting profit lock. If False, only block new entries."""

    # --------------------------
    # Loss Limits
    # --------------------------
    daily_max_loss_usd: float = -30.0
    """Stop trading after losing this much in a day. Must be negative. Set to 0 to disable."""

    catastrophic_loss_usd: float = -75.0
    """Emergency stop - close all positions immediately. Must be negative. Set to 0 to disable."""

    # --------------------------
    # Drawdown Control
    # --------------------------
    max_drawdown_pct: float = 0.0
    """Max drawdown as % of starting equity. 0 = disabled. E.g., 5.0 = stop at 5% drawdown."""

    trailing_profit_lock_pct: float = 0.0
    """Lock in X% of peak profit. E.g., 50 = if peak was $100, lock if drops to $50. 0 = disabled."""

    # --------------------------
    # Per-Trade Limits
    # --------------------------
    max_loss_per_trade_usd: float = 0.0
    """Max loss allowed per single trade. 0 = disabled. Can be used to auto-calculate SL."""

    max_loss_per_trade_pips: float = 0.0
    """Max loss in pips per trade. 0 = disabled."""

    # --------------------------
    # Reset Behavior
    # --------------------------
    reset_at_mt5_midnight: bool = True
    """Reset daily P&L counters at MT5 server midnight."""

    def __post_init__(self):
        """Ensure loss values are negative."""
        if self.daily_max_loss_usd > 0:
            self.daily_max_loss_usd = -self.daily_max_loss_usd
        if self.catastrophic_loss_usd > 0:
            self.catastrophic_loss_usd = -self.catastrophic_loss_usd

    def should_stop_trading(
            self,
            realized_pnl: float,
            floating_pnl: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Check if trading should stop based on P&L.

        Args:
            realized_pnl: Realized P&L for the day (closed trades)
            floating_pnl: Unrealized P&L from open positions

        Returns:
            (should_stop, reason)
        """
        total_pnl = realized_pnl + floating_pnl

        # Profit lock (realized only)
        if self.daily_profit_lock_usd > 0 and realized_pnl >= self.daily_profit_lock_usd:
            return True, f"profit_lock: realized=${realized_pnl:.2f} >= ${self.daily_profit_lock_usd:.2f}"

        # Daily max loss (total = realized + floating)
        if self.daily_max_loss_usd < 0 and total_pnl <= self.daily_max_loss_usd:
            return True, f"daily_max_loss: total=${total_pnl:.2f} <= ${self.daily_max_loss_usd:.2f}"

        # Catastrophic loss
        if self.catastrophic_loss_usd < 0 and total_pnl <= self.catastrophic_loss_usd:
            return True, f"catastrophic_loss: total=${total_pnl:.2f} <= ${self.catastrophic_loss_usd:.2f}"

        return False, "ok"

    def should_force_close(
            self,
            realized_pnl: float,
            floating_pnl: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Check if positions should be force-closed (catastrophic loss only).

        Returns:
            (should_close, reason)
        """
        total_pnl = realized_pnl + floating_pnl

        if self.catastrophic_loss_usd < 0 and total_pnl <= self.catastrophic_loss_usd:
            return True, f"catastrophic_loss: total=${total_pnl:.2f} <= ${self.catastrophic_loss_usd:.2f}"

        return False, "ok"

    def check_trade_loss(
            self,
            trade_pnl: float,
            trade_pips: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Check if a single trade exceeds per-trade loss limits.

        Args:
            trade_pnl: Current P&L of the trade in USD
            trade_pips: Current P&L in pips (optional)

        Returns:
            (exceeds_limit, reason)
        """
        # USD limit
        if self.max_loss_per_trade_usd > 0 and trade_pnl <= -self.max_loss_per_trade_usd:
            return True, f"max_trade_loss_usd: ${trade_pnl:.2f} <= -${self.max_loss_per_trade_usd:.2f}"

        # Pips limit
        if self.max_loss_per_trade_pips > 0 and trade_pips <= -self.max_loss_per_trade_pips:
            return True, f"max_trade_loss_pips: {trade_pips:.1f} <= -{self.max_loss_per_trade_pips:.1f}"

        return False, "ok"


# =====================================
# Preset P&L Configurations
# =====================================

PNL_CONSERVATIVE = PnLLock(
    daily_profit_lock_usd=50.0,
    daily_max_loss_usd=-30.0,
    catastrophic_loss_usd=-75.0,
    lock_on_profit=True,
)
"""Conservative limits for live trading."""

PNL_AGGRESSIVE = PnLLock(
    daily_profit_lock_usd=200.0,
    daily_max_loss_usd=-100.0,
    catastrophic_loss_usd=-200.0,
    lock_on_profit=True,
)
"""Higher limits for experienced traders."""

PNL_UNLIMITED = PnLLock(
    daily_profit_lock_usd=0.0,
    daily_max_loss_usd=0.0,
    catastrophic_loss_usd=0.0,
    lock_on_profit=False,
)
"""No limits - FOR TESTING ONLY."""

PNL_MONITOR_ONLY = PnLLock(
    daily_profit_lock_usd=50.0,
    daily_max_loss_usd=-30.0,
    catastrophic_loss_usd=0.0,  # No force close
    lock_on_profit=False,  # Don't stop trading
)
"""Track P&L but don't enforce limits."""


# =====================================
# Symbol Configuration
# =====================================

class SymbolConfig:
    """
    Symbol configuration.
    Enable strategies per symbol using boolean toggles.
    The .strategies property auto-builds the active list from those booleans.
    """

    def __init__(
            self,
            symbol: str,

            # --------------------------
            # Runner Control
            # --------------------------
            is_enabled: bool = True,  # pricing runner ON/OFF
            is_trading_enabled: bool = False,  # strategy execution ON/OFF

            # --------------------------
            # Trading Parameters
            # --------------------------
            pip_size: float = 0.01,
            lot_size: float = 0.2,
            max_trades_per_day: int = 3,

            threshold: float = 1500.0,
            entry_min_multiplier: float = 1.0,
            entry_max_multiplier: float = 1.25,
            close_multiplier: float = 2.0,

            poll_seconds: float | None = None,

            # --------------------------
            # P&L Lock Configuration
            # --------------------------
            pnl_lock: PnLLock | None = None,
            # Per-symbol P&L lock settings.
            # If None, uses default PNL_CONSERVATIVE.

            # --------------------------
            # Strategy Toggles
            # --------------------------
            use_astra_hawk: bool = False,
            # Threshold strategy — normal entry (1x window) + late entry (2x arm).
            # Multi-symbol, modular, production-grade.

            use_apex_harrier: bool = False,
            # Threshold strategy — XAUUSD-focused, start-price anchored,
            # fixed entry/exit levels, pip-based tolerances.

            use_momentum: bool = False,
            # Standalone momentum strategy — arms on strong directional move,
            # waits for pullback + resume before entering.
            # Independent of threshold zones.

            # --------------------------
            # Trade Slot Control
            # --------------------------
            max_concurrent_trades: int = 1,
            # Max open positions across ALL strategies on this symbol.
            # Ignored when strategy_independent_trades=True.

            strategy_independent_trades: bool = False,
            # False → strategies share max_concurrent_trades slots.
            #         First to enter blocks others until exit.
            # True  → each strategy trades its own position freely.
            #         Can stack positions on same symbol. Use carefully.
    ):
        self.symbol = symbol
        self.is_enabled = is_enabled
        self.is_trading_enabled = is_trading_enabled
        self.pip_size = pip_size
        self.lot_size = lot_size
        self.max_trades_per_day = max_trades_per_day
        self.threshold = threshold
        self.entry_min_multiplier = entry_min_multiplier
        self.entry_max_multiplier = entry_max_multiplier
        self.close_multiplier = close_multiplier
        self.poll_seconds = poll_seconds

        # P&L lock - default to conservative if not specified
        self.pnl_lock = pnl_lock if pnl_lock is not None else PNL_CONSERVATIVE

        # strategy toggles
        self.use_astra_hawk = use_astra_hawk
        self.use_apex_harrier = use_apex_harrier
        self.use_momentum = use_momentum

        # slot control
        self.max_concurrent_trades = max_concurrent_trades
        self.strategy_independent_trades = strategy_independent_trades

    @property
    def is_tradeable(self) -> bool:
        """
        True if symbol is enabled AND trading is enabled.
        Used by strategies to check if they should generate entry signals.

        Note: Even if is_tradeable=False, strategies still run in MONITOR mode
        (they track state but don't signal entries).
        """
        return self.is_enabled and self.is_trading_enabled

    @property
    def strategies(self) -> Tuple[str, ...]:
        """
        Auto-built from boolean toggles above.
        Read by the executor — do not set manually.

        To add a new strategy:
            1. Add  use_your_strategy: bool = False  in __init__
            2. Add  if self.use_your_strategy: active.append("your_strategy")  below
            3. Register the class in strategy/registry.py
        """
        active = []
        if self.use_astra_hawk:
            active.append("astra_hawk")
        if self.use_apex_harrier:
            active.append("apex_harrier")
        if self.use_momentum:
            active.append("momentum")
        return tuple(active)

    def __repr__(self) -> str:
        return (
            f"SymbolConfig({self.symbol!r} "
            f"enabled={self.is_enabled} "
            f"trading={self.is_trading_enabled} "
            f"tradeable={self.is_tradeable} "
            f"strategies={self.strategies})"
        )


# =====================================
# Define All Symbols Here
# =====================================

SYMBOLS: dict[str, SymbolConfig] = {

    "XAUUSD": SymbolConfig(
        symbol="XAUUSD",
        is_enabled=True,
        is_trading_enabled=True,
        pip_size=0.01,
        lot_size=0.2,
        max_trades_per_day=3,
        threshold=1500.0,
        entry_min_multiplier=1.0,
        entry_max_multiplier=1.25,
        close_multiplier=2.0,
        # P&L lock - custom for XAUUSD
        pnl_lock=PnLLock(
            daily_profit_lock_usd=100.0,
            daily_max_loss_usd=-50.0,
            catastrophic_loss_usd=-100.0,
            lock_on_profit=True,
            max_loss_per_trade_usd=25.0,
        ),
        # strategies
        use_astra_hawk=True,
        use_apex_harrier=False,
        use_momentum=False,
        # slot control
        max_concurrent_trades=1,
        strategy_independent_trades=False,
    ),

    "XAUEUR": SymbolConfig(
        symbol="XAUEUR",
        is_enabled=True,
        is_trading_enabled=False,
        pip_size=0.01,
        lot_size=0.2,
        max_trades_per_day=3,
        threshold=1500.0,
        entry_min_multiplier=1.0,
        entry_max_multiplier=1.25,
        close_multiplier=2.0,
        pnl_lock=PNL_MONITOR_ONLY,  # Using preset
        use_astra_hawk=True,
        use_apex_harrier=False,
        use_momentum=False,
        max_concurrent_trades=1,
        strategy_independent_trades=False,
    ),

    "GBPUSD": SymbolConfig(
        symbol="GBPUSD",
        is_enabled=True,
        is_trading_enabled=False,
        pip_size=0.0001,
        lot_size=0.2,
        max_trades_per_day=3,
        threshold=15.0,
        entry_min_multiplier=1.0,
        entry_max_multiplier=1.25,
        close_multiplier=2.0,
        pnl_lock=PNL_CONSERVATIVE,  # Using preset
        use_astra_hawk=True,
        use_apex_harrier=False,
        use_momentum=False,
        max_concurrent_trades=1,
        strategy_independent_trades=False,
    ),

    "EURUSD": SymbolConfig(
        symbol="EURUSD",
        is_enabled=True,
        is_trading_enabled=False,
        pip_size=0.0001,
        lot_size=0.2,
        max_trades_per_day=3,
        threshold=15.0,
        entry_min_multiplier=1.0,
        entry_max_multiplier=1.25,
        close_multiplier=2.0,
        pnl_lock=PNL_CONSERVATIVE,  # Using preset
        use_astra_hawk=True,
        use_apex_harrier=False,
        use_momentum=False,
        max_concurrent_trades=1,
        strategy_independent_trades=False,
    ),
}


# =====================================
# Helper Functions
# =====================================

def get_symbol(name: str) -> SymbolConfig | None:
    """Get SymbolConfig by name, or None if not found."""
    return SYMBOLS.get(name)


def get_enabled_symbols() -> list[str]:
    """Get list of enabled symbol names (pricing runner)."""
    return [k for k, v in SYMBOLS.items() if v.is_enabled]


def get_tradeable_symbols() -> list[str]:
    """Get list of symbols where trading is enabled."""
    return [k for k, v in SYMBOLS.items() if v.is_tradeable]