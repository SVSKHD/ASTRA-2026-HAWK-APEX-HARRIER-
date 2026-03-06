# strategy/astra_hawk_test.py
"""
AstraHawk strategy test — verifies all decision paths with simulated XAUUSD prices.

Run:
    python -m strategy.astra_hawk_test

Level reference for XAUUSD (start=3000, pip=0.01, threshold=1500, em=1.0, ex=1.25, ca=2.0):
    t = 1500 * 0.01 = 15.0

    LONG:
      long_first       = 3015.00    (1.0x)
      long_first_max   = 3018.75    (1.25x)
      long_second_close= 3029.98    (2.0x - buf)
      long_late_entry  = 3018.75    (1.25x)
      long_late_entry_max = 3044.90 (3.0x - late_rem)
      long_late_exit_min  = 3043.50 (2.9x)

    SHORT:
      short_first       = 2985.00   (1.0x)
      short_first_min   = 2981.25   (1.25x)
      short_second_close= 2970.02   (2.0x + buf)
      short_late_entry  = 2981.25
      short_late_entry_min = 2955.10
      short_late_exit_min  = 2956.50

    Bias sets when |move| >= thr_price * 0.25 = 3.75
    Reclaim disables late when |mid - start| <= thr_price * 0.10 = 1.50
"""
from __future__ import annotations

import sys


# ---------------------------------------------------------------------------
# Mock SymbolConfig — matches real config/symbols.py XAUUSD
# ---------------------------------------------------------------------------

class _MockSC:
    """Mimics SymbolConfig for XAUUSD."""
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
from strategy.astra_hawk import AstraHawkStrategy

START = 3000.00
DATE  = "2026-03-06"


def _pkt(mid: float, high=None, low=None, date=DATE, start=START) -> PricePacket:
    """Build a PricePacket with sensible defaults."""
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
        high         = high,
        low          = low,
    )


def _flat(**kw) -> PositionInfo:
    return PositionInfo(**kw)


def _in_trade(side: str, entry_price: float, entry_mode: str = "normal",
              trades_today: int = 0) -> PositionInfo:
    return PositionInfo(
        in_trade=True, side=side, entry_price=entry_price,
        entry_mode=entry_mode, trades_today=trades_today,
    )


def _fresh() -> AstraHawkStrategy:
    """Create a fresh strategy instance."""
    s = AstraHawkStrategy()
    s.init("XAUUSD", _MockSC())
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
# Test 1: Normal Long Entry → Exit at 2x
# ---------------------------------------------------------------------------

def test_normal_long_entry_exit():
    print("\n=== TEST: Normal Long Entry → Exit at 2x ===")
    s = _fresh()
    sc = s.sc

    # Tick 1: price drifts up, bias sets to "long"
    # move = |3005 - 3000| = 5.0 >= 3.75 (25% of t=15)
    r = s.on_tick(_pkt(mid=3005.00), _flat())
    check("bias set, below entry", r, "WAIT")
    assert s._thr.bias == "long", f"bias should be long, got {s._thr.bias}"

    # Tick 2: price reaches 1x entry window  (x_up_ext ≈ 1.07)
    r = s.on_tick(_pkt(mid=3016.00), _flat())
    check("normal long entry", r, "ENTER_FIRST_LONG", True)
    assert r.side == "buy"
    assert r.entry_mode == "normal"

    # Tick 3: in trade, price not yet at 2x exit
    r = s.on_tick(_pkt(mid=3020.00), _in_trade("buy", 3016.00))
    check("holding long, below 2x", r, "WAIT")

    # Tick 4: price hits 2x exit (probe_up >= 3029.98)
    r = s.on_tick(_pkt(mid=3030.00), _in_trade("buy", 3016.00))
    check("exit at 2x long", r, "EXIT_SECOND_LONG", True)
    assert r.exit_price == 3030.00


# ---------------------------------------------------------------------------
# Test 2: Normal Short Entry → Exit at 2x
# ---------------------------------------------------------------------------

