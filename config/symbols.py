# config/symbols.py
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class SymbolConfig:
    symbol: str

    # ==========================
    # Runner Control
    # ==========================
    is_enabled: bool = True              # pricing runner ON/OFF
    is_trading_enabled: bool = False     # strategy execution ON/OFF

    # ==========================
    # Trading Parameters
    # ==========================
    pip_size: float = 0.01               # XAUUSD = 0.01, EURUSD = 0.0001
    lot_size: float = 0.2
    max_trades_per_day: int = 3

    # Threshold logic
    threshold: float = 1500.0            # base threshold in pips
    entry_min_multiplier: float = 1.0
    entry_max_multiplier: float = 1.25
    close_multiplier: float = 2.0

    # Optional per-symbol poll override
    poll_seconds: float | None = None


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
        threshold=1500,              # 1500 points = 15$ move (if 0.01 pip)
        entry_min_multiplier=1.0,
        entry_max_multiplier=1.25,
        close_multiplier=2.0,
    ),
    "XAUEUR": SymbolConfig(
        symbol="XAUEUR",
        is_enabled=True,
        is_trading_enabled=False,
        pip_size=0.01,
        lot_size=0.2,
        max_trades_per_day=3,
        threshold=1500,  # 1500 points = 15$ move (if 0.01 pip)
        entry_min_multiplier=1.0,
        entry_max_multiplier=1.25,
        close_multiplier=2.0,
    ),
    "GBPUSD": SymbolConfig(
        symbol="GBPUSD",
        is_enabled=True,
        is_trading_enabled=False,
        pip_size=0.0001,
        lot_size=0.2,
        max_trades_per_day=3,
        threshold=1500,  # 1500 points = 15$ move (if 0.01 pip)
        entry_min_multiplier=1.0,
        entry_max_multiplier=1.25,
        close_multiplier=2.0,
    ),
    "EURUSD": SymbolConfig(
        symbol="EURUSD",
        is_enabled=True,
        is_trading_enabled=False,     # monitor only
        pip_size=0.0001,
        lot_size=0.2,
        max_trades_per_day=3,
        threshold=15,
        entry_min_multiplier=1.0,
        entry_max_multiplier=1.25,
        close_multiplier=2.0,
    ),
    "GBPUSD": SymbolConfig(
        symbol="GBPUSD",
        is_enabled=True,
        is_trading_enabled=False,
        pip_size=0.0001,
        lot_size=0.2,
        max_trades_per_day=3,
        threshold=15,
    ),
}