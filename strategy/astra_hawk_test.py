from __future__ import annotations

from copy import deepcopy

from config.symbols import SYMBOLS
from strategy.base import PricePacket, PositionInfo
from strategy.astra_hawk import AstraHawkStrategy


def get_test_symbol(symbol: str, *, enable_trading: bool = True):
    sc = deepcopy(SYMBOLS[symbol])
    if enable_trading:
        sc.is_trading_enabled = True
    return sc


def pkt(
    symbol: str,
    *,
    date_mt5: str,
    hhmm_mt5: str,
    start_price: float,
    mid: float,
    high: float | None = None,
    low: float | None = None,
) -> PricePacket:
    bid = mid
    ask = mid
    return PricePacket(
        symbol=symbol,
        date_mt5=date_mt5,
        hhmm_mt5=hhmm_mt5,
        server_time=f"{date_mt5}T{hhmm_mt5}:00Z",
        mid=mid,
        bid=bid,
        ask=ask,
        start_price=start_price,
        start_status="LOCKED",
        high=high,
        low=low,
        is_stale=False,
        stale_seconds=0,
    )


def flat_pos(
    *,
    trades_today: int = 0,
    daily_done: bool = False,
) -> PositionInfo:
    return PositionInfo(
        in_trade=False,
        side=None,
        entry_price=None,
        entry_time=None,
        entry_mode=None,
        daily_done=daily_done,
        trades_today=trades_today,
    )


def in_trade_pos(
    *,
    side: str,
    entry_price: float,
    entry_mode: str = "normal",
    trades_today: int = 1,
) -> PositionInfo:
    return PositionInfo(
        in_trade=True,
        side=side,
        entry_price=entry_price,
        entry_time="2026-03-07T09:00:00Z",
        entry_mode=entry_mode,
        daily_done=False,
        trades_today=trades_today,
    )


def next_pos_from_result(res, prev: PositionInfo) -> PositionInfo:
    if res.decision in ("ENTER_FIRST_LONG", "ENTER_LATE_LONG"):
        return PositionInfo(
            in_trade=True,
            side="buy",
            entry_price=res.entry_price,
            entry_time=res.now_iso,
            entry_mode=res.entry_mode,
            daily_done=prev.daily_done,
            trades_today=prev.trades_today + 1,
        )

    if res.decision in ("ENTER_FIRST_SHORT", "ENTER_LATE_SHORT"):
        return PositionInfo(
            in_trade=True,
            side="sell",
            entry_price=res.entry_price,
            entry_time=res.now_iso,
            entry_mode=res.entry_mode,
            daily_done=prev.daily_done,
            trades_today=prev.trades_today + 1,
        )

    if res.decision in (
        "EXIT_SECOND_LONG",
        "EXIT_LATE_LONG",
        "EXIT_SECOND_SHORT",
        "EXIT_LATE_SHORT",
    ):
        return PositionInfo(
            in_trade=False,
            side=None,
            entry_price=None,
            entry_time=None,
            entry_mode=None,
            daily_done=prev.daily_done,
            trades_today=prev.trades_today,
        )

    return prev


def show(title: str, res) -> None:
    t = res.telemetry
    print(f"\n=== {title} ===")
    print(f"decision              : {res.decision}")
    print(f"action                : {res.action}")
    print(f"did_signal            : {res.did_signal}")
    print(f"side                  : {res.side}")
    print(f"entry_mode            : {res.entry_mode}")
    print(f"entry_price           : {res.entry_price}")
    print(f"exit_price            : {res.exit_price}")
    print(f"present_direction     : {t.get('present_direction')}")
    print(f"candidate_direction   : {t.get('candidate_direction')}")
    print(f"committed_direction   : {t.get('committed_direction')}")
    print(f"direction_committed_at: {t.get('direction_committed_at')}")
    print(f"x_up_extreme          : {t.get('x_up_extreme')}")
    print(f"x_dn_extreme          : {t.get('x_dn_extreme')}")
    print(f"x_now                 : {t.get('x_now')}")
    print(f"late_armed            : {t.get('late_armed')}")
    print(f"opposite_blocked      : {t.get('opposite_blocked')}")
    print(f"miss_reason           : {t.get('miss_reason')}")


def test_commit_long_then_block_short():
    sc = get_test_symbol("XAUUSD", enable_trading=True)
    s = AstraHawkStrategy()
    s.init("XAUUSD", sc)

    pos = flat_pos()

    r1 = s.on_tick(
        pkt(
            "XAUUSD",
            date_mt5="2026-02-26",
            hhmm_mt5="07:25",
            start_price=5159.52,
            mid=5175.00,
            high=5178.00,
            low=5158.90,
        ),
        pos,
    )
    show("COMMIT LONG", r1)
    assert r1.decision == "ENTER_FIRST_LONG"
    assert r1.telemetry.get("committed_direction") == "long"

    r2 = s.on_tick(
        pkt(
            "XAUUSD",
            date_mt5="2026-02-26",
            hhmm_mt5="12:00",
            start_price=5159.52,
            mid=5142.00,
            high=5170.00,
            low=5140.00,
        ),
        flat_pos(),
    )
    show("BLOCK OPPOSITE SHORT", r2)
    assert r2.telemetry.get("committed_direction") == "long"
    assert r2.telemetry.get("opposite_blocked") is True