def test_normal_short_entry_exit():
    print("\n=== TEST: Normal Short Entry → Exit at 2x ===")
    s = _fresh()

    # Tick 1: price drops, bias "short"
    r = s.on_tick(_pkt(mid=2995.00), _flat())
    check("bias set short, below entry", r, "WAIT")
    assert s._thr.bias == "short"

    # Tick 2: price drops to 1x window (x_dn_ext ≈ 1.07)
    r = s.on_tick(_pkt(mid=2984.00), _flat())
    check("normal short entry", r, "ENTER_FIRST_SHORT", True)
    assert r.side == "sell"

    # Tick 3: in trade, holding
    r = s.on_tick(_pkt(mid=2980.00), _in_trade("sell", 2984.00))
    check("holding short", r, "WAIT")

    # Tick 4: price hits 2x exit (probe_dn <= 2970.02)
    r = s.on_tick(_pkt(mid=2970.00), _in_trade("sell", 2984.00))
    check("exit at 2x short", r, "EXIT_SECOND_SHORT", True)


# ---------------------------------------------------------------------------
# Test 3: Jump Over Entry → Late Arm → Late Entry → Late Exit
# ---------------------------------------------------------------------------

def test_late_entry_long():
    print("\n=== TEST: Late Entry Long ===")
    s = _fresh()

    # Tick 1: set bias long
    r = s.on_tick(_pkt(mid=3005.00), _flat())
    check("bias long", r, "WAIT")

    # Tick 2: price jumps past entry_max (x > 1.25 but < 2.0)
    # x_up_ext = (3020 - 3000) / 0.01 / 1500 = 1.333 → between ex(1.25) and ca(2.0)
    r = s.on_tick(_pkt(mid=3020.00), _flat())
    check("jump over entry", r, "SKIP_JUMP_OVER_ENTRY")
    assert s._thr.late_armed, "late should be armed after jump-over"

    # Tick 3: price is in late entry zone
    # late_entry_at_x = 1.25, so need x_now >= 1.25
    # long_late_entry = 3018.75, long_late_entry_max = 3044.90
    # mid=3020 is in [3018.75 - 0.03, 3044.90] range
    r = s.on_tick(_pkt(mid=3020.00), _flat())
    check("late long entry", r, "ENTER_LATE_LONG", True)
    assert r.entry_mode == "late"
    assert not s._thr.late_armed, "late_armed should be cleared after entry"

    # Tick 4: in late trade, waiting for late exit (2.9x = 3043.50)
    r = s.on_tick(_pkt(mid=3035.00), _in_trade("buy", 3020.00, "late"))
    check("holding late long", r, "WAIT")

    # Tick 5: price hits late exit level
    r = s.on_tick(_pkt(mid=3044.00), _in_trade("buy", 3020.00, "late"))
    check("late long exit", r, "EXIT_LATE_LONG", True)


# ---------------------------------------------------------------------------
# Test 4: Direct to Second (price already past 2x)
# ---------------------------------------------------------------------------

def test_direct_to_second():
    print("\n=== TEST: Direct to Second (price already at 2x) ===")
    s = _fresh()

    # Tick 1: price way above start, bias long
    r = s.on_tick(_pkt(mid=3005.00), _flat())
    assert s._thr.bias == "long"

    # Tick 2: price at 2x+ from start (x >= 2.0)
    # x = (3031-3000)/0.01/1500 = 2.067
    r = s.on_tick(_pkt(mid=3031.00), _flat())
    check("direct to second", r, "SKIP_DIRECT_TO_SECOND")


# ---------------------------------------------------------------------------
# Test 5: Guards — daily_done, max_trades, not tradeable
# ---------------------------------------------------------------------------

def test_guards():
    print("\n=== TEST: Guards ===")
    s = _fresh()

    # daily_done blocks new entries
    r = s.on_tick(_pkt(mid=3016.00), _flat(daily_done=True))
    check("daily_done blocks", r, "WAIT")
    assert "daily_done" in r.telemetry.get("miss_reason", "")

    # max_trades reached
    s2 = _fresh()
    s2.on_tick(_pkt(mid=3005.00), _flat())  # set bias
    r = s2.on_tick(_pkt(mid=3016.00), _flat(trades_today=3))
    check("max_trades blocks", r, "WAIT")
    assert "max_trades" in r.telemetry.get("miss_reason", "")

    # not tradeable
    s3 = AstraHawkStrategy()
    s3.init("XAUUSD", _MockSC(is_trading_enabled=False))
    r = s3.on_tick(_pkt(mid=3016.00), _flat())
    check("not tradeable", r, "HALT_NOT_TRADEABLE")


