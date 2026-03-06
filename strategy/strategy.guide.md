# How to Add a New Strategy

## Architecture

```
strategy/                         executor/
├── base.py          ← ABC       ├── runner.py     ← reads price, feeds strategy,
├── loader.py        ← registry  │                    manages state, calls engine
├── astra_hawk.py    ← live      ├── engine.py     ← places MT5 orders
├── apex_harrier.py  ← skeleton  └── price_reader  ← builds PricePacket from JSON
└── your_strategy.py ← new

Strategy is a PURE DECISION MAKER:
  - Receives: PricePacket (price) + PositionInfo (executor-owned state)
  - Returns:  StrategyResult (decision string + telemetry)
  - Manages:  internal tracking state only (bias, flags, counters)
  - NEVER:    touches MT5, files, notifications, or position state
```

## Data flow per tick

```
executor/runner.py:
  1. Read data/price_assembly/<SYMBOL>.json → PricePacket
  2. Check config/symbols.py: is_trading_enabled, strategies list
  3. Build PositionInfo from EngineState
  4. Call strategy.on_tick(pkt, pos) → StrategyResult
  5. If did_signal=True → engine.handle_signal() → MT5 order
  6. Update EngineState from fill result
  7. Call strategy.build_state() → persist to disk
```

---

## Step 1 — Create strategy/your_strategy.py

```python
from __future__ import annotations
from typing import Any, Dict, Optional
from .base import BaseStrategy, StrategyResult, PricePacket, PositionInfo


class YourStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "your_strategy"   # must match loader + config toggle

    # ── lifecycle ──────────────────────────────────────────────────────

    def init(self, symbol: str, sc) -> None:
        super().init(symbol, sc)
        self._date = None
        # init your internal tracking state here
        # (NOT position/trade state — that belongs to executor)

    def on_new_day(self, new_start_price: float) -> None:
        self._date = None
        # reset internal tracking for new day

    # ── state serialization (executor calls these) ─────────────────────

    def build_state(self) -> Dict[str, Any]:
        # return internal tracking state — executor persists this
        return {"date_mt5": self._date}

    def apply_state(self, data: Dict[str, Any]) -> None:
        # restore from saved dict — called once on startup
        try:
            self._date = data.get("date_mt5")
        except Exception:
            self._date = None

    # ── core logic ─────────────────────────────────────────────────────

    def on_tick(self, pkt: PricePacket, pos: PositionInfo) -> StrategyResult:
        sc = self.sc

        if self._date is None:
            self._date = pkt.date_mt5

        # your logic here:
        #   pkt.mid, pkt.start_price, pkt.high, pkt.low  — price data
        #   pos.in_trade, pos.side, pos.entry_price       — executor position
        #   sc.threshold, sc.pip_size, sc.lot_size         — config params

        return StrategyResult(
            strategy   = self.name,
            symbol     = self.symbol,
            decision   = "WAIT",
            action     = "waiting",
            did_signal = False,
            in_trade   = pos.in_trade,
            now_iso    = pkt.server_time,
            telemetry  = {"mid": pkt.mid, "start_price": pkt.start_price},
        )
```

---

## Step 2 — Register in strategy/loader.py

```python
def _load_your_strategy():
    from .your_strategy import YourStrategy
    return YourStrategy

_LOADERS: Dict[str, Callable] = {
    "astra_hawk":    _load_astra_hawk,
    "apex_harrier":  _load_apex_harrier,
    "your_strategy": _load_your_strategy,   # ← add this
}
```

---

## Step 3 — Add toggle to config/symbols.py

```python
class SymbolConfig:
    def __init__(self, ..., use_your_strategy: bool = False, ...):
        ...
        self.use_your_strategy = use_your_strategy

    @property
    def strategies(self) -> Tuple[str, ...]:
        active = []
        ...
        if self.use_your_strategy: active.append("your_strategy")
        return tuple(active)
```

---

## Step 4 — Enable per symbol

```python
SYMBOLS = {
    "XAUUSD": SymbolConfig(..., use_your_strategy=True),
    "EURUSD": SymbolConfig(..., use_your_strategy=False),
}
```

**Done.** Executor discovers it automatically.

---

## What strategy receives

### PricePacket (from price_assembly JSON)
```
pkt.symbol          "XAUUSD"
pkt.date_mt5        "2026-03-06"
pkt.hhmm_mt5        "14:35"
pkt.server_time     ISO timestamp
pkt.mid / bid / ask  current prices
pkt.start_price     day open (locked at 00:00)
pkt.start_status    "LOCKED" | "PENDING" | "NONE"
pkt.high / low      intraday extremes (or None)
pkt.is_stale        bool
pkt.stale_seconds   int
```

### PositionInfo (from executor)
```
pos.in_trade        bool — am I in a position?
pos.side            "buy" | "sell" | None
pos.entry_price     float | None
pos.entry_time      ISO string | None
pos.entry_mode      "normal" | "late" | None
pos.daily_done      bool — done trading for today?
pos.trades_today    int
```

### SymbolConfig (from config)
```
sc.pip_size               0.01 (XAUUSD)
sc.lot_size               0.2
sc.threshold              1500.0
sc.entry_min_multiplier   1.0
sc.entry_max_multiplier   1.25
sc.close_multiplier       2.0
sc.max_trades_per_day     3
sc.is_trading_enabled     True/False
```

---

## Decision vocabulary

| decision                | meaning                              | did_signal |
|-------------------------|--------------------------------------|------------|
| `WAIT`                  | no signal this tick                  | False      |
| `ENTER_FIRST_LONG`      | normal long entry (1x window)       | True       |
| `ENTER_FIRST_SHORT`     | normal short entry                  | True       |
| `ENTER_LATE_LONG`       | late long entry (after jump-over)   | True       |
| `ENTER_LATE_SHORT`      | late short entry                    | True       |
| `EXIT_SECOND_LONG`      | normal long exit (2x target)        | True       |
| `EXIT_SECOND_SHORT`     | normal short exit                   | True       |
| `EXIT_LATE_LONG`        | late long exit                      | True       |
| `EXIT_LATE_SHORT`       | late short exit                     | True       |
| `SKIP_JUMP_OVER_ENTRY`  | price jumped past entry window      | False      |
| `SKIP_DIRECT_TO_SECOND` | price already past 2x               | False      |
| `SKIP_LATE_TOO_FAR`     | late armed but overshot exit        | False      |
| `HALT_NOT_TRADEABLE`    | symbol disabled in config           | False      |

New decision strings for new strategies: add them to
`executor/engine.py` `ENTRY_DECISIONS` / `EXIT_DECISIONS` sets.

---

## What strategy DOES NOT do

- ❌ Read/write files
- ❌ Import MetaTrader5
- ❌ Send notifications
- ❌ Track position state (executor owns in_trade, side, entry_price)
- ❌ Persist its own state (executor calls build_state/apply_state)
- ❌ Know about other strategies running on the same symbol