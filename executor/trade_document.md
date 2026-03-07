# INTEGRATION.md
# Wiring trade.py into executor/runner.py

## Overview

Your `executor/runner.py` has 4 stub functions that need to be replaced with real `trade.py` calls.
This guide shows exactly what to change.

---

## Step 1: Import trade.py

Add at the top of `executor/runner.py`:

```python
# executor/runner.py

# Add this import (trade.py is in same folder)
from .trade import (
    place_market_order_fok,
    close_all_positions_fok,
    get_positions_snapshot,
    get_realized_profit_since,
    calc_profit,
)
```

Or if running as script (not package):
```python
from executor.trade import (
    place_market_order_fok,
    close_all_positions_fok,
    get_positions_snapshot,
    get_realized_profit_since,
    calc_profit,
)
```

---

## Step 2: Replace Stub Functions

### 2.1 Replace `_place_order()`

**BEFORE (stub):**
```python
def _place_order(symbol: str, side: str, comment: str) -> Dict[str, Any]:
    """Stub. Replace with real trade.py call."""
    sc = SYMBOLS.get(symbol)
    return {
        "retcode": TRADE_RETCODE_DONE,
        "price":   0.0,
        "volume":  sc.lot_size if sc else 0.01,
        "comment": comment,
        "_stub":   True,
    }
```

**AFTER (real):**
```python
def _place_order(symbol: str, side: str, comment: str) -> Dict[str, Any]:
    """
    Place FOK market order via trade.py.
    Lot size is pulled from SymbolConfig.
    """
    sc = SYMBOLS.get(symbol)
    lot = sc.lot_size if sc else 0.01
    
    result = place_market_order_fok(
        symbol=symbol,
        side=side,
        lot=lot,
        comment=comment,
    )
    
    return result
```

---

### 2.2 Replace `_close_positions()`

**BEFORE (stub):**
```python
def _close_positions(symbol: str, comment: str) -> Dict[str, Any]:
    """Stub. Replace with real trade.py call."""
    return {"retcode": TRADE_RETCODE_DONE, "closed": True, "_stub": True}
```

**AFTER (real):**
```python
def _close_positions(symbol: str, comment: str) -> Dict[str, Any]:
    """
    Close all positions for symbol via FOK.
    """
    result = close_all_positions_fok(
        symbol=symbol,
        comment=comment,
    )
    
    return result
```

---

### 2.3 Replace `_get_floating_pnl()`

**BEFORE (stub):**
```python
def _get_floating_pnl(symbol: str) -> float:
    """Stub. Replace with real trade.py call."""
    return 0.0
```

**AFTER (real):**
```python
def _get_floating_pnl(symbol: str) -> float:
    """
    Get unrealized P&L for symbol from open positions.
    """
    snap = get_positions_snapshot(symbol=symbol)
    return float(snap.get("total_profit_usd", 0.0))
```

---

### 2.4 Replace `_get_realized_pnl()`

**BEFORE (stub):**
```python
def _get_realized_pnl(symbol: str) -> float:
    """Stub. Replace with real trade.py call."""
    return 0.0
```

**AFTER (real):**
```python
def _get_realized_pnl(symbol: str) -> float:
    """
    Get realized P&L since day start (00:00 UTC).
    """
    return get_realized_profit_since(symbol=symbol)
```

---

## Step 3: Update _sim_pnl for BACKTEST mode

**BEFORE:**
```python
def _sim_pnl(symbol: str, side, entry, current: float) -> float:
    if entry is None:
        return 0.0
    sc = SYMBOLS.get(symbol)
    if sc is None or sc.pip_size <= 0:
        return 0.0
    pips = ((current - entry) if side == "buy" else (entry - current)) / sc.pip_size
    return round(pips * sc.lot_size * 10.0, 2)
```

**AFTER (using MT5 order_calc_profit):**
```python
def _sim_pnl(symbol: str, side, entry, current: float) -> float:
    """
    Calculate simulated P&L using MT5's order_calc_profit.
    More accurate than manual pip calculation.
    """
    if entry is None:
        return 0.0
    
    sc = SYMBOLS.get(symbol)
    lot = sc.lot_size if sc else 0.01
    
    return calc_profit(
        symbol=symbol,
        side=side,
        lot=lot,
        open_price=entry,
        close_price=current,
    )
```

---

## Complete Updated Stubs Section

Here's the complete replacement for the stubs section in `executor/runner.py`:

