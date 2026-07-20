"""
Capital.com data feed — session auth + candle fetch for the 4 tracked instruments.
Kept independent of scoring logic; only responsible for market data (Section 1.2).
"""
import json
import os
import time

import requests

import strategy_config as cfg

CAPITAL_BASE = os.environ.get(
    "CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com/api/v1")

RESOLUTION = {"15min": "MINUTE_15", "5min": "MINUTE_5", "1h": "HOUR", "4h": "HOUR_4", "daily": "DAY"}
CACHE_TTL = {"15min": 0, "5min": 0, "1h": 3600, "4h": 14400, "daily": 3600}

CACHE_DIR = ".cache"


class CapitalFeed:
    def __init__(self, api_key=None, email=None, password=None, cache_dir=CACHE_DIR):
        self.api_key = api_key or os.environ["CAPITAL_API_KEY"]
        self.email = email or os.environ["CAPITAL_EMAIL"]
        self.password = password or os.environ["CAPITAL_PASSWORD"]
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._cst = None
        self._token = None
        self._epics = {}

    def open_session(self):
        r = requests.post(
            f"{CAPITAL_BASE}/session",
            headers={"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"},
            json={"identifier": self.email, "password": self.password}, timeout=20)
        r.raise_for_status()
        self._cst = r.headers["CST"]
        self._token = r.headers["X-SECURITY-TOKEN"]

    def _headers(self):
        return {"X-CAP-API-KEY": self.api_key, "CST": self._cst, "X-SECURITY-TOKEN": self._token}

    def find_epic(self, search_term):
        r = requests.get(f"{CAPITAL_BASE}/markets", headers=self._headers(),
                          params={"searchTerm": search_term}, timeout=20)
        r.raise_for_status()
        markets = r.json().get("markets", [])
        if not markets:
            raise ValueError(f"No markets found for {search_term!r}")
        return markets[0]["epic"]

    def resolve_epics(self):
        for key, meta in cfg.INSTRUMENTS.items():
            self._epics[key] = self.find_epic(meta["search"])
        return dict(self._epics)

    def _cache_path(self, instrument, interval, n):
        # n is part of the key: two callers requesting the same
        # instrument+interval but a different candle count (e.g. a mode whose
        # entry_timeframe matches the fixed "1h"/"4h" context fetch) must not
        # silently share -- and truncate -- each other's cached candles.
        return os.path.join(self.cache_dir, f"{instrument}_{interval}_{n}.json")

    def get_candles(self, instrument, interval, n=60):
        ttl = CACHE_TTL.get(interval, 0)
        p = self._cache_path(instrument, interval, n)
        if ttl and os.path.exists(p) and time.time() - os.path.getmtime(p) < ttl:
            with open(p) as f:
                return json.load(f)

        epic = self._epics.get(instrument)
        if not epic:
            epic = self.find_epic(cfg.INSTRUMENTS[instrument]["search"])
            self._epics[instrument] = epic

        res = RESOLUTION[interval]
        r = requests.get(f"{CAPITAL_BASE}/prices/{epic}", headers=self._headers(),
                          params={"resolution": res, "max": n}, timeout=20)
        if r.status_code == 401:  # session expired — re-auth once
            self.open_session()
            r = requests.get(f"{CAPITAL_BASE}/prices/{epic}", headers=self._headers(),
                              params={"resolution": res, "max": n}, timeout=20)
        r.raise_for_status()
        data = r.json().get("prices", [])
        candles = [{
            # Capital.com's price objects can carry both "snapshotTime"
            # (broker/exchange-local, not guaranteed UTC) and
            # "snapshotTimeUTC" (explicit UTC) -- prefer the explicit one
            # when present. Index instruments have shown a consistent
            # multi-hour "candle from the future" anomaly in bars_report
            # that forex/crypto never do, which is exactly what a
            # non-UTC snapshotTime naively parsed as UTC would produce.
            "t": c.get("snapshotTimeUTC") or c["snapshotTime"],
            "o": float(c["openPrice"]["bid"]),
            "h": float(c["highPrice"]["bid"]),
            "l": float(c["lowPrice"]["bid"]),
            "c": float(c["closePrice"]["bid"]),
            "v": float(c.get("lastTradedVolume") or 0) or None,
        } for c in data]
        if candles:
            with open(p, "w") as f:
                json.dump(candles, f)
        return candles

    def get_current_price(self, instrument):
        candles = self.get_candles(instrument, "15min", n=1)
        return candles[-1]["c"] if candles else None