# ---------------------------------------------------------------------------
# Test 6: Day Rollover resets internal state
# ---------------------------------------------------------------------------

def test_day_rollover():
    print("\n=== TEST: Day Rollover ===")
    s = _fresh()

    # Set up bias and crossed_1x
    s.on_tick(_pkt(mid=3005.00), _flat())
    s.on_tick(_pkt(mid=3016.00), _flat())  # enters, crossed_1x=True
    assert s._thr.bias == "long"
    assert s._thr.crossed_1x

    # Rollover
    s.on_new_day(new_start_price=3050.00)
    assert s._thr.bias == "none", "bias should reset on new day"
    assert not s._thr.crossed_1x, "crossed_1x should reset"
    assert not s._thr.late_armed, "late_armed should reset"
    assert s._thr.start_price == 3050.00

    # Strategy should work with new start
    r = s.on_tick(_pkt(mid=3055.00, start=3050.00, date="2026-03-07"), _flat())
    check("new day bias sets", r, "WAIT")
    assert s._thr.bias == "long"


# ---------------------------------------------------------------------------
# Test 7: build_state / apply_state round-trip
# ---------------------------------------------------------------------------

def test_state_persistence():
    print("\n=== TEST: State Persistence Round-Trip ===")
    s = _fresh()

    # Build up some state
    s.on_tick(_pkt(mid=3005.00), _flat())  # bias=long
    s.on_tick(_pkt(mid=3016.00), _flat())  # crossed_1x=True

    # Snapshot
    state = s.build_state()
    assert state["thr_state"]["bias"] == "long"
    assert state["thr_state"]["crossed_1x"] is True
    assert state["date_mt5"] == DATE

    # Restore into fresh instance
    s2 = _fresh()
    s2.apply_state(state)
    assert s2._thr.bias == "long"
    assert s2._thr.crossed_1x is True
    assert s2._date == DATE

    # Should produce same result
    r = s2.on_tick(_pkt(mid=3020.00), _flat())
    # Already crossed_1x and bias=long, x=1.33 > ex=1.25 < ca=2.0
    # → SKIP_JUMP_OVER_ENTRY
    check("restored state produces correct decision", r, "SKIP_JUMP_OVER_ENTRY")
    print(f"  ✅ State round-trip preserved bias={s2._thr.bias} crossed_1x={s2._thr.crossed_1x}")


# ---------------------------------------------------------------------------
# Test 8: Late entry disabled after reclaim (price returns to start)
# ---------------------------------------------------------------------------

def test_reclaim_disables_late():
    print("\n=== TEST: Reclaim Disables Late ===")
    s = _fresh()

    # Set bias and arm late
    s.on_tick(_pkt(mid=3005.00), _flat())  # bias long
    s.on_tick(_pkt(mid=3020.00), _flat())  # jump → late armed
    assert s._thr.late_armed

    # Price reclaims back to near start (within 1.50 of start)
    # reclaim threshold = thr_price * 0.10 = 15 * 0.10 = 1.50
    s.on_tick(_pkt(mid=3001.00), _flat())
    check_val = s._thr.late_disabled_for_day
    assert check_val, "late should be disabled after reclaim"
    assert not s._thr.late_armed, "late_armed should be cleared"
    print(f"  ✅ Reclaim disabled late: late_disabled={check_val}")


# ---------------------------------------------------------------------------
# Test 9: Short late entry flow
# ---------------------------------------------------------------------------

