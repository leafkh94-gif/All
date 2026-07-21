"""
strategy/whale_tracker.py
--------------------------
BTCUSD-only whale-flow confirmation signal, sourced from Whale Alert's
large-transaction feed (https://whale-alert.io/) -- the most literal, and
most accessible, source of real "whale tracking" data: individual
transactions above a fixed USD floor, tagged with the owner type (exchange,
unknown wallet, etc.) of both the sending and receiving address.

This one feed stands in for two related but distinct requests:
  - "exchange netflow": large deposits into exchanges signal intent to
    sell (bearish); large withdrawals signal accumulation (bullish).
  - "smart money / whale flow": Whale Alert only reports transactions large
    enough to already be whale-sized, so the same netflow calculation is
    inherently a whale-flow signal. A true wallet-labeled "smart money"
    cohort (historically profitable addresses specifically) is not
    available from any free/no-signup source -- this is a deliberate,
    disclosed simplification, not an oversight.

Requires a WHALE_ALERT_API_KEY (free-tier signup at whale-alert.io). Fails
open on any error -- missing key, network failure, rate limit, unexpected
response shape -- always returns [] rather than ever blocking a scan, same
convention as strategy/news_calendar.py and strategy/scan_diagnostics.py.
"""
import json
import os
import time

import requests

import strategy_config as cfg

WHALE_ALERT_API_KEY = os.environ.get("WHALE_ALERT_API_KEY")
WHALE_ALERT_BASE_URL = os.environ.get("WHALE_ALERT_BASE_URL", "https://api.whale-alert.io/v1/transactions")

CACHE_DIR = ".cache"
CACHE_PATH = os.path.join(CACHE_DIR, "whale_alert.json")
CACHE_TTL_SECONDS = 5 * 60  # large-tx feed moves faster than a news calendar; free tier allows frequent polling


def _read_cache():
    try:
        if time.time() - os.path.getmtime(CACHE_PATH) < CACHE_TTL_SECONDS:
            with open(CACHE_PATH) as f:
                return json.load(f)
    except (FileNotFoundError, OSError, ValueError):
        pass
    return None


def _write_cache(transactions):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(transactions, f)
    except OSError:
        pass


def fetch_recent_whale_transactions(now_utc, min_value_usd=None, lookback_minutes=None):
    """Best-effort fetch of recent large BTC transactions. Never raises.

    Returns [] (not an error) when WHALE_ALERT_API_KEY is unset -- this lets
    the feature ship and be wired in now, and switch on the moment a key is
    added as a GitHub secret, with zero code change.
    """
    if not WHALE_ALERT_API_KEY:
        return []
    min_value_usd = cfg.WHALE_MIN_TX_USD if min_value_usd is None else min_value_usd
    lookback_minutes = cfg.WHALE_FLOW_LOOKBACK_MINUTES if lookback_minutes is None else lookback_minutes
    try:
        cached = _read_cache()
        if cached is not None:
            return cached
        start_ts = int(now_utc.timestamp()) - lookback_minutes * 60
        r = requests.get(WHALE_ALERT_BASE_URL, timeout=10, params={
            "api_key": WHALE_ALERT_API_KEY,
            "currency": "btc",
            "min_value": int(min_value_usd),
            "start": start_ts,
        })
        r.raise_for_status()
        payload = r.json()
        transactions = payload.get("transactions", []) if isinstance(payload, dict) else []
        _write_cache(transactions)
        return transactions
    except Exception:
        return []


def compute_exchange_netflow(transactions):
    """(netflow_usd, signal) from a list of Whale Alert-shaped transactions.

    netflow = sum(amount_usd into exchanges) - sum(amount_usd out of exchanges).
    Positive -> deposits dominate (bearish); negative -> withdrawals dominate
    (bullish). Malformed entries are skipped, never raise.
    """
    netflow = 0.0
    for tx in transactions or []:
        try:
            amount_usd = float(tx.get("amount_usd", 0.0))
            from_type = (tx.get("from") or {}).get("owner_type")
            to_type = (tx.get("to") or {}).get("owner_type")
            if to_type == "exchange":
                netflow += amount_usd
            if from_type == "exchange":
                netflow -= amount_usd
        except (TypeError, ValueError, AttributeError):
            continue
    if netflow > 0:
        signal = "bearish"
    elif netflow < 0:
        signal = "bullish"
    else:
        signal = "neutral"
    return netflow, signal


def whale_flow_bonus(direction, netflow_usd, threshold=None):
    """(points, tag) -- confirmation only, never a penalty for the
    opposite/neutral case (matches every other bonus in scoring_strategy.py).
    """
    threshold = cfg.WHALE_FLOW_SIGNIFICANT_USD if threshold is None else threshold
    if direction == "BUY" and netflow_usd <= -threshold:
        return cfg.WHALE_FLOW_BONUS, "whale_accumulation"
    if direction == "SELL" and netflow_usd >= threshold:
        return cfg.WHALE_FLOW_BONUS, "whale_distribution"
    return 0, None
