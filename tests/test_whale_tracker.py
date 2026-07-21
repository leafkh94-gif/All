import datetime as dt

from strategy import whale_tracker as wt


def _tx(amount_usd, from_type=None, to_type=None):
    return {"amount_usd": amount_usd, "from": {"owner_type": from_type}, "to": {"owner_type": to_type}}


def test_netflow_positive_when_deposits_dominate():
    txs = [_tx(4_000_000, from_type="unknown", to_type="exchange")]
    netflow, signal = wt.compute_exchange_netflow(txs)
    assert netflow == 4_000_000
    assert signal == "bearish"


def test_netflow_negative_when_withdrawals_dominate():
    txs = [_tx(4_000_000, from_type="exchange", to_type="unknown")]
    netflow, signal = wt.compute_exchange_netflow(txs)
    assert netflow == -4_000_000
    assert signal == "bullish"


def test_netflow_nets_multiple_transactions():
    txs = [
        _tx(5_000_000, from_type="unknown", to_type="exchange"),
        _tx(2_000_000, from_type="exchange", to_type="unknown"),
    ]
    netflow, signal = wt.compute_exchange_netflow(txs)
    assert netflow == 3_000_000
    assert signal == "bearish"


def test_netflow_zero_is_neutral():
    assert wt.compute_exchange_netflow([]) == (0.0, "neutral")
    assert wt.compute_exchange_netflow(None) == (0.0, "neutral")


def test_netflow_skips_malformed_entries_without_raising():
    txs = [{"amount_usd": "not-a-number"}, {}, None]
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
    # Strong distribution while looking to BUY should not subtract points --
    # every bonus in scoring_strategy.py is confirmation-only, never punitive.
    pts, tag = wt.whale_flow_bonus("BUY", netflow_usd=5_000_000, threshold=3_000_000)
    assert pts == 0 and tag is None


def test_fetch_returns_empty_without_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "WHALE_ALERT_API_KEY", None)
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_alert.json"))
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    assert wt.fetch_recent_whale_transactions(now) == []


def test_fetch_fails_open_on_request_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "WHALE_ALERT_API_KEY", "test-key")
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_alert.json"))

    def _boom(*a, **k):
        raise wt.requests.RequestException("network down")
    monkeypatch.setattr(wt.requests, "get", _boom)

    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    assert wt.fetch_recent_whale_transactions(now) == []  # never raises


def test_fetch_fails_open_on_malformed_response(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "WHALE_ALERT_API_KEY", "test-key")
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_alert.json"))

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return "not-a-dict"

    monkeypatch.setattr(wt.requests, "get", lambda *a, **k: FakeResponse())
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    assert wt.fetch_recent_whale_transactions(now) == []


def test_fetch_returns_transactions_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "WHALE_ALERT_API_KEY", "test-key")
    monkeypatch.setattr(wt, "CACHE_PATH", str(tmp_path / "whale_alert.json"))
    txs = [_tx(1_000_000, from_type="unknown", to_type="exchange")]

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"transactions": txs}

    calls = []
    monkeypatch.setattr(wt.requests, "get", lambda *a, **k: calls.append(1) or FakeResponse())
    now = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)

    result = wt.fetch_recent_whale_transactions(now)
    assert result == txs
    assert len(calls) == 1

    # second call within the TTL window should hit the cache, not the network
    result2 = wt.fetch_recent_whale_transactions(now)
    assert result2 == txs
    assert len(calls) == 1
