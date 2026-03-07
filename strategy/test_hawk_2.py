from __future__ import annotations

from copy import deepcopy

from astra_hawk import (
    AstraHawkStrategy,
    PricePacket,
    PositionInfo,
)
from config.symbols import SYMBOLS


def get_test_symbol(symbol: str, *, enable_trading: bool = True):
    """
    Clone real symbol config from config.symbols.SYMBOLS
    so tests use production values but can override toggles safely.
    """
    sc = deepcopy(SYMBOLS[symbol])
    if enable_trading:
        sc.is_trading_enabled = True
    return sc


def print_result(title, res):
    t = res.telemetry
    print(f"\n=== {title} ===")
    print(f"decision            : {res.decision}")
    print(f"action              : {res.action}")
    print(f"did_signal          : {res.did_signal}")
    print(f"side                : {res.side}")
    print(f"entry_mode          : {res.entry_mode}")
    print(f"entry_price         : {res.entry_price}")
    print(f"exit_price          : {res.exit_price}")
    print(f"present_direction   : {t.get('present_direction')}")
    print(f"candidate_direction : {t.get('candidate_direction')}")
    print(f"committed_direction : {t.get('committed_direction')}")
    print(f"committed_at        : {t.get('direction_committed_at')}")
    print(f"x_up_extreme        : {t.get('x_up_extreme')}")
    print(f"x_dn_extreme        : {t.get('x_dn_extreme')}")
    print(f"x_now               : {t.get('x_now')}")
    print(f"late_armed          : {t.get('late_armed')}")
    print(f"opposite_blocked    : {t.get('opposite_blocked')}")
    print(f"miss_reason         : {t.get('miss_reason')}")


def apply_position(res, pos: PositionInfo):
    if res.decision in ("ENTER_FIRST_LONG", "ENTER_LATE_LONG"):
        pos.in_trade = True
        pos.side = "buy"
        pos.entry_mode = res.entry_mode
        pos.entry_price = res.entry_price
        pos.trades_today += 1

    elif res.decision in ("ENTER_FIRST_SHORT", "ENTER_LATE_SHORT"):
        pos.in_trade = True
        pos.side = "sell"
        pos.entry_mode = res.entry_mode
        pos.entry_price = res.entry_price
        pos.trades_today += 1

    elif res.decision in (
        "EXIT_SECOND_LONG",
        "EXIT_LATE_LONG",
        "EXIT_SECOND_SHORT",
        "EXIT_LATE_SHORT",
    ):
        pos.in_trade = False
        pos.side = "none"
        pos.entry_mode = None
        pos.entry_price = None


def main():
    print("\n==============================")
    print("ASTRA HAWK IMPROVED TEST RUN")
    print("==============================")

    # ------------------------------------------------------------
    # Case 1: Commit LONG first, later short touch is blocked
    # ------------------------------------------------------------
    sc1 = get_test_symbol("XAUUSD", enable_trading=True)
    s1 = AstraHawkStrategy()
    s1.init("XAUUSD", sc1)
    pos1 = PositionInfo()

    pkt1 = PricePacket(
        symbol="XAUUSD",
        date_mt5="2026-02-26",
        server_time="2026-02-26T07:25:00+03:00",
        start_price=5159.52,
        mid=5175.00,
        high=5178.00,
        low=5158.90,
    )
    r1 = s1.on_tick(pkt1, pos1)
    print_result("CASE 1A - FIRST TOUCH COMMITS LONG", r1)

    pkt2 = PricePacket(
        symbol="XAUUSD",
        date_mt5="2026-02-26",
        server_time="2026-02-26T12:00:00+03:00",
        start_price=5159.52,
        mid=5142.00,
        high=5170.00,
        low=5140.00,
    )
    r2 = s1.on_tick(pkt2, pos1)
    print_result("CASE 1B - LATER SHORT SIDE TOUCH SHOULD BE BLOCKED", r2)

    # ------------------------------------------------------------
    # Case 2: Commit SHORT first, later long touch is blocked
    # ------------------------------------------------------------
    sc2 = get_test_symbol("XAUUSD", enable_trading=True)
    s2 = AstraHawkStrategy()
    s2.init("XAUUSD", sc2)
    pos2 = PositionInfo()

    pkt3 = PricePacket(
        symbol="XAUUSD",
        date_mt5="2026-02-26",
        server_time="2026-02-26T08:00:00+03:00",
        start_price=5203.77,
        mid=5188.00,
        high=5200.00,
        low=5187.00,
    )
    r3 = s2.on_tick(pkt3, pos2)
    print_result("CASE 2A - FIRST TOUCH COMMITS SHORT", r3)

    pkt4 = PricePacket(
        symbol="XAUUSD",
        date_mt5="2026-02-26",
        server_time="2026-02-26T15:00:00+03:00",
        start_price=5203.77,
        mid=5220.00,
        high=5222.00,
        low=5185.00,
    )
    r4 = s2.on_tick(pkt4, pos2)
    print_result("CASE 2B - LATER LONG SIDE TOUCH SHOULD BE BLOCKED", r4)

    # ------------------------------------------------------------
    # Case 3: Normal long entry and exit
    # ------------------------------------------------------------
    sc3 = get_test_symbol("EURUSD", enable_trading=True)
    s3 = AstraHawkStrategy()
    s3.init("EURUSD", sc3)
    pos3 = PositionInfo()

    pkt5 = PricePacket(
        symbol="EURUSD",
        date_mt5="2026-03-07",
        server_time="2026-03-07T09:00:00+03:00",
        start_price=1.1000,
        mid=1.1015,
        high=1.1015,
        low=1.1000,
    )
    r5 = s3.on_tick(pkt5, pos3)
    print_result("CASE 3A - ENTER FIRST LONG", r5)
    apply_position(r5, pos3)

    pkt6 = PricePacket(
        symbol="EURUSD",
        date_mt5="2026-03-07",
        server_time="2026-03-07T10:00:00+03:00",
        start_price=1.1000,
        mid=1.10295,
        high=1.10295,
        low=1.1012,
    )
    r6 = s3.on_tick(pkt6, pos3)
    print_result("CASE 3B - EXIT SECOND LONG", r6)
    apply_position(r6, pos3)

    # ------------------------------------------------------------
    # Case 4: Jump-over then late short entry
    # ------------------------------------------------------------
    sc4 = get_test_symbol("EURUSD", enable_trading=True)
    s4 = AstraHawkStrategy()
    s4.init("EURUSD", sc4)
    pos4 = PositionInfo()

    pkt7 = PricePacket(
        symbol="EURUSD",
        date_mt5="2026-03-07",
        server_time="2026-03-07T11:00:00+03:00",
        start_price=1.1000,
        mid=1.0979,
        high=1.1000,
        low=1.0979,
    )
    r7 = s4.on_tick(pkt7, pos4)
    print_result("CASE 4A - SKIP JUMP OVER SHORT ENTRY / ARM LATE", r7)

    pkt8 = PricePacket(
        symbol="EURUSD",
        date_mt5="2026-03-07",
        server_time="2026-03-07T11:05:00+03:00",
        start_price=1.1000,
        mid=1.09815,
        high=1.0982,
        low=1.0979,
    )
    r8 = s4.on_tick(pkt8, pos4)
    print_result("CASE 4B - ENTER LATE SHORT", r8)


if __name__ == "__main__":
    main()