def test_commit_short_then_block_long():
    sc = get_test_symbol("XAUUSD", enable_trading=True)
    s = AstraHawkStrategy()
    s.init("XAUUSD", sc)

    r1 = s.on_tick(
        pkt(
            "XAUUSD",
            date_mt5="2026-02-26",
            hhmm_mt5="08:00",
            start_price=5203.77,
            mid=5188.00,
            high=5200.00,
            low=5187.00,
        ),
        flat_pos(),
    )
    show("COMMIT SHORT", r1)
    assert r1.decision == "ENTER_FIRST_SHORT"
    assert r1.telemetry.get("committed_direction") == "short"

    r2 = s.on_tick(
        pkt(
            "XAUUSD",
            date_mt5="2026-02-26",
            hhmm_mt5="15:00",
            start_price=5203.77,
            mid=5220.00,
            high=5222.00,
            low=5185.00,
        ),
        flat_pos(),
    )
    show("BLOCK OPPOSITE LONG", r2)
    assert r2.telemetry.get("committed_direction") == "short"
    assert r2.telemetry.get("opposite_blocked") is True


def test_normal_long_entry_exit():
    sc = get_test_symbol("EURUSD", enable_trading=True)
    s = AstraHawkStrategy()
    s.init("EURUSD", sc)

    pos = flat_pos()

    r1 = s.on_tick(
        pkt(
            "EURUSD",
            date_mt5="2026-03-07",
            hhmm_mt5="09:00",
            start_price=1.1000,
            mid=1.1015,
            high=1.1015,
            low=1.1000,
        ),
        pos,
    )
    show("ENTER FIRST LONG", r1)
    assert r1.decision == "ENTER_FIRST_LONG"

    pos = next_pos_from_result(r1, pos)

    r2 = s.on_tick(
        pkt(
            "EURUSD",
            date_mt5="2026-03-07",
            hhmm_mt5="10:00",
            start_price=1.1000,
            mid=1.10295,
            high=1.10295,
            low=1.1012,
        ),
        pos,
    )
    show("EXIT SECOND LONG", r2)
    assert r2.decision == "EXIT_SECOND_LONG"


def test_jump_over_then_late_short():
    sc = get_test_symbol("EURUSD", enable_trading=True)
    s = AstraHawkStrategy()
    s.init("EURUSD", sc)

    pos = flat_pos()

    r1 = s.on_tick(
        pkt(
            "EURUSD",
            date_mt5="2026-03-07",
            hhmm_mt5="11:00",
            start_price=1.1000,
            mid=1.0979,
            high=1.1000,
            low=1.0979,
        ),
        pos,
    )
    show("JUMP OVER SHORT / ARM LATE", r1)
    assert r1.decision == "SKIP_JUMP_OVER_ENTRY"

    r2 = s.on_tick(
        pkt(
            "EURUSD",
            date_mt5="2026-03-07",
            hhmm_mt5="11:05",
            start_price=1.1000,
            mid=1.09815,
            high=1.0982,
            low=1.0979,
        ),
        pos,
    )
    show("ENTER LATE SHORT", r2)
    assert r2.decision == "ENTER_LATE_SHORT"


def test_state_round_trip():
    sc = get_test_symbol("XAUUSD", enable_trading=True)
    s = AstraHawkStrategy()
    s.init("XAUUSD", sc)

    r1 = s.on_tick(
        pkt(
            "XAUUSD",
            date_mt5="2026-02-26",
            hhmm_mt5="07:25",
            start_price=5159.52,
            mid=5175.00,
            high=5178.00,
            low=5158.90,
        ),
        flat_pos(),
    )
    assert r1.telemetry.get("committed_direction") == "long"

    snap = s.build_state()

    s2 = AstraHawkStrategy()
    s2.init("XAUUSD", sc)
    s2.apply_state(snap)

    assert s2.build_state()["thr_state"]["committed_direction"] == "long"


if __name__ == "__main__":
    print("\n==============================")
    print("ASTRA HAWK TEST RUN")
    print("==============================")

    test_commit_long_then_block_short()
    test_commit_short_then_block_long()
    test_normal_long_entry_exit()
    test_jump_over_then_late_short()
    test_state_round_trip()

    print("\nDONE")