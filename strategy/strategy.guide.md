# How to Add a New Strategy

## Folder structure

```
strategy/
    base.py              ← BaseStrategy ABC + StrategyResult (never edit)
    persistence.py       ← PersistenceMixin + ShutdownManager (never edit)
    loader.py            ← name → class map (add your entry here)
    __init__.py          ← public exports (never edit)
    astra_hawk.py        ← example: threshold normal + late entry
    apex_harrier.py      ← XAUUSD start-price anchored
    momentum.py          ← standalone pullback + resume
    your_strategy.py     ← your new one goes here
```

---

## Step 1 — Create strategy/your_strategy.py

```python
# strategy/your_strategy.py
from __future__ import annotations

from typing import Any, Dict, Optional
from .base        import BaseStrategy, StrategyResult
from .persistence import PersistenceMixin

# PricePacket and SymbolConfig are passed at runtime — import for type hints only
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from executor.price_reader import PricePacket
    from config.symbols        import SymbolConfig


class YourStrategy(PersistenceMixin, BaseStrategy):

    @property
    def name(self) -> str:
        return "your_strategy"   # must match loader.py key

    # ── lifecycle ──────────────────────────────────────────────────────────

    def init(self, symbol: str, sc: "SymbolConfig", base_dir: str) -> None:
        super().init(symbol, sc, base_dir)
        # initialise your intraday state fields here
        self._position = None
        self._date     = None
        self._persist_init()   # always call this last — registers shutdown hook

    def restore(self) -> None:
        data = self._persist_load()
        if data is None:
            return
        try:
            self._apply_state(data)
        except Exception as e:
            print(f"[{self.name}:{self.symbol}] restore failed ({e!r}) — fresh start")

    def persist(self) -> None:
        self._persist_save(reason="tick")

    def on_new_day(self, new_start_price: float) -> None:
        self._persist_save(reason="rollover")   # snapshot before reset
        self._position = None
        self._date     = None

    # ── persistence contract ───────────────────────────────────────────────

    def _build_state(self) -> Dict[str, Any]:
        # return everything needed to resume after a restart
        return {
            "date_mt5":   self._date,
            "in_trade":   self._position is not None,
            "daily_done": False,
            "position":   self._position,
        }

    def _apply_state(self, data: Dict[str, Any]) -> None:
        self._date     = data.get("date_mt5")
        self._position = data.get("position")

    # ── core logic ─────────────────────────────────────────────────────────

    def on_tick(self, pkt: "PricePacket") -> StrategyResult:
        sc = self.sc

        # day init
        if self._date is None:
            self._date = pkt.date_mt5

        # your logic here — return one of:
        #   decision="WAIT"              action="waiting"      did_signal=False
        #   decision="ENTER_FIRST_LONG"  action="entered"      did_signal=True
        #   decision="EXIT_SECOND_LONG"  action="exited"       did_signal=True
        #   decision="SKIP_*"            action="skip"         did_signal=False
        #   decision="HALT_NOT_TRADEABLE" action="halt"        did_signal=False

        return StrategyResult(
            strategy   = self.name,
            symbol     = self.symbol,
            decision   = "WAIT",
            action     = "waiting",
            did_signal = False,
            in_trade   = self._position is not None,
            now_iso    = pkt.server_time,
            telemetry  = {
                "mid":         pkt.mid,
                "start_price": pkt.start_price,
            },
        )
```

---

## Step 2 — Register in strategy/loader.py

```python
# add a lazy loader function
def _load_your_strategy():
    from .your_strategy import YourStrategy
    return YourStrategy

# add to _LOADERS dict
_LOADERS: Dict[str, Callable] = {
    "astra_hawk":    _load_astra_hawk,
    "apex_harrier":  _load_apex_harrier,
    "momentum":      _load_momentum,
    "your_strategy": _load_your_strategy,   # ← add this
}
```

---

## Step 3 — Add toggle to config/symbols.py

