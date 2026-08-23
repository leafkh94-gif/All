"""Fetch as much M15 XAUUSD history as Capital.com will hand out, then run
the Golden Trio + SMC backtest against it. Designed for CI (has Capital
creds and full network) since the local sandbox blocks external hosts.

Capital.com's /prices/{epic} endpoint accepts a `max` parameter but
individual requests return at most ~1000 M15 bars (~10 days). To cover
longer windows we walk backward in ~10-day chunks using the earliest
timestamp of the previous chunk as the new `to` boundary, dedupe on
snapshotTime, and write the concatenated CSV.

Usage (env vars required for the API):
    CAPITAL_API_KEY=... CAPITAL_EMAIL=... CAPITAL_PASSWORD=... \\
      python tools/fetch_and_backtest.py --weeks 26
"""
import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy.capital_feed import CapitalFeed

CHUNK_MAX = 1000        # per-request bar ceiling; API-side max
BARS_PER_DAY = 96       # M15 bars in 24h


def _fmt_ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def fetch_history(feed, epic, weeks):
    """Walk backwards in ~10-day chunks and stitch into one chronological
    list. Dedupe on `snapshotTime`. Returns a list of candle dicts sorted
    ascending by t."""
    total_bars_target = weeks * 5 * BARS_PER_DAY
    print(f"targeting ~{total_bars_target} bars (~{weeks} weeks)")

    end = datetime.now(timezone.utc)
    all_rows = {}
    for chunk_idx in range((total_bars_target // CHUNK_MAX) + 2):
        params = {"resolution": "MINUTE_15", "max": CHUNK_MAX, "to": _fmt_ts(end)}
        try:
            resp = feed._session.get(
                f"{feed.base_url}/prices/{epic}", params=params, timeout=15,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"chunk {chunk_idx}: fetch failed ({e}); stopping early")
            break
        data = resp.json().get("prices") or resp.json().get("priceList") or []
        if not data:
            print(f"chunk {chunk_idx}: empty response; stopping")
            break
        for c in data:
            t = c.get("snapshotTimeUTC") or c.get("snapshotTime")
            if not t or t in all_rows:
                continue
            try:
                op = c["openPrice"]; cp = c["closePrice"]; hp = c.get("highPrice", cp); lp = c.get("lowPrice", cp)
                all_rows[t] = {
                    "t": t,
                    "o": float(op["bid"]),
                    "h": float(hp["bid"]),
                    "l": float(lp["bid"]),
                    "c": float(cp["bid"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
        got = len(data)
        earliest = min(c.get("snapshotTimeUTC") or c.get("snapshotTime") for c in data)
        print(f"chunk {chunk_idx}: +{got} bars, total unique {len(all_rows)}, earliest {earliest}")
        # Next chunk stops just before the earliest bar we just got
        end = datetime.fromisoformat(earliest.replace("Z", "+00:00")) - timedelta(seconds=1)
        if len(all_rows) >= total_bars_target:
            break
        time.sleep(0.5)   # be polite

    rows = sorted(all_rows.values(), key=lambda r: r["t"])
    return rows


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "o", "h", "l", "c"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} bars to {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=26,
                    help="how many weeks of M15 history to attempt")
    ap.add_argument("--out", default="gold_m15_capital.csv")
    ap.add_argument("--json", default="backtest_result.json")
    args = ap.parse_args()

    feed = CapitalFeed()
    feed.open_session()
    feed.resolve_epics()
    epic = feed._epic_cache.get("XAUUSD")
    if not epic:
        print("could not resolve XAUUSD epic; aborting")
        sys.exit(1)
    print(f"resolved XAUUSD -> {epic}")

    rows = fetch_history(feed, epic, args.weeks)
    if len(rows) < 500:
        print("not enough bars to backtest; aborting")
        sys.exit(1)
    write_csv(rows, args.out)

    print("\n=== running backtest ===")
    subprocess.check_call([
        sys.executable, "backtest.py",
        "--candles", args.out,
        "--json", args.json,
    ])


if __name__ == "__main__":
    main()
