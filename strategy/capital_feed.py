"""Capital.com API client for real-time gold (XAUUSD) market data.

PERF: Uses persistent requests.Session for connection pooling and reuse.
Caches epic resolution and candle data to minimize API calls.
"""
import json
import os
from datetime import datetime, timedelta, timezone
import time

import requests

import strategy_config as cfg


class CapitalFeed:
    """Real-time market data from Capital.com API.
    
    PERF: Maintains a session for connection pooling and caches candles
    to avoid redundant API calls within the same scan cycle.
    """

    def __init__(self):
        self.base_url = os.environ.get(
            "CAPITAL_BASE_URL",
            "https://demo-api-capital.backend-capital.com/api/v1",
        )
        self.api_key = os.environ["CAPITAL_API_KEY"]
        self.email = os.environ["CAPITAL_EMAIL"]
        self.password = os.environ["CAPITAL_PASSWORD"]
        self.cst = None
        self.x_security_token = None
        # PERF: Use persistent session for connection pooling.
        self._session = requests.Session()
        self._session.headers.update({"X-CAP-API-KEY": self.api_key})
        # PERF: Cache epic resolution + candles within a scan cycle.
        self._epic_cache = {}
        self._candle_cache = {}  # key: (instrument, timeframe, n), value: (candles, timestamp)
        self._cache_ttl_seconds = 60  # Invalidate candles older than 1 minute.

    def open_session(self):
        """Authenticate and open a session with Capital.com."""
        try:
            resp = self._session.post(
                f"{self.base_url}/session",
                json={
                    "identifier": self.email,
                    "password": self.password,
                    "encryptionVersion": "V2",
                },
                timeout=10,
            )
            resp.raise_for_status()
            self.cst = resp.headers["CST"]
            self.x_security_token = resp.headers["X-SECURITY-TOKEN"]
            self._session.headers.update({"CST": self.cst, "X-SECURITY-TOKEN": self.x_security_token})
            print(f"[capital_feed] session opened")
        except requests.RequestException as e:
            print(f"[capital_feed] session open failed: {e}")
            raise

    def resolve_epics(self):
        """Resolve instrument symbols to their Capital.com epic IDs.
        
        PERF: Only resolves once per session; results cached in _epic_cache.
        """
        for instrument, meta in cfg.INSTRUMENTS.items():
            if instrument in self._epic_cache:
                continue  # Already resolved
            try:
                search_term = meta.get("search", instrument)
                resp = self._session.get(
                    f"{self.base_url}/markets",
                    params={"searchTerm": search_term},
                    timeout=10,
                )
                resp.raise_for_status()
                results = resp.json().get("instrumentType", [])
                for market in results:
                    if market["instrumentType"] == "COMMODITIES":
                        self._epic_cache[instrument] = market["epic"]
                        print(f"[capital_feed] resolved {instrument} -> {market['epic']}")
                        break
                if instrument not in self._epic_cache:
                    print(f"[capital_feed] warning: could not resolve {instrument}")
            except requests.RequestException as e:
                print(f"[capital_feed] epic resolution failed for {instrument}: {e}")

    def get_current_price(self, instrument):
        """Get the latest bid/ask for the instrument."""
        epic = self._epic_cache.get(instrument)
        if not epic:
            return None
        try:
            resp = self._session.get(
                f"{self.base_url}/markets/{epic}",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            mid = (data["bid"] + data["ask"]) / 2
            return float(mid)
        except (requests.RequestException, KeyError, ValueError):
            return None

    def get_candles(self, instrument, timeframe, n=160):
        """Fetch the last n candles for the instrument at the given timeframe.
        
        PERF: Candles are cached within a scan cycle (TTL 60s) to avoid
        redundant API calls when the same data is requested multiple times.
        """
        epic = self._epic_cache.get(instrument)
        if not epic:
            return None
        
        cache_key = (instrument, timeframe, n)
        now = time.time()
        
        # Check cache validity
        if cache_key in self._candle_cache:
            candles, ts = self._candle_cache[cache_key]
            if now - ts < self._cache_ttl_seconds:
                return candles  # Cache hit
        
        # Cache miss or expired; fetch from API
        try:
            # Map timeframe to Capital.com resolution code
            resolution_map = {"15min": "15", "1h": "60", "4h": "240", "daily": "D"}
            resolution = resolution_map.get(timeframe, "15")
            
            resp = self._session.get(
                f"{self.base_url}/prices/{epic}",
                params={"resolution": resolution, "max": n},
                timeout=10,
            )
            resp.raise_for_status()
            
            candles_data = resp.json().get("priceList", [])
            candles = [
                {
                    "t": c["snapshotTime"],
                    "o": float(c["openPrice"]["bid"]),
                    "h": float(c["closePrice"]["bid"]),  # Capital.com API
                    "l": float(c["closePrice"]["bid"]),
                    "c": float(c["closePrice"]["bid"]),
                    "v": None,
                    "spread": abs(float(c["closePrice"]["bid"]) - float(c["closePrice"]["ask"])),
                }
                for c in candles_data
            ]
            
            # Cache the result
            self._candle_cache[cache_key] = (candles, now)
            return candles
        except requests.RequestException as e:
            print(f"[capital_feed] candle fetch failed for {instrument}/{timeframe}: {e}")
            return None
    
    def clear_candle_cache(self):
        """Clear the candle cache (call at end of scan cycle to refresh for next)."""
        self._candle_cache.clear()
