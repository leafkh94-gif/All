import datetime as dt

from strategy import economic_calendar as ec

XML_TEMPLATE = """<?xml version="1.0"?>
<weeklyevents>
{items}
</weeklyevents>"""

ITEM_TEMPLATE = ("<event><title>{title}</title><country>{country}</country>"
                  "<date>{date}</date><time>{time}</time><impact>{impact}</impact></event>")


def _event(now, offset_minutes, title="Non-Farm Payrolls", country="USD", impact="High"):
    t = now + dt.timedelta(minutes=offset_minutes)
    return {"title": title, "country": country, "impact": impact, "time": t.isoformat()}


def test_parse_event_datetime_handles_am_pm():
    result = ec._parse_event_datetime("07-04-2026", "8:30am")
    assert result is not None
    assert result.tzinfo is not None


def test_parse_event_datetime_returns_none_for_non_clock_time():
    assert ec._parse_event_datetime("07-04-2026", "All Day") is None
    assert ec._parse_event_datetime("07-04-2026", "Tentative") is None
    assert ec._parse_event_datetime(None, "8:30am") is None
    assert ec._parse_event_datetime("07-04-2026", None) is None


def test_parse_event_datetime_never_raises_on_malformed_date():
    assert ec._parse_event_datetime("not-a-date", "8:30am") is None


def test_blackout_active_for_high_impact_relevant_event_within_window():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [_event(now, offset_minutes=-10)]
    active, title = ec.is_economic_blackout_active(now, events, before_minutes=15, after_minutes=15)
    assert active is True
    assert title == "Non-Farm Payrolls"


def test_blackout_active_before_the_event_too():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [_event(now, offset_minutes=10)]  # event is 10 min in the future
    active, _ = ec.is_economic_blackout_active(now, events, before_minutes=15, after_minutes=15)
    assert active is True


def test_blackout_inactive_outside_window():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [_event(now, offset_minutes=-45)]
    active, _ = ec.is_economic_blackout_active(now, events, before_minutes=15, after_minutes=15)
    assert active is False


def test_blackout_ignores_low_impact_event():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [_event(now, offset_minutes=-5, impact="Low")]
    active, _ = ec.is_economic_blackout_active(now, events)
    assert active is False


def test_blackout_ignores_irrelevant_currency():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [_event(now, offset_minutes=-5, country="NZD")]
    active, _ = ec.is_economic_blackout_active(now, events)
    assert active is False


def test_blackout_respects_relevant_currencies_override():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [_event(now, offset_minutes=-5, country="GBP")]
    active, _ = ec.is_economic_blackout_active(now, events, relevant_currencies={"USD"})
    assert active is False
    active2, _ = ec.is_economic_blackout_active(now, events, relevant_currencies={"GBP"})
    assert active2 is True


def test_blackout_never_raises_on_malformed_event():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    events = [{"impact": "High"}, {"impact": "High", "country": "USD", "time": "not-a-date"}, {}, None]
    active, title = ec.is_economic_blackout_active(now, events)
    assert active is False
    assert title is None


def test_blackout_empty_events_is_inactive():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    assert ec.is_economic_blackout_active(now, []) == (False, None)
    assert ec.is_economic_blackout_active(now, None) == (False, None)


def test_fetch_fails_open_on_request_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(ec, "CACHE_PATH", str(tmp_path / "economic_calendar.json"))

    def _boom(*a, **k):
        raise ec.requests.RequestException("network down")
    monkeypatch.setattr(ec.requests, "get", _boom)

    events = ec.fetch_upcoming_events(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc))
    assert events == []


def test_fetch_fails_open_on_malformed_xml(monkeypatch, tmp_path):
    monkeypatch.setattr(ec, "CACHE_PATH", str(tmp_path / "economic_calendar.json"))

    class FakeResponse:
        content = b"not valid xml <<<"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(ec.requests, "get", lambda *a, **k: FakeResponse())
    events = ec.fetch_upcoming_events(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc))
    assert events == []


def test_fetch_parses_valid_feed_and_skips_non_clock_events(monkeypatch, tmp_path):
    monkeypatch.setattr(ec, "CACHE_PATH", str(tmp_path / "economic_calendar.json"))
    xml = XML_TEMPLATE.format(items="\n".join([
        ITEM_TEMPLATE.format(title="Non-Farm Payrolls", country="USD",
                             date="07-04-2026", time="8:30am", impact="High"),
        ITEM_TEMPLATE.format(title="Bank Holiday", country="USD",
                             date="07-04-2026", time="All Day", impact="Low"),
    ]))

    class FakeResponse:
        content = xml.encode()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(ec.requests, "get", lambda *a, **k: FakeResponse())
    events = ec.fetch_upcoming_events(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc))
    assert len(events) == 1
    assert events[0]["title"] == "Non-Farm Payrolls"