```python
# ---------------------------------------------------------------------------
# MT5 Trade Calls (previously stubs, now using trade.py)
# ---------------------------------------------------------------------------

from .trade import (
    place_market_order_fok,
    close_all_positions_fok,
    get_positions_snapshot,
    get_realized_profit_since,
    calc_profit,
)


def _place_order(symbol: str, side: str, comment: str) -> Dict[str, Any]:
    """Place FOK market order. Lot size from SymbolConfig."""
    sc = SYMBOLS.get(symbol)
    lot = sc.lot_size if sc else 0.01
    
    return place_market_order_fok(
        symbol=symbol,
        side=side,
        lot=lot,
        comment=comment,
    )


def _close_positions(symbol: str, comment: str) -> Dict[str, Any]:
    """Close all positions for symbol via FOK."""
    return close_all_positions_fok(symbol=symbol, comment=comment)


def _get_floating_pnl(symbol: str) -> float:
    """Get unrealized P&L from open positions."""
    snap = get_positions_snapshot(symbol=symbol)
    return float(snap.get("total_profit_usd", 0.0))


def _get_realized_pnl(symbol: str) -> float:
    """Get realized P&L since day start."""
    return get_realized_profit_since(symbol=symbol)


def _sim_pnl(symbol: str, side, entry, current: float) -> float:
    """Calculate P&L using MT5 order_calc_profit."""
    if entry is None:
        return 0.0
    sc = SYMBOLS.get(symbol)
    lot = sc.lot_size if sc else 0.01
    return calc_profit(symbol, side, lot, entry, current)
```

---

## Step 4: File Placement

Place `trade.py` in the `executor/` folder:

```
astra-hawk-2026/
├── config/
│   └── symbols.py
├── executor/
│   ├── __init__.py
│   ├── runner.py      ← Update imports here
│   ├── engine.py
│   ├── price_reader.py
│   └── trade.py       ← ADD HERE
├── strategy/
├── pricing/
├── notify/
└── env.py
```

---

## Step 5: Testing

### Test trade.py independently:
```bash
# Basic health check
python test_trade.py --test health

# Test profit calculation
python test_trade.py --test calc

# Test simulator
python test_trade.py --test sim

# Test real FOK order (careful!)
python test_trade.py --test fok --symbol XAUUSD --lot 0.01
```

### Test with executor in MONITOR_ONLY mode first:
```python
# In your runner, set:
MODE = "MONITOR_ONLY"  # No real trades, but logs what would happen
```

### Then switch to ACTIVE:
```python
MODE = "ACTIVE"  # Real trades
```

---

## API Reference

### place_market_order_fok()
```python
result = place_market_order_fok(
    symbol="XAUUSD",
    side="buy",           # "buy" or "sell"
    lot=0.2,              # lot size (optional, uses config default)
    comment="ASTRA_HAWK", # order comment
    sl=0.0,               # stop loss price (0 = none)
    tp=0.0,               # take profit price (0 = none)
    magic=100001,         # magic number (optional)
)

# Returns:
{
    "success": True,
    "retcode": 10009,
    "ticket": 123456789,
    "symbol": "XAUUSD",
    "side": "buy",
    "volume": 0.2,
    "price": 5082.52,
    "error": "",
    "attempts": 1,
    "_stub": False,
}
```

### close_all_positions_fok()
```python
result = close_all_positions_fok(
    symbol="XAUUSD",      # None = all symbols
    comment="CLOSE",
    magic=100001,         # None = all magic numbers
)

# Returns:
{
    "retcode": 10009,
    "closed": True,
    "total": 1,
    "failed": 0,
    "total_profit": 15.50,
    "results": [...],
}
```

### get_positions_snapshot()
```python
snap = get_positions_snapshot(symbol="XAUUSD")

# Returns:
{
    "total_profit_usd": 25.50,
    "count": 1,
    "positions": [
        {
            "ticket": 123456789,
            "symbol": "XAUUSD",
            "type": "buy",
            "volume": 0.2,
            "price_open": 5080.00,
            "price_current": 5082.52,
            "profit": 25.50,
            ...
        }
    ]
}
```

### calc_profit()
```python
# Uses MT5 order_calc_profit internally
profit = calc_profit(
    symbol="XAUUSD",
    side="buy",
    lot=0.2,
    open_price=5080.00,
    close_price=5095.00,
)
# Returns: 150.0 (in account currency)
```

---

## Retry Behavior

The FOK execution automatically retries on these conditions:
- Requote (10004)
- Price changed (10020)
- Price off (10021)
- Connection issues (10031)
- Too many requests (10024)

Default: 3 attempts with 0.3s delay between each.

Configure in trade.py:
```python
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 0.3
```

---

## Checklist

- [ ] Copy `trade.py` to project root
- [ ] Copy `test_trade.py` to project root (optional)
- [ ] Update imports in `executor/runner.py`
- [ ] Replace 4 stub functions
- [ ] Test with `python test_trade.py --test health`
- [ ] Test with `python test_trade.py --test calc`
- [ ] Run executor in MONITOR_ONLY mode
- [ ] Switch to ACTIVE mode when ready