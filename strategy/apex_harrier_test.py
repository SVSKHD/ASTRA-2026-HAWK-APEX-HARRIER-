# strategy/apex_harrier_test.py
"""
ApexHarrier strategy test — verifies lifecycle, guards, and state round-trip.

Since apex_harrier is currently a skeleton (returns WAIT for all ticks),
these tests verify:
    - init / on_tick / on_new_day lifecycle
    - Guard: HALT_NOT_TRADEABLE when is_trading_enabled=False
    - Guard: daily_done blocks entries
    - build_state / apply_state round-trip
    - Correct strategy name and symbol tagging
    - Telemetry contains expected fields

Run:
    python -m strategy.apex_harrier_test
"""
from __future__ import annotations

import sys


# ---------------------------------------------------------------------------
# Mock SymbolConfig
# ---------------------------------------------------------------------------

class _MockSC:
    def __init__(self, **overrides):
        self.symbol               = "XAUUSD"
        self.is_enabled           = True
        self.is_trading_enabled   = True
        self.pip_size             = 0.01
        self.lot_size             = 0.2
        self.max_trades_per_day   = 3
        self.threshold            = 1500.0
        self.entry_min_multiplier = 1.0
        self.entry_max_multiplier = 1.25
        self.close_multiplier     = 2.0
        for k, v in overrides.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from strategy.base import PricePacket, PositionInfo
from strategy.apex_harrier import ApexHarrierStrategy

START = 3000.00
DATE  = "2026-03-06"


def _pkt(mid: float, date=DATE, start=START) -> PricePacket:
    return PricePacket(
        symbol       = "XAUUSD",
        date_mt5     = date,
        hhmm_mt5     = "12:00",
        server_time  = f"{date}T12:00:00Z",
        mid          = mid,
        bid          = mid - 0.05,
        ask          = mid + 0.05,
        start_price  = start,
        start_status = "LOCKED",
        high         = None,
        low          = None,
    )


def _flat(**kw) -> PositionInfo:
    return PositionInfo(**kw)


def _fresh(**sc_kw) -> ApexHarrierStrategy:
    s = ApexHarrierStrategy()
    s.init("XAUUSD", _MockSC(**sc_kw))
    return s


_pass = 0
_fail = 0


def check(label: str, result, expected_decision: str, expected_signal: bool = None):
    global _pass, _fail
    ok = result.decision == expected_decision
    if expected_signal is not None:
        ok = ok and (result.did_signal == expected_signal)

    status = "✅" if ok else "❌"
    if not ok:
        _fail += 1
        extra = ""
        if expected_signal is not None and result.did_signal != expected_signal:
            extra = f" did_signal={result.did_signal} expected={expected_signal}"
        print(f"  {status} {label}: got {result.decision}{extra}  (expected {expected_decision})")
    else:
        _pass += 1
        print(f"  {status} {label}: {result.decision}")


# ---------------------------------------------------------------------------
# Test 1: Basic lifecycle — init, on_tick, returns WAIT
# ---------------------------------------------------------------------------

def test_basic_lifecycle():
    print("\n=== TEST: Basic Lifecycle ===")
    s = _fresh()

    assert s.name == "apex_harrier"
    assert s.symbol == "XAUUSD"

    r = s.on_tick(_pkt(mid=3005.00), _flat())
    check("returns WAIT (placeholder)", r, "WAIT", False)
    assert r.strategy == "apex_harrier"
    assert r.symbol == "XAUUSD"
    assert r.now_iso == f"{DATE}T12:00:00Z"


# ---------------------------------------------------------------------------
# Test 2: Guard — HALT_NOT_TRADEABLE
# ---------------------------------------------------------------------------

def test_not_tradeable():
    print("\n=== TEST: HALT_NOT_TRADEABLE ===")
    s = _fresh(is_trading_enabled=False)

    r = s.on_tick(_pkt(mid=3005.00), _flat())
    check("not tradeable", r, "HALT_NOT_TRADEABLE")
    assert not r.did_signal


# ---------------------------------------------------------------------------
# Test 3: Guard — daily_done blocks entries
# ---------------------------------------------------------------------------

def test_daily_done():
    print("\n=== TEST: daily_done blocks ===")
    s = _fresh()

    r = s.on_tick(_pkt(mid=3005.00), _flat(daily_done=True))
    check("daily_done blocks", r, "WAIT")
    assert "daily_done" in r.telemetry.get("miss_reason", "")


# ---------------------------------------------------------------------------
# Test 4: Telemetry contains expected fields
# ---------------------------------------------------------------------------

