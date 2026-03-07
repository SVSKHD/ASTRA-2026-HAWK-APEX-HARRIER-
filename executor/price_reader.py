# executor/price_reader.py
from __future__ import annotations

"""
Price Packet Reader — reads price data from pricing module output.

The pricing module writes to: data/price_assembly/<SYMBOL>.json
This module reads those files and returns PricePacket objects.
"""

import os
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# PricePacket — standardized price data for strategies
# ---------------------------------------------------------------------------

@dataclass
class PricePacket:
    """
    Standardized price data consumed by strategies.
    Populated from data/price_assembly/<SYMBOL>.json
    """
    symbol: str

    # Current prices
    mid: float
    bid: float
    ask: float

    # Start price (daily open)
    start_price: Optional[float]
    start_status: str  # "LOCKED" | "PENDING" | "MISSING"

    # Intraday high/low
    high: Optional[float]
    low: Optional[float]

    # Timestamps
    date_mt5: str  # "2026-03-06"
    hhmm_mt5: str  # "14:35"
    server_time: str  # ISO timestamp
    tick_time_epoch: int

    # Stale detection
    is_stale: bool
    stale_seconds: float

    # Raw data for telemetry
    raw: Dict[str, Any]

    def __post_init__(self):
        # Ensure numeric types
        self.mid = float(self.mid) if self.mid else 0.0
        self.bid = float(self.bid) if self.bid else 0.0
        self.ask = float(self.ask) if self.ask else 0.0


# ---------------------------------------------------------------------------
# Reader Functions
# ---------------------------------------------------------------------------

def read_price_packet(
        symbol: str,
        base_dir: str = "data",
        stale_threshold: float = 20.0,
) -> Optional[PricePacket]:
    """
    Read price packet from JSON file written by pricing module.

    Args:
        symbol: Symbol name (e.g., "XAUUSD")
        base_dir: Base data directory
        stale_threshold: Seconds after which packet is considered stale

    Returns:
        PricePacket or None if file not found/invalid
    """
    path = os.path.join(base_dir, "price_assembly", f"{symbol}.json")

    try:
        if not os.path.exists(path):
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return _parse_price_packet(data, stale_threshold)

    except (json.JSONDecodeError, IOError, KeyError) as e:
        # Log but don't crash
        return None


def _parse_price_packet(data: Dict[str, Any], stale_threshold: float) -> Optional[PricePacket]:
    """Parse raw JSON into PricePacket."""
    if data is None:
        return None

    symbol = data.get("symbol", "")

    # Current prices
    current = data.get("current") or {}
    mid = current.get("mid", 0.0)
    bid = current.get("bid", 0.0)
    ask = current.get("ask", 0.0)

    if mid <= 0 and bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0

    # If no valid price, return None
    if mid <= 0:
        return None

    # Start price
    start = data.get("start") or {}
    start_price = start.get("price")
    start_status = start.get("status", "MISSING")

    # Intraday high/low
    high_data = data.get("high") or {}
    low_data = data.get("low") or {}
    high = high_data.get("since_day_start")
    low = low_data.get("since_day_start")

    # Meta / timestamps
    meta = data.get("meta") or {}
    date_mt5 = meta.get("date_mt5", "")
    hhmm_mt5 = meta.get("hhmm_mt5", "")
    server_time = meta.get("server_time", "")

    # Tick time for stale detection
    tick_time_epoch = current.get("tick_time_epoch", 0)
    if not tick_time_epoch:
        timestamps = data.get("timestamps") or {}
        tick_time_epoch = timestamps.get("tick_time_epoch", 0)

    # Stale detection
    now_epoch = int(time.time())
    stale_seconds = now_epoch - tick_time_epoch if tick_time_epoch > 0 else 9999
    is_stale = stale_seconds > stale_threshold

    # Check if meta says it's stale
    if meta.get("is_stale", False):
        is_stale = True
    if meta.get("note") == "NO_TICK":
        is_stale = True

    return PricePacket(
        symbol=symbol,
        mid=mid,
        bid=bid,
        ask=ask,
        start_price=float(start_price) if start_price else None,
        start_status=start_status,
        high=float(high) if high else None,
        low=float(low) if low else None,
        date_mt5=date_mt5,
        hhmm_mt5=hhmm_mt5,
        server_time=server_time,
        tick_time_epoch=tick_time_epoch,
        is_stale=is_stale,
        stale_seconds=stale_seconds,
        raw=data,
    )


def read_all_price_packets(
        symbols: list,
        base_dir: str = "data",
        stale_threshold: float = 20.0,
) -> Dict[str, Optional[PricePacket]]:
    """
    Read price packets for multiple symbols.

    Returns:
        Dict mapping symbol → PricePacket (or None)
    """
    return {
        symbol: read_price_packet(symbol, base_dir, stale_threshold)
        for symbol in symbols
    }


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def get_current_price(symbol: str, base_dir: str = "data") -> Optional[float]:
    """Get current mid price for symbol."""
    pkt = read_price_packet(symbol, base_dir)
    return pkt.mid if pkt else None


def get_start_price(symbol: str, base_dir: str = "data") -> Optional[float]:
    """Get start price for symbol."""
    pkt = read_price_packet(symbol, base_dir)
    return pkt.start_price if pkt else None


def is_price_stale(symbol: str, base_dir: str = "data", threshold: float = 20.0) -> bool:
    """Check if price data is stale."""
    pkt = read_price_packet(symbol, base_dir, threshold)
    return pkt.is_stale if pkt else True