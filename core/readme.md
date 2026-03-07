# core/logger.py — Centralized Logging

## Overview

Single source of truth for all logging across the trading system.

## Features

- ✅ Colored console output
- ✅ File logging with rotation (10MB max, 5 backups)
- ✅ Separate trade log (`logs/trades.log`)
- ✅ Separate error log (`logs/errors.log`)
- ✅ Configurable via environment variables

## Usage

```python
from core.logger import get_logger

logger = get_logger("executor")
logger.info("Message")
logger.error("Error", exc_info=True)
```

## Trade Logging

```python
from core.logger import log_trade_open, log_trade_close

log_trade_open(
    symbol="XAUUSD",
    side="buy",
    price=5082.52,
    lot=0.2,
    ticket=123456789,
    strategy="astra_hawk",
    mode="ACTIVE",
)

log_trade_close(
    symbol="XAUUSD",
    side="buy",
    entry_price=5082.52,
    exit_price=5095.00,
    profit=12.50,
    ticket=123456789,
    strategy="astra_hawk",
    mode="ACTIVE",
)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_DIR` | `logs` | Directory for log files |
| `LOG_MAX_BYTES` | `10485760` | Max log file size (10MB) |
| `LOG_BACKUP_COUNT` | `5` | Number of backup files to keep |
| `LOG_TO_FILE` | `true` | Enable file logging |
| `LOG_TO_CONSOLE` | `true` | Enable console logging |

## Log Files

```
logs/
├── executor.log     # Main executor logs
├── trade.log        # All trade activity
├── trades.log       # Structured trade events
├── strategy.log     # Strategy decisions
├── pricing.log      # Price feed logs
└── errors.log       # Errors only
```

## Output Format

### Console (Colored)
```
14:35:22 | INFO     | executor        | ✅ [XAUUSD] BUY opened @ 5082.52
14:35:25 | INFO     | executor        | 📊 [XAUUSD] SIM exit @ 5095.00
14:35:30 | ERROR    | trade           | ❌ FOK order failed: requote
```

### File (Plain)
```
2026-03-06 14:35:22 | INFO     | executor        | [XAUUSD] BUY opened @ 5082.52
2026-03-06 14:35:25 | INFO     | executor        | [XAUUSD] SIM exit @ 5095.00
```

### Trade Log (Structured)
```
2026-03-06 14:35:22 | 📈 OPEN | ACTIVE | XAUUSD | BUY | 0.2 lots @ 5082.52 | ticket=123456789 | astra_hawk
2026-03-06 14:35:45 | 🟢 CLOSE | ACTIVE | XAUUSD | BUY | 5082.52 → 5095.00 | P&L: $12.50 | ticket=123456789 | astra_hawk
```