from strategy.capital_feed import CapitalFeed


class FakeResponse:
    status_code = 200

    def __init__(self, prices):
        self._prices = prices

    def raise_for_status(self):
        pass

    def json(self):
        return {"prices": self._prices}


def _price(i):
    return {"snapshotTime": f"2026-01-01T00:{i:02d}:00", "openPrice": {"bid": 100.0},
            "highPrice": {"bid": 101.0}, "lowPrice": {"bid": 99.0}, "closePrice": {"bid": 100.5},
            "lastTradedVolume": 1000}


def test_get_candles_different_n_same_interval_do_not_collide(tmp_path, monkeypatch):
    # Regression test: a mode whose entry_timeframe matches the fixed "1h"/"4h"
    # context fetch (e.g. swing mode's 1h entries alongside the always-fetched
    # "h1" bias candles) must not have one request's candle count silently
    # truncate the other's cached result, since both share the same
    # instrument+interval.
    feed = CapitalFeed(api_key="k", email="e", password="p", cache_dir=str(tmp_path))
    feed._cst, feed._token = "cst", "token"
    feed._epics["US500"] = "EPIC"

    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(params["max"])
        return FakeResponse([_price(i) for i in range(params["max"])])

    monkeypatch.setattr("strategy.capital_feed.requests.get", fake_get)

    small = feed.get_candles("US500", "1h", n=80)
    large = feed.get_candles("US500", "1h", n=160)
    assert len(small) == 80
    assert len(large) == 160
    assert calls == [80, 160]  # both hit the network -- no cache collision

    # a repeat request for n=80 within the TTL should now hit the cache
    cached_small = feed.get_candles("US500", "1h", n=80)
    assert len(cached_small) == 80
    assert calls == [80, 160]  # no third network call


def test_get_candles_prefers_snapshot_time_utc_when_present(tmp_path, monkeypatch):
    """snapshotTime isn't guaranteed to be UTC for every instrument class;
    snapshotTimeUTC is explicit when Capital.com provides it and must win."""
    feed = CapitalFeed(api_key="k", email="e", password="p", cache_dir=str(tmp_path))
    feed._cst, feed._token = "cst", "token"
    feed._epics["US500"] = "EPIC"

    price = _price(0)
    price["snapshotTimeUTC"] = "2026-01-01T04:00:00"

    def fake_get(url, headers=None, params=None, timeout=None):
        return FakeResponse([price])

    monkeypatch.setattr("strategy.capital_feed.requests.get", fake_get)

    candles = feed.get_candles("US500", "1h", n=1)
    assert candles[0]["t"] == "2026-01-01T04:00:00"


def test_get_candles_falls_back_to_snapshot_time_without_utc_field(tmp_path, monkeypatch):
    feed = CapitalFeed(api_key="k", email="e", password="p", cache_dir=str(tmp_path))
    feed._cst, feed._token = "cst", "token"
    feed._epics["BTCUSD"] = "EPIC"

    def fake_get(url, headers=None, params=None, timeout=None):
        return FakeResponse([_price(0)])

    monkeypatch.setattr("strategy.capital_feed.requests.get", fake_get)

    candles = feed.get_candles("BTCUSD", "1h", n=1)
    assert candles[0]["t"] == "2026-01-01T00:00:00"
