# strategy/base.py
from __future__ import annotations

"""
BaseStrategy — interface every strategy must implement.

Strategies are PURE DECISION MAKERS:
    - Receive PricePacket + PositionInfo
    - Return StrategyResult (decision string + telemetry)
    - Maintain internal tracking state (bias, armed flags, etc.) in memory
    - NEVER touch MT5, files, or notifications

Executor lifecycle:
    strategy.init(symbol, sc)                 once on startup
    strategy.apply_state(data)                once after init (if resuming)
    strategy.on_tick(pkt, pos) → result       every tick
    data = strategy.build_state()             after every tick (executor persists)
    strategy.on_new_day(new_start_price)      on MT5 date rollover
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# PricePacket — built by executor from price_assembly JSON
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PricePacket:
    """
    Read-only price snapshot passed to on_tick().
    Executor builds this from data/price_assembly/<SYMBOL>.json.
    """
    symbol:       str
    date_mt5:     str               # "2026-03-06"
    hhmm_mt5:     str               # "14:35"
    server_time:  str               # ISO timestamp

    mid:          float
    bid:          float
    ask:          float

    start_price:  float             # day open (locked at 00:00 MT5)
    start_status: str               # "LOCKED" | "PENDING" | "NONE"

    high:         Optional[float]   # intraday high (or None)
    low:          Optional[float]   # intraday low (or None)

    is_stale:       bool  = False
    stale_seconds:  int   = 0

    @staticmethod
    def from_packet(packet: Dict[str, Any]) -> Optional[PricePacket]:
        """Build from a price_assembly JSON dict. Returns None if data is missing."""
        if packet is None:
            return None
        current = packet.get("current")
        if current is None or current.get("mid") is None:
            return None

        meta   = packet.get("meta") or {}
        start  = packet.get("start")
        high_b = packet.get("high")
        low_b  = packet.get("low")

        if isinstance(start, dict) and start.get("status") == "LOCKED":
            start_status = "LOCKED"
            start_price  = start.get("price")
        else:
            start_status = "NONE" if start is None else (start.get("status") or "PENDING")
            start_price  = (start or {}).get("price")

        if start_price is None:
            start_price = 0.0

        return PricePacket(
            symbol       = packet.get("symbol", ""),
            date_mt5     = meta.get("date_mt5", ""),
            hhmm_mt5     = meta.get("hhmm_mt5", ""),
            server_time  = meta.get("updated_utc", ""),
            mid          = float(current["mid"]),
            bid          = float(current.get("bid", 0.0)),
            ask          = float(current.get("ask", 0.0)),
            start_price  = float(start_price),
            start_status = start_status,
            high         = float(high_b["since_day_start"]) if isinstance(high_b, dict) and high_b.get("since_day_start") is not None else None,
            low          = float(low_b["since_day_start"])  if isinstance(low_b, dict) and low_b.get("since_day_start") is not None else None,
            is_stale     = bool(meta.get("is_stale", False)),
            stale_seconds= int(meta.get("stale_seconds", 0)),
        )


# ---------------------------------------------------------------------------
# PositionInfo — executor-owned, passed to strategy read-only
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PositionInfo:
    """
    Current position state — owned and managed by executor.
    Strategy reads this but NEVER mutates it.
    Executor updates this after processing each StrategyResult.
    """
    in_trade:     bool            = False
    side:         Optional[str]   = None    # "buy" | "sell"
    entry_price:  Optional[float] = None
    entry_time:   Optional[str]   = None
    entry_mode:   Optional[str]   = None    # "normal" | "late"
    daily_done:   bool            = False
    trades_today: int             = 0


# ---------------------------------------------------------------------------
# StrategyResult — returned by on_tick(), consumed by executor
# ---------------------------------------------------------------------------

@dataclass
class StrategyResult:
    """
    Decision from strategy. Executor routes this to handle_signal().

    The strategy ONLY returns a decision — it does NOT modify position state.
    Executor reads .decision and .did_signal to decide whether to place a trade.
    """
    strategy:   str             # "astra_hawk"
    symbol:     str
    decision:   str             # WAIT | ENTER_* | EXIT_* | SKIP_* | HALT_*
    action:     str             # entered | exited | waiting | skip | blocked_* | none
    did_signal: bool = False    # True = executor should act on this decision

    # populated on entry / exit decisions
    side:        Optional[str]   = None     # "buy" | "sell"
    entry_price: Optional[float] = None     # mid at decision time
    exit_price:  Optional[float] = None     # mid at decision time
    entry_mode:  Optional[str]   = None     # "normal" | "late"

    # state flags — for executor bookkeeping
    in_trade:    bool = False
    daily_done:  bool = False
    zone_id:     Optional[int] = None

    # rich telemetry — logged + sent to notifications
    telemetry:   Dict[str, Any] = field(default_factory=dict)
    now_iso:     str            = ""


# ---------------------------------------------------------------------------
# BaseStrategy ABC
# ---------------------------------------------------------------------------

class BaseStrategy(ABC):
    """
    All strategies inherit from this.

    Rules:
        - on_tick() returns StrategyResult every tick (even WAIT)
        - Reads PositionInfo but NEVER modifies position state
        - NEVER imports MT5, opens files, or sends notifications
        - Internal tracking state (bias, armed flags) in memory only
        - build_state() / apply_state() — executor calls for persistence
    """

    # ── identity ─────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Unique name. Must match:
          - config/symbols.py:  use_<name> toggle
          - strategy/loader.py: _LOADERS key
        """
        ...

    # ── lifecycle ────────────────────────────────────────────────────────

    def init(self, symbol: str, sc: Any) -> None:
        """
        Called once before the run loop starts.
        Call super().init() first, then set up subclass fields.
        """
        self.symbol = symbol
        self.sc     = sc

    @abstractmethod
    def on_tick(self, pkt: PricePacket, pos: PositionInfo) -> StrategyResult:
        """
        Core logic — called every tick.

        Args:
            pkt: current price data (read-only)
            pos: current position state from executor (read-only)

        Returns:
            StrategyResult with decision + telemetry
        """
        ...

    def on_new_day(self, new_start_price: float) -> None:
        """
        Called on MT5 date rollover.
        Override to reset internal tracking (bias, armed flags, counters).
        """
        pass

    # ── state serialization (executor calls for persistence) ─────────────

    def build_state(self) -> Dict[str, Any]:
        """
        Returns internal tracking state for executor to persist.
        Called after every on_tick(). Default: empty dict.
        """
        return {}

    def apply_state(self, data: Dict[str, Any]) -> None:
        """
        Restore internal tracking state from saved dict.
        Called once on startup if state exists. Must never raise.
        """
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, symbol={getattr(self, 'symbol', '?')})"