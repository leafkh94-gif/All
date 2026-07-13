import datetime as dt

from strategy import news_calendar as nc


def _event(now, offset_minutes, impact="High", event="CPI"):
    t = now + dt.timedelta(minutes=offset_minutes)
    return {"event": event, "time": t.isoformat(), "impact": impact, "country": "USD"}


def test_blackout_active_before_event():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [_event(now, offset_minutes=10)]  # event fires in 10 min
    active, name = nc.is_news_blackout_active(now, events, before_minutes=15, after_minutes=15)
    assert active is True
    assert name == "CPI"


def test_blackout_active_after_event():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [_event(now, offset_minutes=-10)]  # event fired 10 min ago
    active, _ = nc.is_news_blackout_active(now, events, before_minutes=15, after_minutes=15)
    assert active is True


def test_blackout_inactive_outside_window():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [_event(now, offset_minutes=60)]  # an hour away
    active, name = nc.is_news_blackout_active(now, events, before_minutes=15, after_minutes=15)
    assert active is False
    assert name is None


def test_blackout_ignores_below_min_impact():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [_event(now, offset_minutes=5, impact="Medium")]
    active, _ = nc.is_news_blackout_active(now, events, before_minutes=15, after_minutes=15, min_impact="High")
    assert active is False


def test_blackout_never_raises_on_malformed_event():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [{"event": "broken", "impact": "High"}, {"impact": "High", "time": "not-a-date"}]
    active, name = nc.is_news_blackout_active(now, events)
    assert active is False
    assert name is None


def test_blackout_empty_events_is_inactive():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    assert nc.is_news_blackout_active(now, []) == (False, None)
    assert nc.is_news_blackout_active(now, None) == (False, None)


def test_fetch_returns_empty_without_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("NEWS_CALENDAR_API_KEY", raising=False)
    monkeypatch.setattr(nc, "CACHE_PATH", str(tmp_path / "news_calendar.json"))

    def _boom(*a, **k):
        raise AssertionError("should never hit the network without an API key")
    monkeypatch.setattr(nc.requests, "get", _boom)

    events = nc.fetch_high_impact_events(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc))
    assert events == []


def test_fetch_fails_open_on_request_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_CALENDAR_API_KEY", "test-key")
    monkeypatch.setattr(nc, "CACHE_PATH", str(tmp_path / "news_calendar.json"))

    def _boom(*a, **k):
        raise nc.requests.RequestException("network down")
    monkeypatch.setattr(nc.requests, "get", _boom)

    events = nc.fetch_high_impact_events(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc))
    assert events == []  # never raises


def test_fetch_parses_and_caches_successful_response(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_CALENDAR_API_KEY", "test-key")
    cache_path = str(tmp_path / "news_calendar.json")
    monkeypatch.setattr(nc, "CACHE_PATH", cache_path)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"economicCalendar": [
                {"event": "NFP", "time": "2026-07-01T12:30:00", "impact": "High", "country": "USD"},
            ]}

    calls = []
    monkeypatch.setattr(nc.requests, "get", lambda *a, **k: calls.append(1) or FakeResponse())

    events = nc.fetch_high_impact_events(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc))
    assert events == [{"event": "NFP", "time": "2026-07-01T12:30:00", "impact": "High", "country": "USD"}]
    assert len(calls) == 1

    # second call within the TTL should hit the cache, not the network again
    nc.fetch_high_impact_events(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc))
    assert len(calls) == 1
