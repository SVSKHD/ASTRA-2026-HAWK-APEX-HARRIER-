# config/symbols.py
from __future__ import annotations

from typing import Tuple


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
        is_enabled:         bool = True,    # pricing runner ON/OFF
        is_trading_enabled: bool = False,   # strategy execution ON/OFF

        # --------------------------
        # Trading Parameters
        # --------------------------
        pip_size:           float = 0.01,
        lot_size:           float = 0.2,
        max_trades_per_day: int   = 3,

        threshold:            float = 1500.0,
        entry_min_multiplier: float = 1.0,
        entry_max_multiplier: float = 1.25,
        close_multiplier:     float = 2.0,

        poll_seconds: float | None = None,

        # --------------------------
        # Strategy Toggles
        # --------------------------
        use_astra_hawk:  bool = False,
        # Threshold strategy — normal entry (1x window) + late entry (2x arm).
        # Multi-symbol, modular, production-grade.

        use_apex_harrier: bool = False,
        # Threshold strategy — XAUUSD-focused, start-price anchored,
        # fixed entry/exit levels, pip-based tolerances.

        use_momentum:    bool = False,
        # Standalone momentum strategy — arms on strong directional move,
        # waits for pullback + resume before entering.
        # Independent of threshold zones.

        # --------------------------
        # Trade Slot Control
        # --------------------------
        max_concurrent_trades:       int  = 1,
        # Max open positions across ALL strategies on this symbol.
        # Ignored when strategy_independent_trades=True.

        strategy_independent_trades: bool = False,
        # False → strategies share max_concurrent_trades slots.
        #         First to enter blocks others until exit.
        # True  → each strategy trades its own position freely.
        #         Can stack positions on same symbol. Use carefully.
    ):
        self.symbol               = symbol
        self.is_enabled           = is_enabled
        self.is_trading_enabled   = is_trading_enabled
        self.pip_size             = pip_size
        self.lot_size             = lot_size
        self.max_trades_per_day   = max_trades_per_day
        self.threshold            = threshold
        self.entry_min_multiplier = entry_min_multiplier
        self.entry_max_multiplier = entry_max_multiplier
        self.close_multiplier     = close_multiplier
        self.poll_seconds         = poll_seconds

        # strategy toggles
        self.use_astra_hawk   = use_astra_hawk
        self.use_apex_harrier = use_apex_harrier
        self.use_momentum     = use_momentum

        # slot control
        self.max_concurrent_trades       = max_concurrent_trades
        self.strategy_independent_trades = strategy_independent_trades

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
            f"trading={self.is_trading_enabled} "
            f"strategies={self.strategies})"
        )


# =====================================
# Define All Symbols Here
# =====================================

SYMBOLS: dict[str, SymbolConfig] = {

    "XAUUSD": SymbolConfig(
        symbol               = "XAUUSD",
        is_enabled           = True,
        is_trading_enabled   = True,
        pip_size             = 0.01,
        lot_size             = 0.2,
        max_trades_per_day   = 3,
        threshold            = 1500.0,
        entry_min_multiplier = 1.0,
        entry_max_multiplier = 1.25,
        close_multiplier     = 2.0,
        # strategies
        use_astra_hawk               = True,
        use_apex_harrier             = False,
        use_momentum                 = False,
        # slot control
        max_concurrent_trades        = 1,
        strategy_independent_trades  = False,
    ),

    "XAUEUR": SymbolConfig(
        symbol               = "XAUEUR",
        is_enabled           = True,
        is_trading_enabled   = False,
        pip_size             = 0.01,
        lot_size             = 0.2,
        max_trades_per_day   = 3,
        threshold            = 1500.0,
        entry_min_multiplier = 1.0,
        entry_max_multiplier = 1.25,
        close_multiplier     = 2.0,
        use_astra_hawk               = True,
        use_apex_harrier             = False,
        use_momentum                 = False,
        max_concurrent_trades        = 1,
        strategy_independent_trades  = False,
    ),

    "GBPUSD": SymbolConfig(
        symbol               = "GBPUSD",
        is_enabled           = True,
        is_trading_enabled   = False,
        pip_size             = 0.0001,
        lot_size             = 0.2,
        max_trades_per_day   = 3,
        threshold            = 15.0,
        entry_min_multiplier = 1.0,
        entry_max_multiplier = 1.25,
        close_multiplier     = 2.0,
        use_astra_hawk               = True,
        use_apex_harrier             = False,
        use_momentum                 = False,
        max_concurrent_trades        = 1,
        strategy_independent_trades  = False,
    ),

    "EURUSD": SymbolConfig(
        symbol               = "EURUSD",
        is_enabled           = True,
        is_trading_enabled   = False,
        pip_size             = 0.0001,
        lot_size             = 0.2,
        max_trades_per_day   = 3,
        threshold            = 15.0,
        entry_min_multiplier = 1.0,
        entry_max_multiplier = 1.25,
        close_multiplier     = 2.0,
        use_astra_hawk               = True,
        use_apex_harrier             = False,
        use_momentum                 = False,
        max_concurrent_trades        = 1,
        strategy_independent_trades  = False,
    ),
}