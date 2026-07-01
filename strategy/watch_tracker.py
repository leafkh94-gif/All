"""
WATCH lifecycle module (Section 3). Tracks 62-74 score setups, re-evaluates them
every 15 minutes before the main scan runs, and upgrades/cancels/updates them.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import strategy_config as cfg

STATE_DIR = "state"
WATCH_STATE_PATH = os.path.join(STATE_DIR, "watches.json")

ISO = "%Y-%m-%dT%H:%M:%S%z"


def _parse(ts):
    return datetime.fromisoformat(ts)


def _iso(dt):
    return dt.isoformat()


def _trend_arrow(old, new):
    if new > old:
        return "↗"  # ↗
    if new < old:
        return "↘"  # ↘
    return "→"      # →


class WatchTracker:
    """rescorer(instrument, direction, now_utc) -> scored dict (see
    scoring_strategy.score_candidate) or None if the pattern no longer scores.
    notifier(text) -> sends a Telegram message.
    aplus_formatter(scored) -> full A+ alert body (Section 7 format)."""

    def __init__(self, rescorer, notifier, aplus_formatter, on_upgrade=None, path=WATCH_STATE_PATH):
        self.rescorer = rescorer
        self.notifier = notifier
        self.aplus_formatter = aplus_formatter
        self.on_upgrade = on_upgrade
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._data = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def has_active(self, instrument):
        return instrument in self._data

    def add(self, scored, now_utc):
        instrument = scored["instrument"]
        self._data[instrument] = {
            "instrument": instrument,
            "direction": scored["direction"],
            "score": scored["score"],
            "entry_price": scored["entry_price"],
            "stop_loss": scored["stop_loss"],
            "tp1": scored["tp1"],
            "tp2": scored["tp2"],
            "issued_at": _iso(now_utc),
            "expires_at": _iso(now_utc + timedelta(hours=cfg.WATCH_EXPIRY_HOURS)),
            "last_update_sent": _iso(now_utc),
        }
        self._save()

    def active_instruments(self):
        return list(self._data.keys())

    def evaluate_all(self, now_utc):
        """Run the Section 3.2 decision tree for every active WATCH."""
        for instrument, w in list(self._data.items()):
            expires_at = _parse(w["expires_at"])
            if now_utc > expires_at:
                del self._data[instrument]  # Step 1 — silent expiry, no message
                self._save()
                continue

            scored = self.rescorer(w["direction"], instrument, now_utc)
            new_score = scored["score"] if scored else 0

            if new_score >= cfg.WATCH_UPGRADE_SCORE:
                self.notifier(
                    f"\U0001F7E2 WATCH → A+ — {instrument}\n"
                    f"Setup confirmed. Full signal now active.\n\n"
                    f"{self.aplus_formatter(scored)}"
                )
                if self.on_upgrade:
                    self.on_upgrade(scored, now_utc)
                del self._data[instrument]
                self._save()
                continue

            if new_score < cfg.WATCH_COLLAPSE_SCORE:
                self.notifier(
                    f"✖️ {instrument} watch closed.\n"
                    f"Setup did not complete. No action needed."
                )
                del self._data[instrument]
                self._save()
                continue

            # Score still 55-74 — continue monitoring, send update every 45 min
            last_update = _parse(w["last_update_sent"])
            if now_utc - last_update > timedelta(minutes=cfg.WATCH_UPDATE_INTERVAL_MINUTES):
                arrow = _trend_arrow(w["score"], new_score)
                remaining = expires_at - now_utc
                hrs, rem = divmod(int(remaining.total_seconds()), 3600)
                mins = rem // 60
                self.notifier(
                    f"⏱ WATCH Update — {instrument}\n"
                    f"Setup still active.\n"
                    f"Score: {w['score']} → {new_score} {arrow}\n"
                    f"Entry zone: {w['entry_price']}\n"
                    f"Time remaining: {hrs}h {mins}m\n"
                    f"⏳ Monitoring..."
                )
                w["last_update_sent"] = _iso(now_utc)
            w["score"] = new_score
            self._data[instrument] = w
            self._save()
