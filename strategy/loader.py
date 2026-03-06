# strategy/loader.py
from __future__ import annotations

"""
Strategy loader — maps strategy name string → class instance.

To add a new strategy:
    1. Create  strategy/your_name.py  extending BaseStrategy
    2. Add a lazy loader function below
    3. Add it to _LOADERS dict
    4. Add  use_your_name: bool  to config/symbols.py SymbolConfig
    5. Flip it True per symbol — selectors pick it up automatically
"""

from typing import Callable, Dict, List
from .base import BaseStrategy


# ---------------------------------------------------------------------------
# Lazy loaders — import only when the strategy is actually enabled
# ---------------------------------------------------------------------------

def _load_astra_hawk():
    from .astra_hawk import AstraHawkStrategy
    return AstraHawkStrategy

def _load_apex_harrier():
    from .apex_harrier import ApexHarrierStrategy
    return ApexHarrierStrategy

def _load_momentum():
    from .momentum import MomentumStrategy
    return MomentumStrategy


_LOADERS: Dict[str, Callable] = {
    "astra_hawk":   _load_astra_hawk,
    "apex_harrier": _load_apex_harrier,
    "momentum":     _load_momentum,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_strategy(name: str) -> BaseStrategy:
    """
    Returns a fresh uninitialised instance of the named strategy.
    Raises ValueError if name is not in _LOADERS.
    """
    loader = _LOADERS.get(name)
    if loader is None:
        raise ValueError(
            f"Unknown strategy '{name}'. "
            f"Available: {list(_LOADERS.keys())}. "
            f"Register it in strategy/loader.py."
        )
    return loader()()     # loader() → class,  class() → instance


def available_strategies() -> List[str]:
    """Returns all registered strategy names."""
    return list(_LOADERS.keys())