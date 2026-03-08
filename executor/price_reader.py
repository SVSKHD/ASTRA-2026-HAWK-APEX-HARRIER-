from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_PRICE_BASE_DIR = os.path.join(PROJECT_ROOT, "pricing", "data")


@dataclass
class PricePacket:
    symbol: str
    mid: float
    bid: float
    ask: float

    start_price: Optional[float]
    start_status: str

    high: Optional[float]
    low: Optional[float]

    date_mt5: str
    hhmm_mt5: str
    server_time: str
    tick_time_epoch: int

    is_stale: bool
    stale_seconds: float

    raw: Dict[str, Any]

    def __post_init__(self) -> None:
        self.mid = float(self.mid) if self.mid is not None else 0.0
        self.bid = float(self.bid) if self.bid is not None else 0.0
        self.ask = float(self.ask) if self.ask is not None else 0.0


def read_price_packet(
    symbol: str,
    base_dir: str = DEFAULT_PRICE_BASE_DIR,
    stale_threshold: float = 20.0,
) -> Optional[PricePacket]:
    path = os.path.join(base_dir, "price_assembly", f"{symbol}.json")

    try:
        if not os.path.exists(path):
            print(f"[price_reader] missing file: {path}")
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return _parse_price_packet(data, stale_threshold)

    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
        print(f"[price_reader] failed reading {path}: {type(e).__name__}: {e}")
        return None


def _parse_price_packet(data: Dict[str, Any], stale_threshold: float) -> Optional[PricePacket]:
    if not data:
        return None

    symbol = data.get("symbol", "")

    current = data.get("current") or {}
    mid = current.get("mid", 0.0)
    bid = current.get("bid", 0.0)
    ask = current.get("ask", 0.0)

    try:
        mid = float(mid or 0.0)
        bid = float(bid or 0.0)
        ask = float(ask or 0.0)
    except (TypeError, ValueError):
        return None

    if mid <= 0 and bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0

    if mid <= 0:
        return None

    start = data.get("start") or {}
    start_price = start.get("price")
    start_status = start.get("status", "MISSING")

    high_data = data.get("high") or {}
    low_data = data.get("low") or {}
    high = high_data.get("since_day_start")
    low = low_data.get("since_day_start")

    meta = data.get("meta") or {}
    date_mt5 = meta.get("date_mt5", "")
    hhmm_mt5 = meta.get("hhmm_mt5", "")
    server_time = (
        meta.get("server_time")
        or meta.get("updated_utc")
        or meta.get("updated_from_tick_utc")
        or ""
    )

    tick_time_epoch = current.get("tick_time_epoch", 0)
    if not tick_time_epoch:
        timestamps = data.get("timestamps") or {}
        tick_time_epoch = (
            timestamps.get("tick_time_epoch")
            or timestamps.get("current_tick_epoch")
            or 0
        )

    try:
        tick_time_epoch = int(tick_time_epoch or 0)
    except (TypeError, ValueError):
        tick_time_epoch = 0

    now_epoch = int(time.time())
    stale_seconds = float(now_epoch - tick_time_epoch) if tick_time_epoch > 0 else 9999.0
    is_stale = stale_seconds > stale_threshold

    if meta.get("is_stale", False):
        is_stale = True
    if meta.get("note") == "NO_TICK":
        is_stale = True

    try:
        parsed_start_price = float(start_price) if start_price is not None else None
    except (TypeError, ValueError):
        parsed_start_price = None

    try:
        parsed_high = float(high) if high is not None else None
    except (TypeError, ValueError):
        parsed_high = None

    try:
        parsed_low = float(low) if low is not None else None
    except (TypeError, ValueError):
        parsed_low = None

    return PricePacket(
        symbol=symbol,
        mid=mid,
        bid=bid,
        ask=ask,
        start_price=parsed_start_price,
        start_status=start_status,
        high=parsed_high,
        low=parsed_low,
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
    base_dir: str = DEFAULT_PRICE_BASE_DIR,
    stale_threshold: float = 20.0,
) -> Dict[str, Optional[PricePacket]]:
    return {
        symbol: read_price_packet(symbol, base_dir, stale_threshold)
        for symbol in symbols
    }


def get_current_price(symbol: str, base_dir: str = DEFAULT_PRICE_BASE_DIR) -> Optional[float]:
    pkt = read_price_packet(symbol, base_dir)
    return pkt.mid if pkt else None


def get_start_price(symbol: str, base_dir: str = DEFAULT_PRICE_BASE_DIR) -> Optional[float]:
    pkt = read_price_packet(symbol, base_dir)
    return pkt.start_price if pkt else None


def is_price_stale(symbol: str, base_dir: str = DEFAULT_PRICE_BASE_DIR, threshold: float = 20.0) -> bool:
    pkt = read_price_packet(symbol, base_dir, threshold)
    return pkt.is_stale if pkt else True