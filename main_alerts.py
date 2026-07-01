"""
Trading Alert Bot — main loop (Section 9 implementation order).
Alert-only. Never executes trades. Scans every 15 minutes aligned to
:00/:15/:30/:45 UTC via GitHub Actions cron.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import requests

import market_sessions
import scoring_indicators as ind
import scoring_strategy as strat
import strategy_config as cfg
from strategy.capital_feed import CapitalFeed
from strategy.watch_tracker import WatchTracker

STATE_DIR = "state"
MAIN_STATE_PATH = os.path.join(STATE_DIR, "main_state.json")
ACTIVE_ENTRIES_PATH = os.path.join(STATE_DIR, "active_entries.json")


# ─────────────────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────────────────
def send_telegram(text):
    requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
        json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text}, timeout=20)


# ─────────────────────────────────────────────────────────────────────
# State persistence
# ─────────────────────────────────────────────────────────────────────
def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ─────────────────────────────────────────────────────────────────────
# Section 6 — Entry expiry timer for issued A+ alerts
# ─────────────────────────────────────────────────────────────────────
class ActiveEntryTracker:
    def __init__(self, path=ACTIVE_ENTRIES_PATH):
        self.path = path
        self._data = load_json(path)

    def add(self, scored, now_utc):
        self._data[scored["instrument"]] = {
            "direction": scored["direction"],
            "entry_price": scored["entry_price"],
            "alert_time": now_utc.isoformat(),
        }
        save_json(self.path, self._data)

    def evaluate_all(self, now_utc, feed):
        for instrument, e in list(self._data.items()):
            alert_time = datetime.fromisoformat(e["alert_time"])
            price = feed.get_current_price(instrument)
            if price is None:
                continue
            touched = (e["direction"] == "BUY" and price <= e["entry_price"]) or (
                e["direction"] == "SELL" and price >= e["entry_price"])
            if touched:
                del self._data[instrument]
                save_json(self.path, self._data)
                continue
            if now_utc - alert_time > timedelta(hours=cfg.ENTRY_EXPIRY_HOURS):
                send_telegram(
                    f"⌛ {instrument} entry expired.\n"
                    f"Price did not reach entry zone within 2 hours.\n"
                    f"Setup cancelled. No action needed."
                )
                del self._data[instrument]
                save_json(self.path, self._data)


# ─────────────────────────────────────────────────────────────────────
# Alert formatting (Section 3.3 initial WATCH send, Section 7 A+ format)
# ─────────────────────────────────────────────────────────────────────
def _level_description(scored):
    b = scored["breakdown"]
    if b.get("pdh_pdl"):
        return f"{b['pdh_pdl']} sweep"
    if b.get("weekly_sweep"):
        return f"{b['weekly_sweep']} sweep"
    if b.get("eqh_eql"):
        return "EQH/EQL liquidity zone"
    if b.get("fvg"):
        return "FVG retest zone"
    return "key level"


def format_watch_alert(scored, expires_at):
    return (
        f"⚡ WATCH — {scored['instrument']}\n"
        f"Potential setup forming.\n"
        f"Direction: {scored['direction']}\n"
        f"Entry zone: {scored['entry_price']}\n"
        f"Score: {scored['score']}/100\n"
        f"Expires: {expires_at.strftime('%H:%M')} UTC (4 hours)"
    )


def format_aplus_alert(scored, now_utc):
    expiry = now_utc + timedelta(hours=cfg.ENTRY_EXPIRY_HOURS)
    return (
        f"🟢 A+ SIGNAL — {scored['instrument']}\n\n"
        f"Direction:  {scored['direction']}\n"
        f"Entry:      {scored['entry_price']}  (limit at 50% retrace)\n"
        f"Stop Loss:  {scored['stop_loss']}\n"
        f"TP1:        {scored['tp1']}   ← close 50% of position here\n"
        f"TP2:        {scored['tp2']}   ← trail remaining 50% to breakeven, let run\n\n"
        f"R:R Ratio:  1:{scored['rr_ratio']:g}\n"
        f"Expires:    {expiry.strftime('%H:%M')} UTC  (2 hours)\n\n"
        f"📋 Reason: {scored['breakdown']['pattern']} at {_level_description(scored)}\n"
        f"   Score: {scored['score']}/100  |  Bias: {scored['htf_bias']}\n\n"
        f"After TP1 is hit → move stop loss to breakeven (entry price).\n"
        f"Let remaining 50% run to TP2.\n"
        f"If TP2 not hit before 18:30 UTC → close all remaining position."
    )


def format_health_check(main_state, watch_tracker, now_utc):
    return (
        f"✅ Bot running — {now_utc.strftime('%Y-%m-%d %H:%M')} UTC\n"
        f"Last scan: {main_state.get('last_scan_time', 'n/a')}\n"
        f"Active WATCHes: {len(watch_tracker.active_instruments())}\n"
        f"Today's A+ signals: {main_state.get('aplus_count', 0)}"
    )


# ─────────────────────────────────────────────────────────────────────
# Hard flat (Section 1.5 / 9.1) — no new entry alerts after 18:30 UTC, US indices
# ─────────────────────────────────────────────────────────────────────
def hard_flat_active(now_utc, instrument_class):
    if instrument_class != "US_INDEX":
        return False
    return (now_utc.hour, now_utc.minute) >= (cfg.HARD_FLAT_UTC_HOUR, cfg.HARD_FLAT_UTC_MINUTE)


# ─────────────────────────────────────────────────────────────────────
# Section 5.1 / 5.5 — daily PDH/PDL and weekly level snapshots
# ─────────────────────────────────────────────────────────────────────
def maybe_record_daily_levels(feed, level_store, now_utc):
    today_key = now_utc.strftime("%Y-%m-%d")
    if now_utc.hour != 0:
        return
    for instrument in cfg.INSTRUMENTS:
        existing = level_store.get_daily_levels(instrument)
        if existing and existing.get("day_key") == today_key:
            continue
        daily = feed.get_candles(instrument, "daily", n=3)
        if len(daily) < 2:
            continue
        prev_day = daily[-2]
        level_store.set_daily_levels(instrument, prev_day["h"], prev_day["l"], today_key)


def maybe_record_weekly_levels(feed, level_store, now_utc):
    if now_utc.weekday() != 4 or now_utc.hour != 21:
        return
    week_key = now_utc.strftime("%G-W%V")
    for instrument in cfg.INSTRUMENTS:
        existing = level_store.get_weekly_levels(instrument)
        if existing and existing.get("week_key") == week_key:
            continue
        daily = feed.get_candles(instrument, "daily", n=6)
        if len(daily) < 5:
            continue
        week_candles = daily[-5:]
        week_high = max(c["h"] for c in week_candles)
        week_low = min(c["l"] for c in week_candles)
        level_store.set_weekly_levels(instrument, week_high, week_low, week_key)


# ─────────────────────────────────────────────────────────────────────
# Market data bundle
# ─────────────────────────────────────────────────────────────────────
def build_market(feed, instrument):
    return {
        "entry": feed.get_candles(instrument, "15min", n=80),
        "h1": feed.get_candles(instrument, "1h", n=160),
        "h4": feed.get_candles(instrument, "4h", n=260),
    }


# ─────────────────────────────────────────────────────────────────────
# Section 5.6 — 3-candle confirmation for pending A+ setups
# ─────────────────────────────────────────────────────────────────────
def evaluate_pending_confirmations(pending_store, feed, level_store, now_utc, entry_tracker, main_state):
    for instrument, scored in list(pending_store.all().items()):
        market = build_market(feed, instrument)
        last_closed = market["entry"][-1]
        direction = scored["direction"]

        if not strat.confirmation_closed_in_direction(last_closed, direction):
            pending_store.remove(instrument)  # closed against direction -> cancel silently
            continue

        candidate = strat.find_candidate(market["entry"])
        rescored = None
        if candidate and candidate["direction"] == direction:
            cls = cfg.INSTRUMENTS[instrument]["class"]
            rescored = strat.score_candidate(
                instrument, cls, candidate, market, now_utc, level_store,
                confirmation_bonus=cfg.CONFIRMATION_CANDLE_BONUS)

        if rescored and rescored["score"] >= cfg.APLUS_MIN_SCORE:
            send_telegram(format_aplus_alert(rescored, now_utc))
            entry_tracker.add(rescored, now_utc)
            main_state["aplus_count"] = main_state.get("aplus_count", 0) + 1
        pending_store.remove(instrument)


# ─────────────────────────────────────────────────────────────────────
# Section 4 — Health check
# ─────────────────────────────────────────────────────────────────────
def maybe_send_health_check(main_state, watch_tracker, now_utc):
    last = main_state.get("last_health_check_time")
    if last and now_utc - datetime.fromisoformat(last) <= timedelta(hours=cfg.HEALTH_CHECK_INTERVAL_HOURS):
        return
    send_telegram(format_health_check(main_state, watch_tracker, now_utc))
    main_state["last_health_check_time"] = now_utc.isoformat()


# ─────────────────────────────────────────────────────────────────────
# Correlation dedup — US indices are highly correlated; BTC is exempt (Section 1.5)
# ─────────────────────────────────────────────────────────────────────
def dedup_us_index_candidates(candidates):
    by_direction = {}
    keep = []
    for instrument, scored in candidates:
        cls = cfg.INSTRUMENTS[instrument]["class"]
        if cls != "US_INDEX":
            keep.append((instrument, scored))
            continue
        key = scored["direction"]
        if key not in by_direction or scored["score"] > by_direction[key][1]["score"]:
            by_direction[key] = (instrument, scored)
    keep.extend(by_direction.values())
    return keep


# ─────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────
def daily_reset_if_needed(main_state, now_utc):
    today_key = now_utc.strftime("%Y-%m-%d")
    if main_state.get("aplus_count_date") != today_key:
        main_state["aplus_count_date"] = today_key
        main_state["aplus_count"] = 0


def run():
    now = datetime.now(timezone.utc)
    main_state = load_json(MAIN_STATE_PATH)
    daily_reset_if_needed(main_state, now)

    feed = CapitalFeed()
    feed.open_session()
    feed.resolve_epics()

    level_store = ind.LevelStore()
    pending_store = strat.PendingAPlusStore()
    entry_tracker = ActiveEntryTracker()

    maybe_record_daily_levels(feed, level_store, now)
    maybe_record_weekly_levels(feed, level_store, now)

    def rescorer(direction, instrument, now_utc):
        market = build_market(feed, instrument)
        candidate = strat.find_candidate(market["entry"])
        if not candidate or candidate["direction"] != direction:
            return None
        cls = cfg.INSTRUMENTS[instrument]["class"]
        return strat.score_candidate(instrument, cls, candidate, market, now_utc, level_store)

    def on_upgrade(scored, now_utc):
        entry_tracker.add(scored, now_utc)
        main_state["aplus_count"] = main_state.get("aplus_count", 0) + 1

    watch_tracker = WatchTracker(
        rescorer=rescorer, notifier=send_telegram,
        aplus_formatter=lambda scored: format_aplus_alert(scored, now),
        on_upgrade=on_upgrade)

    # START of every 15-min loop, per Section 3.4 — evaluate WATCHes before scanning.
    watch_tracker.evaluate_all(now)
    entry_tracker.evaluate_all(now, feed)
    evaluate_pending_confirmations(pending_store, feed, level_store, now, entry_tracker, main_state)
    maybe_send_health_check(main_state, watch_tracker, now)

    candidates = []
    diagnostics = {}
    for instrument, meta in cfg.INSTRUMENTS.items():
        market = build_market(feed, instrument)
        candidate = strat.find_candidate(market["entry"])
        if not candidate:
            diagnostics[instrument] = {"pattern": None, "direction": None, "score": None,
                                        "blocked": "no pattern detected"}
            continue
        scored = strat.score_candidate(instrument, meta["class"], candidate, market, now, level_store,
                                        diagnostic=True)
        diagnostics[instrument] = {"pattern": scored["pattern"], "direction": scored["direction"],
                                    "score": scored["score"], "blocked": scored["blocked"]}
        if scored["blocked"] is None:
            candidates.append((instrument, scored))

    candidates = dedup_us_index_candidates(candidates)

    for instrument, scored in candidates:
        cls = cfg.INSTRUMENTS[instrument]["class"]

        if scored["score"] >= cfg.APLUS_MIN_SCORE:
            if hard_flat_active(now, cls):
                continue  # no new entry alerts after 18:30 UTC, US indices
            if watch_tracker.has_active(instrument) or pending_store.get(instrument):
                continue
            # Section 5.6 — A+ waits for one candle's confirmation; WATCH stays instant.
            pending_store.add(instrument, scored)
            continue

        if scored["score"] >= cfg.WATCH_MIN_SCORE:
            if watch_tracker.has_active(instrument):
                continue  # Section 3.4 cooldown — one active WATCH per instrument
            expires_at = now + timedelta(hours=cfg.WATCH_EXPIRY_HOURS)
            send_telegram(format_watch_alert(scored, expires_at))
            watch_tracker.add(scored, now)

    main_state["last_scan_time"] = now.strftime("%Y-%m-%d %H:%M UTC")
    main_state["last_diagnostics"] = diagnostics
    save_json(MAIN_STATE_PATH, main_state)
    return diagnostics


if __name__ == "__main__":
    run()
