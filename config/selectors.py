# config/selectors.py
from __future__ import annotations
from typing import List
from .symbols import SYMBOLS

def get_price_symbols() -> List[str]:
    """Symbols that should have pricing recorded."""
    return [k for k, sc in SYMBOLS.items() if sc.is_enabled]

def get_trading_symbols() -> List[str]:
    """Symbols that executor is allowed to trade."""
    return [k for k, sc in SYMBOLS.items() if sc.is_enabled and sc.is_trading_enabled]