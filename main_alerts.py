"""
Trading Alert Bot — main loop (Section 9 implementation order).
Alert-only. Never executes trades. Scans every 15 minutes aligned to
:00/:15/:30/:45 UTC via GitHub Actions cron.
"""
import json
import os
import traceback
from datetime import datetime, timedelta, timezone

import requests

import market_sessions
import scoring_indicators as ind
import scoring_strategy as strat
import strategy_config as cfg
from strategy import modes
from strategy import news_calendar
from strategy import scan_diagnostics
from strategy.capital_feed import CapitalFeed
from strategy.watch_tracker import WatchTracker

STATE_DIR = "state"
MAIN_STATE_PATH = os.path.join(STATE_DIR, "main_state.json")
ACTIVE_ENTRIES_PATH = os.path.join(STATE_DIR, "active_entries.json")
OPEN_TRADES_PATH = os.path.join(STATE_DIR, "open_trades.json")
TRADE_LOG_PATH = os.path.join(STATE_DIR, "trade_log.json")
TRADE_LOG_MAX_ENTRIES = 500
MODE_STATE_PATH = os.path.join(STATE_DIR, "mode.json")


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
# Trading mode (standard / loose / fast) — user-selectable via /mode
# ─────────────────────────────────────────────────────────────────────
def load_active_mode(path=None):
    name = load_json(path or MODE_STATE_PATH).get("mode", modes.DEFAULT_MODE)
    return modes.MODES.get(name, modes.STANDARD)


def save_active_mode_name(name, path=None):
    save_json(path or MODE_STATE_PATH, {"mode": name})


def _format_duration(minutes):
    minutes = int(round(minutes))
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    if minutes < 60:
        return f"{minutes} minutes"
    hrs, mins = divmod(minutes, 60)
    return f"{hrs}h {mins}m"


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
            "stop_loss": scored.get("stop_loss"),
            "tp1": scored.get("tp1"),
            "tp2": scored.get("tp2"),
            "pattern": scored.get("pattern"),
            "alert_time": now_utc.isoformat(),
        }
        save_json(self.path, self._data)

    def evaluate_all(self, now_utc, feed, mode=None, open_tracker=None):
        m = mode or modes.STANDARD
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
                if open_tracker is not None and e.get("stop_loss") is not None:
                    open_tracker.add({**e, "instrument": instrument}, now_utc)
                continue
            if now_utc - alert_time > timedelta(minutes=m.entry_expiry_minutes):
                send_telegram(
                    f"⌛ {instrument} entry expired.\n"
                    f"Price did not reach entry zone within {_format_duration(m.entry_expiry_minutes)}.\n"
                    f"Setup cancelled. No action needed."
                )
                del self._data[instrument]
                save_json(self.path, self._data)


# ─────────────────────────────────────────────────────────────────────
# Section 7 — Live TP/stop tracking for filled entries.
# Alert-only: tells you what to do (close 50%, move stop, etc.), never
# touches the broker itself.
# ─────────────────────────────────────────────────────────────────────
def _r_multiple(direction, entry_price, initial_risk, exit_price):
    """R-multiple of exit_price relative to entry, sized by the trade's original
    risk distance (captured before any breakeven-stop adjustment)."""
    if not initial_risk:
        return 0.0
    raw = (exit_price - entry_price) / initial_risk
    return raw if direction == "BUY" else -raw


def _append_trade_log(entry, path=None):
    path = path or TRADE_LOG_PATH
    log = load_json(path)
    entries = log.get("entries", [])
    entries.append(entry)
    log["entries"] = entries[-TRADE_LOG_MAX_ENTRIES:]
    save_json(path, log)


