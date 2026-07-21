"""
strategy/whale_tracker.py
--------------------------
BTCUSD-only whale/exchange-flow confirmation signal, sourced from
Blockstream's Esplora block explorer API (https://blockstream.info/api) --
a genuinely free, no-signup, open-source Bitcoin block explorer API. This
replaces an earlier design built around Whale Alert, which turned out to
need a paid subscription rather than the free tier originally assumed --
confirmed by the user's own account, not guessed.

Esplora's /address/{address}/txs endpoint returns one specific address's
recent confirmed transaction history -- there is no market-wide "all whale
transactions" feed here either, so exactly as before, this tracks net
inflow/outflow to a small, user-supplied list of BTC addresses
(WHALE_MONITORED_ADDRESSES): a deposit into a monitored address is
distribution/selling pressure (bearish); a withdrawal out of one is
accumulation (bullish).

No exchange address list is hardcoded here, for the same reason as before:
cold/hot wallets rotate over time without notice, and shipping a stale or
wrong guess would silently produce a misleading trading signal. Populate
WHALE_MONITORED_ADDRESSES yourself (comma-separated BTC addresses you
trust) -- with none configured, this feature is a pure no-op.

Amounts are tracked in native BTC (satoshis / 1e8), not USD -- Esplora
returns raw on-chain values with no fiat conversion built in, and pulling
in a separate price feed just for this one signal would be an extra moving
part for little benefit. WHALE_FLOW_SIGNIFICANT_BTC is the significance
threshold.

No API key needed at all. Fails open on any error -- network failure, rate
limit, unexpected response shape -- always returns [] rather than ever
blocking a scan, same convention as strategy/news_calendar.py.
"""
import json
import os
import time

import requests

import strategy_config as cfg

ESPLORA_BASE_URL = os.environ.get("ESPLORA_BASE_URL", "https://blockstream.info/api")
WHALE_MONITORED_ADDRESSES = [
    a.strip() for a in os.environ.get("WHALE_MONITORED_ADDRESSES", "").split(",") if a.strip()
]

CACHE_DIR = ".cache"
CACHE_PATH = os.path.join(CACHE_DIR, "whale_flow.json")
CACHE_TTL_SECONDS = 5 * 60

SATS_PER_BTC = 100_000_000


def _read_cache():
    try:
        if time.time() - os.path.getmtime(CACHE_PATH) < CACHE_TTL_SECONDS:
            with open(CACHE_PATH) as f:
                return json.load(f)
    except (FileNotFoundError, OSError, ValueError):
        pass
    return None


def _write_cache(entries):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(entries, f)
    except OSError:
        pass


def _address_net_btc(tx, address):
    """Net BTC received by `address` in one transaction (positive = inbound
    deposit, negative = outbound withdrawal). Sums across every matching
    vin/vout entry, since a single tx can touch the same address more than
    once (e.g. change outputs). Never raises."""
    net_sats = 0
    try:
        for vout in tx.get("vout", []) or []:
            if vout.get("scriptpubkey_address") == address:
                net_sats += int(vout.get("value", 0))
        for vin in tx.get("vin", []) or []:
            prevout = vin.get("prevout") or {}
            if prevout.get("scriptpubkey_address") == address:
                net_sats -= int(prevout.get("value", 0))
    except (TypeError, ValueError, AttributeError):
        return 0.0
    return net_sats / SATS_PER_BTC


def fetch_recent_whale_transactions(now_utc, addresses=None, lookback_minutes=None):
    """Best-effort fetch of each monitored address's recent confirmed
    transactions, each reduced to its net BTC impact on that address.
    Never raises. Returns [] with no addresses configured -- a pure no-op
    until WHALE_MONITORED_ADDRESSES is populated."""
    addresses = WHALE_MONITORED_ADDRESSES if addresses is None else addresses
    if not addresses:
        return []
    lookback_minutes = cfg.WHALE_FLOW_LOOKBACK_MINUTES if lookback_minutes is None else lookback_minutes
    cutoff_ts = now_utc.timestamp() - lookback_minutes * 60
    try:
        cached = _read_cache()
        if cached is not None:
            return cached
        entries = []
        for address in addresses:
            try:
                r = requests.get(f"{ESPLORA_BASE_URL}/address/{address}/txs", timeout=10)
                r.raise_for_status()
                txs = r.json()
                if not isinstance(txs, list):
                    continue
                for tx in txs:
                    if not isinstance(tx, dict):
                        continue
                    block_time = (tx.get("status") or {}).get("block_time")
                    if block_time is None or block_time < cutoff_ts:
                        continue
                    net_btc = _address_net_btc(tx, address)
                    if net_btc != 0:
                        entries.append({"address": address, "net_btc": net_btc, "block_time": block_time})
            except Exception:
                continue  # one bad/rate-limited address must not block the others
        _write_cache(entries)
        return entries
    except Exception:
        return []


def compute_exchange_netflow(entries):
    """(netflow_btc, signal) summed across all monitored-address entries.
    Positive -> net deposits into monitored addresses (bearish); negative ->
    net withdrawals (bullish). Malformed entries are skipped, never raise.
    """
    netflow = 0.0
    for e in entries or []:
        try:
            netflow += float(e.get("net_btc", 0.0))
        except (TypeError, ValueError, AttributeError):
            continue
    if netflow > 0:
        signal = "bearish"
    elif netflow < 0:
        signal = "bullish"
    else:
        signal = "neutral"
    return netflow, signal


def whale_flow_bonus(direction, netflow_btc, threshold=None):
    """(points, tag) -- confirmation only, never a penalty for the
    opposite/neutral case (matches every other bonus in scoring_strategy.py).
    """
    threshold = cfg.WHALE_FLOW_SIGNIFICANT_BTC if threshold is None else threshold
    if direction == "BUY" and netflow_btc <= -threshold:
        return cfg.WHALE_FLOW_BONUS, "whale_accumulation"
    if direction == "SELL" and netflow_btc >= threshold:
        return cfg.WHALE_FLOW_BONUS, "whale_distribution"
    return 0, None
