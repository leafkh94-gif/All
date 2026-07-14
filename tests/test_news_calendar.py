import datetime as dt

from strategy import news_calendar as nc

RSS_TEMPLATE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
{items}
</channel></rss>"""

ITEM_TEMPLATE = """<item><title>{title}</title><pubDate>{pub_date}</pubDate></item>"""


def _rfc822(dt_obj):
    return dt_obj.strftime("%a, %d %b %Y %H:%M:%S GMT")


def _headline(now, offset_minutes, title="Fed hints at rate cut"):
    t = now + dt.timedelta(minutes=offset_minutes)
    return {"title": title, "time": t.isoformat()}


def test_blackout_active_for_relevant_headline_within_window():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    headlines = [_headline(now, offset_minutes=-10, title="Fed signals rate cut ahead")]
    active, title = nc.is_news_blackout_active(now, headlines, after_minutes=30)
    assert active is True
    assert title == "Fed signals rate cut ahead"


def test_blackout_inactive_after_window_expires():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    headlines = [_headline(now, offset_minutes=-45, title="Fed signals rate cut ahead")]
    active, _ = nc.is_news_blackout_active(now, headlines, after_minutes=30)
    assert active is False


def test_blackout_ignores_irrelevant_headline():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    headlines = [_headline(now, offset_minutes=-5, title="Dubai opens new metro line")]
    active, _ = nc.is_news_blackout_active(now, headlines, after_minutes=30)
    assert active is False


def test_blackout_ignores_future_pub_time_clock_skew_guard():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    headlines = [_headline(now, offset_minutes=5, title="Fed rate decision")]  # "published" in the future
    active, _ = nc.is_news_blackout_active(now, headlines, after_minutes=30)
    assert active is False


def test_blackout_never_raises_on_malformed_headline():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    headlines = [{"title": "Fed decision"}, {"title": "Fed decision", "time": "not-a-date"}, {}]
    active, title = nc.is_news_blackout_active(now, headlines)
    assert active is False
    assert title is None


def test_blackout_empty_headlines_is_inactive():
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    assert nc.is_news_blackout_active(now, []) == (False, None)
    assert nc.is_news_blackout_active(now, None) == (False, None)


def test_fetch_fails_open_on_request_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(nc, "CACHE_PATH", str(tmp_path / "news_rss.json"))

    def _boom(*a, **k):
        raise nc.requests.RequestException("network down")
    monkeypatch.setattr(nc.requests, "get", _boom)

    headlines = nc.fetch_recent_headlines(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc))
    assert headlines == []  # never raises


def test_fetch_fails_open_on_malformed_xml(monkeypatch, tmp_path):
    monkeypatch.setattr(nc, "CACHE_PATH", str(tmp_path / "news_rss.json"))

    class FakeResponse:
        content = b"not valid xml <<<"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(nc.requests, "get", lambda *a, **k: FakeResponse())

    headlines = nc.fetch_recent_headlines(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc))
    assert headlines == []


def test_fetch_parses_and_caches_successful_response(monkeypatch, tmp_path):
    cache_path = str(tmp_path / "news_rss.json")
    monkeypatch.setattr(nc, "CACHE_PATH", cache_path)

    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    xml = RSS_TEMPLATE.format(items=ITEM_TEMPLATE.format(
        title="Fed holds rates steady", pub_date=_rfc822(now)))

    class FakeResponse:
        content = xml.encode()

        def raise_for_status(self):
            pass

    calls = []
    monkeypatch.setattr(nc.requests, "get", lambda *a, **k: calls.append(1) or FakeResponse())

    headlines = nc.fetch_recent_headlines(now)
    assert len(headlines) == 1
    assert headlines[0]["title"] == "Fed holds rates steady"
    assert len(calls) == 1

    # second call within the TTL should hit the cache, not the network again
    nc.fetch_recent_headlines(now)
    assert len(calls) == 1


def test_fetch_skips_items_missing_title_or_pubdate(monkeypatch, tmp_path):
    monkeypatch.setattr(nc, "CACHE_PATH", str(tmp_path / "news_rss.json"))
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    xml = RSS_TEMPLATE.format(items="<item><title>No date here</title></item>")

    class FakeResponse:
        content = xml.encode()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(nc.requests, "get", lambda *a, **k: FakeResponse())
    headlines = nc.fetch_recent_headlines(now)
    assert headlines == []
