"""
strategy/whale_tracker.py
--------------------------
BTCUSD-only whale-flow confirmation signal, sourced from Whale Alert's
per-address transaction endpoint -- confirmed by the user's own account as
GET {base}/bitcoin/address/{address}/transactions?api_key=... . This is
different from an earlier draft of this module, which assumed a global
"all whale transactions above $X" feed; that assumption could not be
verified (no live network access from this sandbox) and turned out not to
match what this account actually has. This version only relies on the
confirmed per-address endpoint.

Because this endpoint returns one address's history at a time rather than
a market-wide feed, this module tracks a small, user-supplied list of BTC
addresses (WHALE_MONITORED_ADDRESSES) and computes net inflow/outflow
*to those specific addresses* as a netflow proxy: a deposit into a
monitored address = distribution/selling pressure (bearish); a withdrawal
out of one = accumulation (bullish). This stands in for both "exchange
netflow" and "whale/smart-money tracking" -- the same combined-signal
approach as before, just computed bottom-up from known addresses instead
of top-down from a global feed.

DISCLOSED LIMITATIONS (not hidden):
  - No address list is hardcoded here. I have no way to verify from this
    sandbox which BTC addresses currently belong to which exchange --
    cold/hot wallets rotate over time without notice, and a stale or wrong
    guess would silently produce a misleading trading signal, which is
    worse than no signal at all. WHALE_MONITORED_ADDRESSES must be
    supplied by you (comma-separated BTC addresses you trust, e.g. via a
    repo secret). With none configured, this feature is a pure no-op --
    same as with no API key.
  - The exact response JSON shape from this endpoint was not confirmed
    live either. Parsing below defensively handles the shape used across
    Whale Alert's public API generally (a list of transactions, each with
    "from"/"to" as either a plain address string or a {"address": ...}
    object, and "amount_usd"), tolerating either directly-returned lists
    or a {"transactions": [...]} wrapper, and skips anything that doesn't
    match rather than raising. If the real shape differs further, this
    fails open (contributes zero, never crashes a scan) rather than
    breaking -- but the netflow signal will simply stay neutral until the
    parsing is corrected against a real captured response.
"""
import json
import os
import time

import requests

import strategy_config as cfg

WHALE_ALERT_API_KEY = os.environ.get("WHALE_ALERT_API_KEY")
WHALE_ALERT_BASE_URL = os.environ.get("WHALE_ALERT_BASE_URL", "https://leviathan.whale-alert.io")
WHALE_MONITORED_ADDRESSES = [
    a.strip() for a in os.environ.get("WHALE_MONITORED_ADDRESSES", "").split(",") if a.strip()
]

CACHE_DIR = ".cache"
CACHE_PATH = os.path.join(CACHE_DIR, "whale_alert.json")
CACHE_TTL_SECONDS = 5 * 60


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


def _extract_address(side):
    """side is whatever the API put under "from"/"to" -- a plain address
    string or a dict with an "address" key. Never raises."""
    if isinstance(side, str):
        return side
    if isinstance(side, dict):
        return side.get("address")
    return None


def fetch_recent_whale_transactions(now_utc, addresses=None):
    """Best-effort fetch of recent transactions for each monitored address.
    Never raises. Returns [] with no API key or no addresses configured --
    a pure no-op until both are set."""
    addresses = WHALE_MONITORED_ADDRESSES if addresses is None else addresses
    if not WHALE_ALERT_API_KEY or not addresses:
        return []
    try:
        cached = _read_cache()
        if cached is not None:
            return cached
        transactions = []
        for address in addresses:
            try:
                r = requests.get(
                    f"{WHALE_ALERT_BASE_URL}/bitcoin/address/{address}/transactions",
                    timeout=10, params={"api_key": WHALE_ALERT_API_KEY})
                r.raise_for_status()
                payload = r.json()
                txs = payload.get("transactions", payload) if isinstance(payload, dict) else payload
                if isinstance(txs, list):
                    for tx in txs:
                        if isinstance(tx, dict):
                            tagged = dict(tx)
                            tagged["_monitored_address"] = address
                            transactions.append(tagged)
            except Exception:
                continue  # one bad/rate-limited address must not block the others
        _write_cache(transactions)
        return transactions
    except Exception:
        return []


def compute_exchange_netflow(transactions):
    """(netflow_usd, signal) -- netflow relative to the monitored addresses:
    an inbound transfer *to* a monitored address is a deposit (bearish); an
    outbound transfer *from* one is a withdrawal (bullish). Malformed
    entries are skipped, never raise.
    """
    netflow = 0.0
    for tx in transactions or []:
        try:
            amount_usd = float(tx.get("amount_usd", 0.0))
            monitored = tx.get("_monitored_address")
            if not monitored:
                continue
            to_addr = _extract_address(tx.get("to"))
            from_addr = _extract_address(tx.get("from"))
            if to_addr == monitored:
                netflow += amount_usd
            elif from_addr == monitored:
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