def test_late_entry_short():
    print("\n=== TEST: Late Entry Short ===")
    s = _fresh()

    # Tick 1: bias short
    r = s.on_tick(_pkt(mid=2994.00), _flat())
    check("bias short", r, "WAIT")
    assert s._thr.bias == "short"

    # Tick 2: jump past entry window (x_dn_ext > 1.25 but < 2.0)
    # x = (3000 - 2980) / 0.01 / 1500 = 1.333
    r = s.on_tick(_pkt(mid=2980.00), _flat())
    check("jump over short", r, "SKIP_JUMP_OVER_ENTRY")
    assert s._thr.late_armed

    # Tick 3: price in late short entry zone
    # short_late_entry = 2981.25, short_late_entry_min = 2955.10
    # mid=2980 is in [2955.10, 2981.25 + 0.03=2981.28]
    r = s.on_tick(_pkt(mid=2980.00), _flat())
    check("late short entry", r, "ENTER_LATE_SHORT", True)
    assert r.side == "sell"
    assert r.entry_mode == "late"

    # Tick 4: in trade, price approaches late exit
    r = s.on_tick(_pkt(mid=2960.00), _in_trade("sell", 2980.00, "late"))
    check("holding late short", r, "WAIT")

    # Tick 5: price hits late exit (short_late_exit_min = 2956.50)
    r = s.on_tick(_pkt(mid=2956.00), _in_trade("sell", 2980.00, "late"))
    check("late short exit", r, "EXIT_LATE_SHORT", True)


# ---------------------------------------------------------------------------
# Test 10: High/Low probe detection
# ---------------------------------------------------------------------------

def test_high_low_probe():
    print("\n=== TEST: High/Low Probe Detection ===")
    s = _fresh()

    # Set bias
    s.on_tick(_pkt(mid=3005.00), _flat())

    # Mid is below entry, but HIGH already crossed entry window
    # high=3016 → probe_up=3016 → x_up_ext = 1.067, in [1.0, 1.25]
    r = s.on_tick(_pkt(mid=3010.00, high=3016.00), _flat())
    check("entry via high probe", r, "ENTER_FIRST_LONG", True)

    # Same for exit: mid below 2x but high crosses it
    s2 = _fresh()
    s2.on_tick(_pkt(mid=3005.00), _flat())
    s2.on_tick(_pkt(mid=3016.00), _flat())  # entry tick (for bias/1x)
    r = s2.on_tick(_pkt(mid=3025.00, high=3030.00), _in_trade("buy", 3016.00))
    check("exit via high probe", r, "EXIT_SECOND_LONG", True)


# ---------------------------------------------------------------------------
# Test 11: Multiple entries blocked when already in trade
# ---------------------------------------------------------------------------

def test_entry_while_in_trade():
    print("\n=== TEST: Entry Blocked While In Trade ===")
    s = _fresh()

    # Set bias and get into entry window
    s.on_tick(_pkt(mid=3005.00), _flat())  # bias long

    # Already in trade — entry decision should become WAIT (holding)
    r = s.on_tick(_pkt(mid=3016.00), _in_trade("buy", 3010.00))
    check("entry blocked while in trade", r, "WAIT")
    assert "waiting_for_exit_level" in r.telemetry.get("miss_reason", "")


# ---------------------------------------------------------------------------
# Test 12: Window hit counters track correctly
# ---------------------------------------------------------------------------

def test_window_hit_counters():
    print("\n=== TEST: Window Hit Counters ===")
    s = _fresh()

    # Set bias
    s.on_tick(_pkt(mid=3005.00), _flat())

    # Ticks in long window (3015.00 to 3018.75)
    s.on_tick(_pkt(mid=3016.00), _flat())  # entry triggers, but check counter
    assert s._thr.window_hit_long >= 1, f"window_hit_long={s._thr.window_hit_long}"
    print(f"  ✅ window_hit_long={s._thr.window_hit_long} after entry window tick")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  AstraHawk Strategy Test Suite")
    print("=" * 60)

    test_normal_long_entry_exit()
    test_normal_short_entry_exit()
    test_late_entry_long()
    test_direct_to_second()
    test_guards()
    test_day_rollover()
    test_state_persistence()
    test_reclaim_disables_late()
    test_late_entry_short()
    test_high_low_probe()
    test_entry_while_in_trade()
    test_window_hit_counters()

    print("\n" + "=" * 60)
    print(f"  RESULTS: {_pass} passed, {_fail} failed")
    print("=" * 60)

    if _fail > 0:
        sys.exit(1)
    else:
        print("  🎉 All tests passed!")
        sys.exit(0)