```python
class SymbolConfig:
    def __init__(
        self,
        ...
        use_your_strategy: bool = False,   # ← add this field
        ...
    ):
        ...
        self.use_your_strategy = use_your_strategy   # ← store it

    @property
    def strategies(self) -> Tuple[str, ...]:
        active = []
        if self.use_astra_hawk:    active.append("astra_hawk")
        if self.use_apex_harrier:  active.append("apex_harrier")
        if self.use_momentum:      active.append("momentum")
        if self.use_your_strategy: active.append("your_strategy")  # ← add this
        return tuple(active)
```

---

## Step 4 — Enable per symbol in SYMBOLS dict

```python
SYMBOLS = {
    "XAUUSD": SymbolConfig(
        ...
        use_your_strategy = True,   # ← flip on
    ),
    "EURUSD": SymbolConfig(
        ...
        use_your_strategy = False,  # ← leave off
    ),
}
```

---

## That's it. Nothing else to change.

`config/selectors.py` → `get_strategies_for_symbol("XAUUSD")` returns `("your_strategy",)` automatically.
`executor/runner.py` → loads it, feeds it PricePackets, routes decisions to engine.

---

## Decision vocabulary

| decision string        | meaning                                      |
|------------------------|----------------------------------------------|
| `WAIT`                 | no signal this tick                          |
| `ENTER_FIRST_LONG`     | normal long entry (1x window)                |
| `ENTER_FIRST_SHORT`    | normal short entry                           |
| `ENTER_LATE_LONG`      | late long entry (after jump-over)            |
| `ENTER_LATE_SHORT`     | late short entry                             |
| `EXIT_SECOND_LONG`     | normal long exit (2x target)                 |
| `EXIT_SECOND_SHORT`    | normal short exit                            |
| `EXIT_LATE_LONG`       | late long exit                               |
| `EXIT_LATE_SHORT`      | late short exit                              |
| `SKIP_JUMP_OVER_ENTRY` | price jumped past entry window — arm late    |
| `SKIP_DIRECT_TO_SECOND`| price already past 2x on open               |
| `SKIP_LATE_TOO_FAR`    | late armed but price overshot exit           |
| `HALT_NOT_TRADEABLE`   | symbol config says is_tradeable=False        |

For a completely new pattern (e.g. `ENTER_BREAKOUT`), add it to:
- `strategy/base.py` docstring
- `executor/engine.py` ENTRY_DECISIONS / EXIT_DECISIONS sets

---

## What persists automatically

After `_persist_init()` is called in `init()`:

- Every tick → `data/strategy_state/<name>/<SYMBOL>/state.json` overwritten atomically
- On `Ctrl+C` / `kill` → `shutdown_<timestamp>.json` saved permanently
- On restart → gap printed to console, `resume.log` appended

You only need to implement:
```python
def _build_state(self) -> Dict    # what to save
def _apply_state(self, data)      # how to restore
```

---

## PricePacket fields available in on_tick()

```python
pkt.symbol        # "XAUUSD"
pkt.date_mt5      # "2026-03-06"
pkt.hhmm_mt5      # "14:35"
pkt.server_time   # ISO timestamp

pkt.mid           # current mid price
pkt.bid           # current bid
pkt.ask           # current ask

pkt.start_price   # day open locked at 00:00 MT5 time
pkt.start_status  # "LOCKED"

pkt.high          # intraday high since day start (or None)
pkt.low           # intraday low since day start (or None)

pkt.is_stale      # always False (reader filters stale packets out)
pkt.stale_seconds # seconds since last MT5 tick
```

## SymbolConfig fields available via self.sc

```python
self.sc.pip_size             # 0.01 (XAUUSD), 0.0001 (EURUSD)
self.sc.lot_size             # 0.2
self.sc.threshold            # 1500.0
self.sc.entry_min_multiplier # 1.0
self.sc.entry_max_multiplier # 1.25
self.sc.close_multiplier     # 2.0
self.sc.max_trades_per_day   # 3
self.sc.is_trading_enabled   # True/False
```