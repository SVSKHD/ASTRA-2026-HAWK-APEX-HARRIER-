# strategy/base.py
from __future__ import annotations

"""
BaseStrategy — interface every strategy must implement.

Executor calls per tick:
    strategy.init(symbol, sc, base_dir)   once on startup
    strategy.restore()                    once after init
    strategy.on_tick(pkt)                 every 0.1s
    strategy.persist()                    after every on_tick
    strategy.on_new_day(new_start_price)  on MT5 date rollover
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

# TYPE_CHECKING = False at runtime, True only when a type checker (mypy/pyright) runs.
# This breaks the circular import:
#   strategy/base.py  ->  executor/price_reader.py  ->  (nothing back)
# but executor/engine.py -> strategy/base.py would cause a cycle at runtime
# if we imported PricePacket unconditionally here.
if TYPE_CHECKING:
    from executor.price_reader import PricePacket
    from config.symbols        import SymbolConfig


# ---------------------------------------------------------------------------
# StrategyResult — returned by on_tick(), consumed by executor/engine.py
# ---------------------------------------------------------------------------

@dataclass
class StrategyResult:
    strategy:   str     # e.g. "astra_hawk"
    symbol:     str
    decision:   str     # WAIT | ENTER_* | EXIT_* | SKIP_* | HALT_*
    action:     str     # entered | exited | waiting | skip | blocked_* | none
    did_signal: bool = False   # True only when decision warrants engine action

    # populated on entry / exit
    side:        Optional[str]   = None   # "buy" | "sell"
    entry_price: Optional[float] = None
    exit_price:  Optional[float] = None
    entry_mode:  Optional[str]   = None   # "normal" | "late" | "momentum"
    profit_usd:  float           = 0.0

    # state flags — runner bookkeeping
    in_trade:    bool = False
    daily_done:  bool = False
    zone_id:     Optional[int] = None

    # rich telemetry — logged + sent to Discord/Telegram
    telemetry:   Dict[str, Any] = field(default_factory=dict)
    now_iso:     str            = ""


# ---------------------------------------------------------------------------
# BaseStrategy ABC
# ---------------------------------------------------------------------------

class BaseStrategy(ABC):

    # ── identity ─────────────────────────────────────────────────────────────
    @property
    @abstractmethod
    def name(self) -> str:
        """
        Unique lowercase_underscore name.
        Must match the key in strategy/loader.py _LOADERS dict.
        Examples: "astra_hawk", "apex_harrier", "momentum"
        """
        ...

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def init(self, symbol: str, sc: "SymbolConfig", base_dir: str) -> None:
        """
        Called once before the run loop starts.
        Call super().init(...) first, then set up subclass fields.
        """
        self.symbol   = symbol
        self.sc       = sc
        self.base_dir = base_dir

    @abstractmethod
    def restore(self) -> None:
        """
        Load persisted state from disk.
        Called once after init(). Must never raise — catch all, log, continue.
        """
        ...

    @abstractmethod
    def persist(self) -> None:
        """
        Save current intraday state to disk atomically.
        Called after every on_tick(). Must never raise.
        """
        ...

    @abstractmethod
    def on_tick(self, pkt: "PricePacket") -> StrategyResult:
        """
        Core strategy logic — called every 0.1s.
        Receives PricePacket, mutates internal state, returns StrategyResult.
        Must be fast (< a few ms). No blocking IO inside here.
        """
        ...

    def on_new_day(self, new_start_price: float) -> None:
        """
        Called by runner on MT5 date rollover.
        Override to reset all intraday state.
        Default does nothing.
        """
        pass

    # ── helper ───────────────────────────────────────────────────────────────
    def _state_dir(self) -> str:
        """
        Root folder for this strategy+symbol persisted state.
        e.g.  data/strategy_state/astra_hawk/XAUUSD/
        Used internally by PersistenceMixin.
        """
        return os.path.join(self.base_dir, "strategy_state", self.name, self.symbol)