def test_telemetry():
    print("\n=== TEST: Telemetry ===")
    s = _fresh()

    r = s.on_tick(_pkt(mid=3015.50), _flat())
    tel = r.telemetry

    assert "current" in tel, "telemetry should have 'current'"
    assert "start_price" in tel, "telemetry should have 'start_price'"
    assert "decision" in tel, "telemetry should have 'decision'"
    assert tel["current"] == 3015.50
    assert tel["start_price"] == START
    print(f"  ✅ Telemetry keys: {sorted(tel.keys())}")


# ---------------------------------------------------------------------------
# Test 5: build_state / apply_state round-trip
# ---------------------------------------------------------------------------

def test_state_persistence():
    print("\n=== TEST: State Persistence Round-Trip ===")
    s = _fresh()

    # Run a tick so _date gets set
    s.on_tick(_pkt(mid=3005.00), _flat())
    assert s._date == DATE

    # Snapshot
    state = s.build_state()
    assert state["date_mt5"] == DATE

    # Restore into fresh instance
    s2 = _fresh()
    assert s2._date is None
    s2.apply_state(state)
    assert s2._date == DATE

    # Should still produce same decision
    r = s2.on_tick(_pkt(mid=3005.00), _flat())
    check("restored state works", r, "WAIT")
    print(f"  ✅ State round-trip: date={s2._date}")


# ---------------------------------------------------------------------------
# Test 6: on_new_day resets internal state
# ---------------------------------------------------------------------------

def test_day_rollover():
    print("\n=== TEST: Day Rollover ===")
    s = _fresh()

    # Set date
    s.on_tick(_pkt(mid=3005.00), _flat())
    assert s._date == DATE

    # Rollover
    s.on_new_day(new_start_price=3050.00)
    assert s._date is None, "_date should reset on new day"

    # Next tick should set new date
    r = s.on_tick(_pkt(mid=3055.00, start=3050.00, date="2026-03-07"), _flat())
    check("new day tick", r, "WAIT")
    assert s._date == "2026-03-07"
    print(f"  ✅ After rollover: date={s._date}")


# ---------------------------------------------------------------------------
# Test 7: Multiple ticks stay consistent
# ---------------------------------------------------------------------------

def test_multiple_ticks():
    print("\n=== TEST: Multiple Ticks ===")
    s = _fresh()

    decisions = []
    for price in [3000, 3005, 3010, 3015, 3020, 3025, 3030]:
        r = s.on_tick(_pkt(mid=float(price)), _flat())
        decisions.append(r.decision)

    # All should be WAIT since it's a placeholder
    all_wait = all(d == "WAIT" for d in decisions)
    status = "✅" if all_wait else "❌"
    print(f"  {status} 7 ticks all returned WAIT: {all_wait}")
    if all_wait:
        global _pass
        _pass += 1
    else:
        global _fail
        _fail += 1


# ---------------------------------------------------------------------------
# Test 8: Different symbols
# ---------------------------------------------------------------------------

def test_different_symbol():
    print("\n=== TEST: Different Symbol ===")
    s = ApexHarrierStrategy()
    sc = _MockSC(symbol="EURUSD", pip_size=0.0001, threshold=15.0)
    s.init("EURUSD", sc)

    pkt = PricePacket(
        symbol="EURUSD", date_mt5=DATE, hhmm_mt5="12:00",
        server_time=f"{DATE}T12:00:00Z",
        mid=1.0850, bid=1.0849, ask=1.0851,
        start_price=1.0800, start_status="LOCKED",
        high=None, low=None,
    )

    r = s.on_tick(pkt, _flat())
    check("EURUSD returns WAIT", r, "WAIT")
    assert r.symbol == "EURUSD"
    assert r.strategy == "apex_harrier"
    print(f"  ✅ Symbol={r.symbol} strategy={r.strategy}")


# ---------------------------------------------------------------------------
# Test 9: apply_state handles corrupt data gracefully
# ---------------------------------------------------------------------------

def test_corrupt_state():
    print("\n=== TEST: Corrupt State Recovery ===")
    s = _fresh()

    # Feed it garbage — should not crash
    s.apply_state({"garbage": True, "nested": {"bad": "data"}})
    assert s._date is None  # should fall back to None gracefully

    # Should still work normally
    r = s.on_tick(_pkt(mid=3005.00), _flat())
    check("works after corrupt state", r, "WAIT")
    print(f"  ✅ Graceful recovery from corrupt state")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  ApexHarrier Strategy Test Suite")
    print("=" * 60)

    test_basic_lifecycle()
    test_not_tradeable()
    test_daily_done()
    test_telemetry()
    test_state_persistence()
    test_day_rollover()
    test_multiple_ticks()
    test_different_symbol()
    test_corrupt_state()

    print("\n" + "=" * 60)
    print(f"  RESULTS: {_pass} passed, {_fail} failed")
    print("=" * 60)

    if _fail > 0:
        sys.exit(1)
    else:
        print("  🎉 All tests passed!")
        sys.exit(0)