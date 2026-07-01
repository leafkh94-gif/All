import datetime as dt

import market_sessions as ms


def _t(hour, minute=0):
    return dt.datetime(2026, 7, 1, hour, minute, tzinfo=dt.timezone.utc)


def test_london_killzone_us_index():
    assert ms.killzone_bonus(_t(7, 30), "US_INDEX") == (12, "LONDON_KILLZONE")


def test_ny_killzone_btc():
    assert ms.killzone_bonus(_t(13, 0), "CRYPTO") == (10, "NY_KILLZONE")


def test_asian_session():
    assert ms.killzone_bonus(_t(2, 0), "US_INDEX") == (2, "ASIAN_SESSION")
    assert ms.killzone_bonus(_t(2, 0), "CRYPTO") == (3, "ASIAN_SESSION")


def test_dead_zone_us_index():
    assert ms.killzone_bonus(_t(16, 0), "US_INDEX") == (-4, "DEAD_ZONE")


def test_dead_zone_btc():
    assert ms.killzone_bonus(_t(16, 0), "CRYPTO") == (-2, "DEAD_ZONE")


def test_windows_do_not_overlap():
    # every minute of the day must map to exactly one window (or the dead zone)
    seen_minutes = set()
    for name, start, end, *_ in ms.KILLZONES:
        cur = dt.datetime.combine(dt.date(2026, 1, 1), start)
        end_dt = dt.datetime.combine(dt.date(2026, 1, 1), end)
        while cur < end_dt:
            key = (cur.hour, cur.minute)
            assert key not in seen_minutes, f"overlap at {key} in {name}"
            seen_minutes.add(key)
            cur += dt.timedelta(minutes=1)