class OpenTradeTracker:
    def __init__(self, path=OPEN_TRADES_PATH, trade_log_path=None):
        self.path = path
        self.trade_log_path = trade_log_path or TRADE_LOG_PATH
        self._data = load_json(path)

    def add(self, scored, now_utc):
        entry_price = scored["entry_price"]
        stop_loss = scored["stop_loss"]
        self._data[scored["instrument"]] = {
            "direction": scored["direction"],
            "pattern": scored.get("pattern"),
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "initial_risk": abs(entry_price - stop_loss),
            "tp1": scored["tp1"],
            "tp2": scored["tp2"],
            "tp1_hit": False,
            "locked_r": 0.0,
            "opened_at": now_utc.isoformat(),
        }
        save_json(self.path, self._data)

    def _close(self, instrument, t, now_utc, outcome, r_multiple):
        del self._data[instrument]
        save_json(self.path, self._data)
        _append_trade_log({
            "instrument": instrument, "pattern": t.get("pattern"), "direction": t["direction"],
            "outcome": outcome, "r_multiple": round(r_multiple, 2), "closed_at": now_utc.isoformat(),
        }, path=self.trade_log_path)

    def evaluate_all(self, now_utc, feed):
        for instrument, t in list(self._data.items()):
            price = feed.get_current_price(instrument)
            if price is None:
                continue
            is_buy = t["direction"] == "BUY"
            entry_price, initial_risk = t["entry_price"], t["initial_risk"]

            if not t["tp1_hit"]:
                hit_tp1 = price >= t["tp1"] if is_buy else price <= t["tp1"]
                hit_stop = price <= t["stop_loss"] if is_buy else price >= t["stop_loss"]
                if hit_tp1:
                    t["tp1_hit"] = True
                    t["locked_r"] = 0.5 * _r_multiple(t["direction"], entry_price, initial_risk, t["tp1"])
                    t["stop_loss"] = entry_price
                    save_json(self.path, self._data)
                    send_telegram(
                        f"🎯 {instrument} TP1 hit @ {t['tp1']}.\n"
                        f"Close 50% of the position now.\n"
                        f"Stop loss moved to breakeven ({entry_price}) on the rest — let it run to TP2."
                    )
                    continue
                if hit_stop:
                    r = _r_multiple(t["direction"], entry_price, initial_risk, t["stop_loss"])
                    self._close(instrument, t, now_utc, "stop_before_tp1", r)
                    send_telegram(f"🛑 {instrument} stop loss hit @ {t['stop_loss']}. Full position closed.")
                    continue
            else:
                hit_tp2 = price >= t["tp2"] if is_buy else price <= t["tp2"]
                hit_be = price <= t["stop_loss"] if is_buy else price >= t["stop_loss"]
                if hit_tp2:
                    r = t["locked_r"] + 0.5 * _r_multiple(t["direction"], entry_price, initial_risk, t["tp2"])
                    self._close(instrument, t, now_utc, "tp2_after_tp1", r)
                    send_telegram(f"✅ {instrument} TP2 hit @ {t['tp2']}. Close the remaining position — trade complete.")
                    continue
                if hit_be:
                    r = t["locked_r"] + 0.5 * _r_multiple(t["direction"], entry_price, initial_risk, t["stop_loss"])
                    self._close(instrument, t, now_utc, "breakeven_after_tp1", r)
                    send_telegram(
                        f"⚖️ {instrument} breakeven stop hit after TP1. "
                        f"Remainder closed at entry — partial profit locked in."
                    )
                    continue

            cls = cfg.INSTRUMENTS.get(instrument, {}).get("class")
            if hard_flat_active(now_utc, cls):
                if t["tp1_hit"]:
                    r = t["locked_r"] + 0.5 * _r_multiple(t["direction"], entry_price, initial_risk, price)
                    outcome = "session_cutoff_after_tp1"
                else:
                    r = _r_multiple(t["direction"], entry_price, initial_risk, price)
                    outcome = "session_cutoff_before_tp1"
                self._close(instrument, t, now_utc, outcome, r)
                send_telegram(
                    f"⏰ {instrument} — TP2 not hit before "
                    f"{cfg.HARD_FLAT_UTC_HOUR:02d}:{cfg.HARD_FLAT_UTC_MINUTE:02d} UTC.\n"
                    f"Close all remaining position now, per the original A+ plan."
                )


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


def format_watch_alert(scored, expires_at, mode=None):
    m = mode or modes.STANDARD
    return (
        f"⚡ WATCH — {scored['instrument']}\n"
        f"Potential setup forming.\n"
        f"Direction: {scored['direction']}\n"
        f"Entry zone: {scored['entry_price']}\n"
        f"Score: {scored['score']}/100\n"
        f"Expires: {expires_at.strftime('%H:%M')} UTC ({_format_duration(m.watch_expiry_minutes)})"
    )


