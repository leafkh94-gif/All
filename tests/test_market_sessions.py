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


def test_forex_gets_its_own_tier_not_the_crypto_bucket():
    # Regression guard: FOREX/FOREX_JPY previously fell through to the old
    # binary function's "else" branch and silently got CRYPTO's bonus values.
    assert ms.killzone_bonus(_t(13, 0), "FOREX") == (12, "NY_KILLZONE")
    assert ms.killzone_bonus(_t(13, 0), "FOREX") != ms.killzone_bonus(_t(13, 0), "CRYPTO")


def test_forex_jpy_has_a_real_asian_session_bonus():
    # Unlike plain FOREX, JPY crosses are genuinely active at the Tokyo open.
    pts, name = ms.killzone_bonus(_t(2, 0), "FOREX_JPY")
    assert name == "ASIAN_SESSION"
    assert pts == 10


def test_asia_index_treats_asian_session_as_its_prime_window():
    pts, name = ms.killzone_bonus(_t(2, 0), "ASIA_INDEX")
    assert name == "ASIAN_SESSION"
    assert pts == 12


def test_asia_index_gets_a_minor_bonus_during_london_ny_not_its_prime_tier():
    london_pts, _ = ms.killzone_bonus(_t(7, 30), "ASIA_INDEX")
    ny_pts, _ = ms.killzone_bonus(_t(13, 0), "ASIA_INDEX")
    asian_pts, _ = ms.killzone_bonus(_t(2, 0), "ASIA_INDEX")
    assert london_pts < asian_pts
    assert ny_pts < asian_pts


def test_dead_zone_forex_and_asia_index():
    assert ms.killzone_bonus(_t(16, 0), "FOREX") == (-3, "DEAD_ZONE")
    assert ms.killzone_bonus(_t(16, 0), "FOREX_JPY") == (-3, "DEAD_ZONE")
    assert ms.killzone_bonus(_t(16, 0), "ASIA_INDEX") == (-4, "DEAD_ZONE")


def test_killzone_bonus_falls_back_gracefully_for_unknown_class():
    # Never raises even if called with a class not in the per-window dict.
    pts, name = ms.killzone_bonus(_t(13, 0), "SOMETHING_NEW")
    assert name == "NY_KILLZONE"
    assert pts == 12  # falls back to the US_INDEX tier


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
