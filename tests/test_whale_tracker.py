import datetime as dt

from strategy import whale_tracker as wt

ADDR = "17BkMzRJt3XjEqp3TTmjmoj4dCZdjB9NEk"
OTHER_ADDR = "1SomeOtherAddressNotMonitored"


def _entry(net_btc, address=ADDR):
    return {"address": address, "net_btc": net_btc, "block_time": 0}


def _tx(vout=None, vin=None, block_time=1000):
    return {"vout": vout or [], "vin": vin or [], "status": {"confirmed": True, "block_time": block_time}}


def _vout(address, sats):
    return {"scriptpubkey_address": address, "value": sats}


def _vin(address, sats):
    return {"prevout": {"scriptpubkey_address": address, "value": sats}}


def test_address_net_btc_positive_for_deposit_into_address():
    tx = _tx(vout=[_vout(ADDR, 5_00_000_000)], vin=[_vin(OTHER_ADDR, 5_00_000_000)])
    assert wt._address_net_btc(tx, ADDR) == 5.0


def test_address_net_btc_negative_for_withdrawal_from_address():
    tx = _tx(vout=[_vout(OTHER_ADDR, 3_00_000_000)], vin=[_vin(ADDR, 3_00_000_000)])
    assert wt._address_net_btc(tx, ADDR) == -3.0


def test_address_net_btc_sums_multiple_matching_outputs():
    tx = _tx(vout=[_vout(ADDR, 1_00_000_000), _vout(ADDR, 2_00_000_000), _vout(OTHER_ADDR, 1_00_000_000)])
    assert wt._address_net_btc(tx, ADDR) == 3.0


def test_address_net_btc_zero_when_address_not_involved():
    tx = _tx(vout=[_vout(OTHER_ADDR, 1_00_000_000)])
    assert wt._address_net_btc(tx, ADDR) == 0.0


def test_address_net_btc_never_raises_on_malformed_tx():
    assert wt._address_net_btc({}, ADDR) == 0.0
    assert wt._address_net_btc({"vout": None, "vin": None}, ADDR) == 0.0
    assert wt._address_net_btc({"vout": [{"scriptpubkey_address": ADDR, "value": "bad"}]}, ADDR) == 0.0


def test_netflow_positive_when_deposits_dominate():
    entries = [_entry(5.0)]
    netflow, signal = wt.compute_exchange_netflow(entries)
    assert netflow == 5.0
    assert signal == "bearish"


def test_netflow_negative_when_withdrawals_dominate():
    entries = [_entry(-5.0)]
    netflow, signal = wt.compute_exchange_netflow(entries)
    assert netflow == -5.0
    assert signal == "bullish"


def test_netflow_nets_multiple_entries():
    entries = [_entry(10.0), _entry(-3.0)]
    netflow, signal = wt.compute_exchange_netflow(entries)
    assert netflow == 7.0
    assert signal == "bearish"


def test_netflow_zero_is_neutral():
    assert wt.compute_exchange_netflow([]) == (0.0, "neutral")
    assert wt.compute_exchange_netflow(None) == (0.0, "neutral")


def test_netflow_skips_malformed_entries_without_raising():
    entries = [{"net_btc": "not-a-number"}, {}, None]
    netflow, signal = wt.compute_exchange_netflow(entries)
    assert netflow == 0.0
    assert signal == "neutral"


def test_whale_flow_bonus_fires_on_strong_accumulation_for_buy():
    pts, tag = wt.whale_flow_bonus("BUY", netflow_btc=-80.0, threshold=50.0)
    assert pts == wt.cfg.WHALE_FLOW_BONUS
    assert tag == "whale_accumulation"


def test_whale_flow_bonus_fires_on_strong_distribution_for_sell():
    pts, tag = wt.whale_flow_bonus("SELL", netflow_btc=80.0, threshold=50.0)
    assert pts == wt.cfg.WHALE_FLOW_BONUS
    assert tag == "whale_distribution"


def test_whale_flow_bonus_is_zero_when_below_threshold():
    pts, tag = wt.whale_flow_bonus("BUY", netflow_btc=-10.0, threshold=50.0)
    assert pts == 0 and tag is None


def test_whale_flow_bonus_is_zero_never_a_penalty_when_contradicting_direction():
    pts, tag = wt.whale_flow_bonus("BUY", netflow_btc=80.0, threshold=50.0)
    assert pts == 0 and tag is None


def test_fetch_returns_empty_without_any_addresses_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_flow.json"))
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    assert wt.fetch_recent_whale_transactions(now, addresses=[]) == []


def test_fetch_fails_open_on_request_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_flow.json"))

    def _boom(*a, **k):
        raise wt.requests.RequestException("network down")
    monkeypatch.setattr(wt.requests, "get", _boom)

    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    assert wt.fetch_recent_whale_transactions(now, addresses=[ADDR]) == []


def test_fetch_one_bad_address_does_not_block_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_flow.json"))
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    now_ts = int(now.timestamp())

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [_tx(vout=[_vout("good-addr", 1_00_000_000)], block_time=now_ts - 60)]

    def _get(url, *a, **k):
        if "bad-addr" in url:
            raise wt.requests.RequestException("rate limited")
        return FakeResponse()

    monkeypatch.setattr(wt.requests, "get", _get)
    result = wt.fetch_recent_whale_transactions(now, addresses=["bad-addr", "good-addr"])
    assert len(result) == 1
    assert result[0]["address"] == "good-addr"
    assert result[0]["net_btc"] == 1.0


def test_fetch_filters_out_transactions_older_than_lookback(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_flow.json"))
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    now_ts = int(now.timestamp())

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [
                _tx(vout=[_vout(ADDR, 1_00_000_000)], block_time=now_ts - 60),        # within window
                _tx(vout=[_vout(ADDR, 2_00_000_000)], block_time=now_ts - 3600 * 5),  # too old
            ]

    monkeypatch.setattr(wt.requests, "get", lambda *a, **k: FakeResponse())
    result = wt.fetch_recent_whale_transactions(now, addresses=[ADDR], lookback_minutes=60)
    assert len(result) == 1
    assert result[0]["net_btc"] == 1.0


def test_fetch_skips_transactions_missing_block_time(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_flow.json"))
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"vout": [_vout(ADDR, 1_00_000_000)], "vin": [], "status": {"confirmed": False}}]

    monkeypatch.setattr(wt.requests, "get", lambda *a, **k: FakeResponse())
    result = wt.fetch_recent_whale_transactions(now, addresses=[ADDR])
    assert result == []


def test_fetch_caches_within_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_flow.json"))
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    now_ts = int(now.timestamp())

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [_tx(vout=[_vout(ADDR, 1_00_000_000)], block_time=now_ts - 60)]

    calls = []
    monkeypatch.setattr(wt.requests, "get", lambda *a, **k: calls.append(1) or FakeResponse())

    result = wt.fetch_recent_whale_transactions(now, addresses=[ADDR])
    assert len(result) == 1
    assert len(calls) == 1

    result2 = wt.fetch_recent_whale_transactions(now, addresses=[ADDR])
    assert result2 == result
    assert len(calls) == 1  # second call hit the cache, not the network