def format_aplus_alert(scored, now_utc, mode=None):
    m = mode or modes.STANDARD
    expiry = now_utc + timedelta(minutes=m.entry_expiry_minutes)
    return (
        f"🟢 A+ SIGNAL — {scored['instrument']}\n\n"
        f"Direction:  {scored['direction']}\n"
        f"Entry:      {scored['entry_price']}  (limit at 50% retrace)\n"
        f"Stop Loss:  {scored['stop_loss']}\n"
        f"TP1:        {scored['tp1']}   ← close 50% of position here\n"
        f"TP2:        {scored['tp2']}   ← trail remaining 50% to breakeven, let run\n\n"
        f"R:R Ratio:  1:{scored['rr_ratio']:g}\n"
        f"Expires:    {expiry.strftime('%H:%M')} UTC  ({_format_duration(m.entry_expiry_minutes)})\n\n"
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
def build_market(feed, instrument, mode=None):
    m = mode or modes.STANDARD
    return {
        "entry": feed.get_candles(instrument, m.entry_timeframe, n=80),
        "h1": feed.get_candles(instrument, "1h", n=160),
        "h4": feed.get_candles(instrument, "4h", n=260),
    }


# ─────────────────────────────────────────────────────────────────────
# Section 5.6 — 3-candle confirmation for pending A+ setups
# ─────────────────────────────────────────────────────────────────────
def evaluate_pending_confirmations(pending_store, feed, level_store, now_utc, entry_tracker, main_state,
                                    mode=None):
    m = mode or modes.STANDARD
    for instrument, scored in list(pending_store.all().items()):
        market = build_market(feed, instrument, mode=m)
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
                confirmation_bonus=cfg.CONFIRMATION_CANDLE_BONUS, mode=m)

        if rescored and rescored["score"] >= m.aplus_min_score:
            send_telegram(format_aplus_alert(rescored, now_utc, mode=m))
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
        main_state["daily_loss_total"] = 0.0


# ─────────────────────────────────────────────────────────────────────
# Daily loss circuit-breaker — self-reported, since this alert-only bot has
# no visibility into the user's real account balance/P&L. Runs as a
# DAILY_LOSS_BREAKER_DURATION_DAYS trial: the window starts the first time
# the bot ever sees it (not a hardcoded date, so it isn't thrown off by
# when this actually goes live), and enforcement quietly stops once it
# expires — /loss and /win keep logging, they just stop pausing alerts.
# ─────────────────────────────────────────────────────────────────────
def ensure_loss_breaker_window(main_state, now_utc):
    if "loss_breaker_active_until" not in main_state:
        main_state["loss_breaker_active_until"] = (
            now_utc + timedelta(days=cfg.DAILY_LOSS_BREAKER_DURATION_DAYS)
        ).isoformat()


def loss_breaker_window_active(main_state, now_utc):
    until = main_state.get("loss_breaker_active_until")
    return until is not None and now_utc < datetime.fromisoformat(until)


