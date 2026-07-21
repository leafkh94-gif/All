import datetime as dt

from strategy import whale_tracker as wt

ADDR = "17BkMzRJt3XjEqp3TTmjmoj4dCZdjB9NEk"


def _tx(amount_usd, from_side=None, to_side=None, monitored=ADDR):
    return {"amount_usd": amount_usd, "from": from_side, "to": to_side, "_monitored_address": monitored}


def test_netflow_positive_when_deposit_into_monitored_address():
    txs = [_tx(4_000_000, from_side="unknown-addr", to_side=ADDR)]
    netflow, signal = wt.compute_exchange_netflow(txs)
    assert netflow == 4_000_000
    assert signal == "bearish"


def test_netflow_negative_when_withdrawal_from_monitored_address():
    txs = [_tx(4_000_000, from_side=ADDR, to_side="unknown-addr")]
    netflow, signal = wt.compute_exchange_netflow(txs)
    assert netflow == -4_000_000
    assert signal == "bullish"


def test_netflow_handles_address_as_dict_shape():
    txs = [_tx(4_000_000, from_side="unknown-addr", to_side={"address": ADDR})]
    netflow, signal = wt.compute_exchange_netflow(txs)
    assert netflow == 4_000_000
    assert signal == "bearish"


def test_netflow_nets_multiple_transactions():
    txs = [
        _tx(5_000_000, from_side="unknown-addr", to_side=ADDR),
        _tx(2_000_000, from_side=ADDR, to_side="unknown-addr"),
    ]
    netflow, signal = wt.compute_exchange_netflow(txs)
    assert netflow == 3_000_000
    assert signal == "bearish"


def test_netflow_ignores_transaction_not_touching_monitored_address():
    txs = [_tx(4_000_000, from_side="unknown-a", to_side="unknown-b")]
    netflow, signal = wt.compute_exchange_netflow(txs)
    assert netflow == 0.0
    assert signal == "neutral"


def test_netflow_zero_is_neutral():
    assert wt.compute_exchange_netflow([]) == (0.0, "neutral")
    assert wt.compute_exchange_netflow(None) == (0.0, "neutral")


def test_netflow_skips_malformed_entries_without_raising():
    txs = [{"amount_usd": "not-a-number", "_monitored_address": ADDR}, {}, None]
    netflow, signal = wt.compute_exchange_netflow(txs)
    assert netflow == 0.0
    assert signal == "neutral"


def test_netflow_skips_entries_missing_monitored_tag():
    txs = [{"amount_usd": 4_000_000, "from": "a", "to": "b"}]
    netflow, signal = wt.compute_exchange_netflow(txs)
    assert netflow == 0.0
    assert signal == "neutral"


def test_whale_flow_bonus_fires_on_strong_accumulation_for_buy():
    pts, tag = wt.whale_flow_bonus("BUY", netflow_usd=-5_000_000, threshold=3_000_000)
    assert pts == wt.cfg.WHALE_FLOW_BONUS
    assert tag == "whale_accumulation"


def test_whale_flow_bonus_fires_on_strong_distribution_for_sell():
    pts, tag = wt.whale_flow_bonus("SELL", netflow_usd=5_000_000, threshold=3_000_000)
    assert pts == wt.cfg.WHALE_FLOW_BONUS
    assert tag == "whale_distribution"


def test_whale_flow_bonus_is_zero_when_below_threshold():
    pts, tag = wt.whale_flow_bonus("BUY", netflow_usd=-1_000_000, threshold=3_000_000)
    assert pts == 0 and tag is None


def test_whale_flow_bonus_is_zero_never_a_penalty_when_contradicting_direction():
    pts, tag = wt.whale_flow_bonus("BUY", netflow_usd=5_000_000, threshold=3_000_000)
    assert pts == 0 and tag is None


def test_fetch_returns_empty_without_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "WHALE_ALERT_API_KEY", None)
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_alert.json"))
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    assert wt.fetch_recent_whale_transactions(now, addresses=[ADDR]) == []


def test_fetch_returns_empty_without_any_addresses_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "WHALE_ALERT_API_KEY", "test-key")
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_alert.json"))
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    assert wt.fetch_recent_whale_transactions(now, addresses=[]) == []


def test_fetch_fails_open_on_request_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "WHALE_ALERT_API_KEY", "test-key")
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_alert.json"))

    def _boom(*a, **k):
        raise wt.requests.RequestException("network down")
    monkeypatch.setattr(wt.requests, "get", _boom)

    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    assert wt.fetch_recent_whale_transactions(now, addresses=[ADDR]) == []  # never raises


def test_fetch_one_bad_address_does_not_block_the_others(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "WHALE_ALERT_API_KEY", "test-key")
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_alert.json"))

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"transactions": [{"amount_usd": 1_000_000, "from": "x", "to": "good-addr"}]}

    def _get(url, *a, **k):
        if "bad-addr" in url:
            raise wt.requests.RequestException("rate limited")
        return FakeResponse()

    monkeypatch.setattr(wt.requests, "get", _get)
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    result = wt.fetch_recent_whale_transactions(now, addresses=["bad-addr", "good-addr"])
    assert len(result) == 1
    assert result[0]["_monitored_address"] == "good-addr"


def test_fetch_handles_response_as_bare_list_not_wrapped(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "WHALE_ALERT_API_KEY", "test-key")
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_alert.json"))

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"amount_usd": 1_000_000, "from": "x", "to": ADDR}]

    monkeypatch.setattr(wt.requests, "get", lambda *a, **k: FakeResponse())
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    result = wt.fetch_recent_whale_transactions(now, addresses=[ADDR])
    assert len(result) == 1
    assert result[0]["_monitored_address"] == ADDR


def test_fetch_caches_within_ttl(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "WHALE_ALERT_API_KEY", "test-key")
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_alert.json"))

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"transactions": [{"amount_usd": 1_000_000, "from": "x", "to": ADDR}]}

    calls = []
    monkeypatch.setattr(wt.requests, "get", lambda *a, **k: calls.append(1) or FakeResponse())
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)

    result = wt.fetch_recent_whale_transactions(now, addresses=[ADDR])
    assert len(result) == 1
    assert len(calls) == 1

    result2 = wt.fetch_recent_whale_transactions(now, addresses=[ADDR])
    assert result2 == result
    assert len(calls) == 1  # second call hit the cache, not the network
