import datetime as dt

import market_sessions as ms


def _t(hour, minute=0):
    return dt.datetime(2026, 7, 1, hour, minute, tzinfo=dt.timezone.utc)


def test_london_killzone_gold():
    assert ms.killzone_bonus(_t(7, 30), "COMMODITY") == (12, "LONDON_KILLZONE")


def test_ny_killzone_gold_covers_the_london_ny_overlap():
    # 13:00-16:00 UTC is gold's prime liquidity window.
    assert ms.killzone_bonus(_t(13, 0), "COMMODITY") == (12, "NY_KILLZONE")
    assert ms.killzone_bonus(_t(15, 30), "COMMODITY") == (12, "NY_KILLZONE")


def test_asian_session_is_a_minor_bonus_for_gold():
    pts, name = ms.killzone_bonus(_t(2, 0), "COMMODITY")
    assert name == "ASIAN_SESSION"
    assert pts == 2


def test_dead_zone_after_ny_killzone_ends():
    assert ms.killzone_bonus(_t(16, 30), "COMMODITY") == (-4, "DEAD_ZONE")


def test_unknown_class_returns_zero_not_a_crash():
    pts, name = ms.killzone_bonus(_t(13, 0), "SOMETHING_NEW")
    assert name == "NY_KILLZONE"
    assert pts == 0


def test_windows_do_not_overlap():
    seen_minutes = set()
    for name, start, end, *_ in ms.KILLZONES:
        cur = dt.datetime.combine(dt.date(2026, 1, 1), start)
        end_dt = dt.datetime.combine(dt.date(2026, 1, 1), end)
        while cur < end_dt:
            key = (cur.hour, cur.minute)
            assert key not in seen_minutes, f"overlap at {key} in {name}"
            seen_minutes.add(key)
            cur += dt.timedelta(minutes=1)


def test_killzone_score_alias_matches_killzone_bonus():
    assert ms.killzone_score(_t(7, 30), "COMMODITY") == ms.killzone_bonus(_t(7, 30), "COMMODITY")