def record_loss(amount, now_utc=None, path=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    path = path or MAIN_STATE_PATH
    main_state = load_json(path)
    daily_reset_if_needed(main_state, now_utc)
    ensure_loss_breaker_window(main_state, now_utc)
    window_active = loss_breaker_window_active(main_state, now_utc)
    was_tripped = window_active and main_state.get("daily_loss_total", 0.0) >= cfg.DAILY_LOSS_LIMIT_USD
    main_state["daily_loss_total"] = main_state.get("daily_loss_total", 0.0) + amount
    save_json(path, main_state)
    now_tripped = window_active and main_state["daily_loss_total"] >= cfg.DAILY_LOSS_LIMIT_USD
    if now_tripped and not was_tripped:
        send_telegram(
            f"🛑 Daily loss limit (${cfg.DAILY_LOSS_LIMIT_USD:.2f}) reached "
            f"(logged: ${main_state['daily_loss_total']:.2f}).\n"
            f"No new WATCH/A+ alerts until the reset at UTC midnight."
        )
    return main_state["daily_loss_total"]


def record_win(amount, now_utc=None, path=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    path = path or MAIN_STATE_PATH
    main_state = load_json(path)
    daily_reset_if_needed(main_state, now_utc)
    ensure_loss_breaker_window(main_state, now_utc)
    main_state["daily_loss_total"] = main_state.get("daily_loss_total", 0.0) - amount
    save_json(path, main_state)
    return main_state["daily_loss_total"]


# ─────────────────────────────────────────────────────────────────────
# Manual blackout — user-declared "go quiet" window (e.g. ahead of known
# news), separate from the self-reported loss breaker above.
# ─────────────────────────────────────────────────────────────────────
def manual_blackout_active(main_state, now_utc):
    until = main_state.get("blackout_until")
    return until is not None and now_utc < datetime.fromisoformat(until)


def set_blackout(minutes, now_utc=None, path=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    path = path or MAIN_STATE_PATH
    main_state = load_json(path)
    main_state["blackout_until"] = (now_utc + timedelta(minutes=minutes)).isoformat()
    save_json(path, main_state)
    return main_state["blackout_until"]


def clear_blackout(path=None):
    path = path or MAIN_STATE_PATH
    main_state = load_json(path)
    main_state.pop("blackout_until", None)
    save_json(path, main_state)


def run():
    now = datetime.now(timezone.utc)
    main_state = load_json(MAIN_STATE_PATH)
    daily_reset_if_needed(main_state, now)
    ensure_loss_breaker_window(main_state, now)
    mode = load_active_mode()
    breaker_tripped = (loss_breaker_window_active(main_state, now)
                       and main_state.get("daily_loss_total", 0.0) >= cfg.DAILY_LOSS_LIMIT_USD)
    news_headlines = news_calendar.fetch_recent_headlines(now)
    news_blackout, news_event_name = news_calendar.is_news_blackout_active(now, news_headlines)
    main_state["news_blackout_event"] = news_event_name if news_blackout else None
    suppress_new_alerts = breaker_tripped or manual_blackout_active(main_state, now) or news_blackout

    feed = CapitalFeed()
    feed.open_session()
    feed.resolve_epics()

    level_store = ind.LevelStore()
    pending_store = strat.PendingAPlusStore()
    entry_tracker = ActiveEntryTracker()
    open_trade_tracker = OpenTradeTracker()

    maybe_record_daily_levels(feed, level_store, now)
    maybe_record_weekly_levels(feed, level_store, now)

    def rescorer(direction, instrument, now_utc):
        market = build_market(feed, instrument, mode=mode)
        candidate = strat.find_candidate(market["entry"])
        if not candidate or candidate["direction"] != direction:
            return None
        cls = cfg.INSTRUMENTS[instrument]["class"]
        return strat.score_candidate(instrument, cls, candidate, market, now_utc, level_store, mode=mode)

    def on_upgrade(scored, now_utc):
        entry_tracker.add(scored, now_utc)
        main_state["aplus_count"] = main_state.get("aplus_count", 0) + 1

    watch_tracker = WatchTracker(
        rescorer=rescorer, notifier=send_telegram,
        aplus_formatter=lambda scored: format_aplus_alert(scored, now, mode=mode),
        on_upgrade=on_upgrade, mode=mode)

    # START of every 15-min loop, per Section 3.4 — evaluate WATCHes before scanning.
    watch_tracker.evaluate_all(now)
    open_trade_tracker.evaluate_all(now, feed)
    entry_tracker.evaluate_all(now, feed, mode=mode, open_tracker=open_trade_tracker)
    evaluate_pending_confirmations(pending_store, feed, level_store, now, entry_tracker, main_state, mode=mode)
    maybe_send_health_check(main_state, watch_tracker, now)

    candidates = []
    diagnostics = {}
    for instrument, meta in cfg.INSTRUMENTS.items():
        try:
            market = build_market(feed, instrument, mode=mode)
            bars_diag = scan_diagnostics.bars_report(instrument, market["entry"], now)
            print(bars_diag)
            candidate = strat.find_candidate(market["entry"])
            if not candidate:
                diagnostics[instrument] = {"pattern": None, "direction": None, "score": None,
                                            "blocked": f"no pattern detected ({bars_diag.split(': ', 1)[1]})"}
                continue
            scored = strat.score_candidate(instrument, meta["class"], candidate, market, now, level_store,
                                            diagnostic=True, mode=mode)
            diagnostics[instrument] = {"pattern": scored["pattern"], "direction": scored["direction"],
                                        "score": scored["score"], "blocked": scored["blocked"]}
            if scored["blocked"] is None:
                candidates.append((instrument, scored))
        except Exception:
            # One instrument's scoring must never take down the scan for the
            # other three, or block an already-collected qualifying alert.
            print(f"[{instrument}] scoring failed:\n{traceback.format_exc()}")
            diagnostics[instrument] = {"pattern": None, "direction": None, "score": None,
                                        "blocked": "internal error (see logs)"}

    candidates = dedup_us_index_candidates(candidates)

    for instrument, scored in candidates:
        cls = cfg.INSTRUMENTS[instrument]["class"]

        if suppress_new_alerts:
            continue  # daily loss limit, manual /blackout, or news blackout — no new entries

        if scored["score"] >= mode.aplus_min_score:
            if hard_flat_active(now, cls):
                continue  # no new entry alerts after 18:30 UTC, US indices
            if watch_tracker.has_active(instrument) or pending_store.get(instrument):
                continue
            # Section 5.6 — A+ waits for one candle's confirmation; WATCH stays instant.
            pending_store.add(instrument, scored)
            continue

        if scored["score"] >= mode.watch_min_score:
            if watch_tracker.has_active(instrument):
                continue  # Section 3.4 cooldown — one active WATCH per instrument
            expires_at = now + timedelta(minutes=mode.watch_expiry_minutes)
            send_telegram(format_watch_alert(scored, expires_at, mode=mode))
            watch_tracker.add(scored, now)

    main_state["last_scan_time"] = now.strftime("%Y-%m-%d %H:%M UTC")
    main_state["last_scan_mode"] = mode.name
    main_state["last_diagnostics"] = diagnostics
    save_json(MAIN_STATE_PATH, main_state)
    return diagnostics


if __name__ == "__main__":
    run()
