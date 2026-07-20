"""
Trading Alert Bot — main loop (Section 9 implementation order).
Alert-only. Never executes trades. Scans every 15 minutes aligned to
:00/:15/:30/:45 UTC via GitHub Actions cron.
"""
import json
import os
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd
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
# Entry/SL/TP Selection Rules v1.3 §2 — pending-order lifecycle.
# Three cancellation reasons, checked in this order: touched (filled) takes
# priority over everything; then the two invalidation conditions; then the
# flat time-based expiry.
# ─────────────────────────────────────────────────────────────────────
_CANCEL_MESSAGES = {
    "SWEEP_VIOLATED": "A candle closed back beyond the sweep wick (leg_origin) before the order filled — the "
                       "setup's premise failed.",
    "LEFT_WITHOUT_US": "Price extended more than 1x the leg size beyond the move without ever filling — do not "
                        "chase, the trade is missed.",
}


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
            "tp3": scored.get("tp3"),
            "leg_origin": scored.get("leg_origin"),
            "leg_end": scored.get("leg_end"),
            "pattern": scored.get("pattern"),
            "alert_time": now_utc.isoformat(),
        }
        save_json(self.path, self._data)

    def _cancel(self, instrument, reason):
        del self._data[instrument]
        save_json(self.path, self._data)
        send_telegram(
            f"⌛ {instrument} entry cancelled ({reason}).\n"
            f"{_CANCEL_MESSAGES.get(reason, '')}\n"
            f"No action needed."
        )

    def evaluate_all(self, now_utc, feed, mode=None, open_tracker=None):
        # mode is accepted for call-site compatibility but no longer scales
        # the pending-order timer -- v1.3's 90-minute expiry is flat across
        # every instrument, not mode/instrument-scaled like the old system.
        for instrument, e in list(self._data.items()):
            alert_time = datetime.fromisoformat(e["alert_time"])
            price = feed.get_current_price(instrument)
            if price is None:
                continue
            direction = e["direction"]

            touched = (direction == "BUY" and price <= e["entry_price"]) or (
                direction == "SELL" and price >= e["entry_price"])
            if touched:
                del self._data[instrument]
                save_json(self.path, self._data)
                if open_tracker is not None and e.get("stop_loss") is not None:
                    open_tracker.add({**e, "instrument": instrument}, now_utc)
                continue

            leg_origin, leg_end = e.get("leg_origin"), e.get("leg_end")
            if leg_origin is not None:
                sweep_violated = (direction == "BUY" and price < leg_origin) or (
                    direction == "SELL" and price > leg_origin)
                if sweep_violated:
                    self._cancel(instrument, "SWEEP_VIOLATED")
                    continue

            if leg_origin is not None and leg_end is not None:
                leg_size = abs(leg_end - leg_origin)
                left_without_us = (direction == "BUY" and price > leg_end + leg_size) or (
                    direction == "SELL" and price < leg_end - leg_size)
                if left_without_us:
                    self._cancel(instrument, "LEFT_WITHOUT_US")
                    continue

            if now_utc - alert_time > timedelta(minutes=cfg.PENDING_ORDER_MAX_MINUTES):
                send_telegram(
                    f"⌛ {instrument} entry expired (EXPIRED).\n"
                    f"Price did not reach entry zone within "
                    f"{_format_duration(cfg.PENDING_ORDER_MAX_MINUTES)}.\n"
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
    """v1.3 §5 post-fill management: TP1 (50%, SL->breakeven), TP2 (30%,
    SL->TP1), runner (20%, targets TP3, SL trails behind new confirmed M15
    minor swings after TP2). A one-time 18:00 UTC heads-up alert precedes
    the existing 18:30 UTC hard flat (which now applies to every instrument,
    BTCUSD included)."""

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
            "tp3": scored["tp3"],
            "tp1_hit": False,
            "tp2_hit": False,
            "locked_r": 0.0,
            "warned_1800": False,
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

    def _maybe_trail_runner_stop(self, instrument, t, feed):
        """Runner-phase (post-TP2) optional trail: move SL to the most
        recent confirmed M15 minor swing in the trade's favor, but only
        ever toward price, never away from it."""
        candles = feed.get_candles(instrument, "15min", n=30)
        if not candles or len(candles) < 6:
            return
        df = pd.DataFrame(candles)
        is_buy = t["direction"] == "BUY"
        swings = strat._swings(df, "low" if is_buy else "high", window=2)
        if not swings:
            return
        _, latest_swing_price = swings[-1]
        better = (latest_swing_price > t["stop_loss"]) if is_buy else (latest_swing_price < t["stop_loss"])
        if better:
            t["stop_loss"] = float(latest_swing_price)
            save_json(self.path, self._data)

    def evaluate_all(self, now_utc, feed, mode=None):
        for instrument, t in list(self._data.items()):
            price = feed.get_current_price(instrument)
            if price is None:
                continue
            is_buy = t["direction"] == "BUY"
            entry_price, initial_risk = t["entry_price"], t["initial_risk"]
            closed_this_cycle = False

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
                        f"Stop loss moved to breakeven ({entry_price}) on the rest — targeting TP2 ({t['tp2']})."
                    )
                    continue
                if hit_stop:
                    r = _r_multiple(t["direction"], entry_price, initial_risk, t["stop_loss"])
                    self._close(instrument, t, now_utc, "stop_before_tp1", r)
                    send_telegram(f"🛑 {instrument} stop loss hit @ {t['stop_loss']}. Full position closed.")
                    closed_this_cycle = True
            elif not t["tp2_hit"]:
                hit_tp2 = price >= t["tp2"] if is_buy else price <= t["tp2"]
                hit_be = price <= t["stop_loss"] if is_buy else price >= t["stop_loss"]
                if hit_tp2:
                    t["tp2_hit"] = True
                    t["locked_r"] += 0.3 * _r_multiple(t["direction"], entry_price, initial_risk, t["tp2"])
                    t["stop_loss"] = t["tp1"]
                    save_json(self.path, self._data)
                    send_telegram(
                        f"🎯 {instrument} TP2 hit @ {t['tp2']}.\n"
                        f"Close 30% of the position now.\n"
                        f"Stop loss moved to TP1 ({t['tp1']}) on the runner (20%) — targeting TP3 ({t['tp3']})."
                    )
                    continue
                if hit_be:
                    r = t["locked_r"] + 0.5 * _r_multiple(t["direction"], entry_price, initial_risk, t["stop_loss"])
                    self._close(instrument, t, now_utc, "breakeven_after_tp1", r)
                    send_telegram(
                        f"⚖️ {instrument} breakeven stop hit after TP1. "
                        f"Remainder closed at entry — partial profit locked in."
                    )
                    closed_this_cycle = True
            else:
                hit_tp3 = price >= t["tp3"] if is_buy else price <= t["tp3"]
                hit_runner_stop = price <= t["stop_loss"] if is_buy else price >= t["stop_loss"]
                if hit_tp3:
                    r = t["locked_r"] + 0.2 * _r_multiple(t["direction"], entry_price, initial_risk, t["tp3"])
                    self._close(instrument, t, now_utc, "tp3_runner_complete", r)
                    send_telegram(f"✅ {instrument} TP3 hit @ {t['tp3']}. Close the runner — trade complete.")
                    closed_this_cycle = True
                elif hit_runner_stop:
                    r = t["locked_r"] + 0.2 * _r_multiple(t["direction"], entry_price, initial_risk, t["stop_loss"])
                    self._close(instrument, t, now_utc, "runner_stopped", r)
                    send_telegram(f"🏁 {instrument} runner stopped @ {t['stop_loss']}. Trade complete.")
                    closed_this_cycle = True
                else:
                    self._maybe_trail_runner_stop(instrument, t, feed)

            if closed_this_cycle:
                continue

            if not t["warned_1800"] and (now_utc.hour, now_utc.minute) >= (
                    cfg.WARNING_UTC_HOUR, cfg.WARNING_UTC_MINUTE) and (now_utc.hour, now_utc.minute) < (
                    cfg.HARD_FLAT_UTC_HOUR, cfg.HARD_FLAT_UTC_MINUTE):
                t["warned_1800"] = True
                save_json(self.path, self._data)
                send_telegram(
                    f"⏰ {instrument} — {cfg.WARNING_UTC_HOUR:02d}:{cfg.WARNING_UTC_MINUTE:02d} UTC.\n"
                    f"Get ready to close all remaining position manually by the "
                    f"{cfg.HARD_FLAT_UTC_HOUR:02d}:{cfg.HARD_FLAT_UTC_MINUTE:02d} UTC hard flat if not closed by then."
                )

            if hard_flat_active(now_utc, instrument, mode=mode):
                if t["tp2_hit"]:
                    r = t["locked_r"] + 0.2 * _r_multiple(t["direction"], entry_price, initial_risk, price)
                    outcome = "session_cutoff_runner"
                elif t["tp1_hit"]:
                    r = t["locked_r"] + 0.5 * _r_multiple(t["direction"], entry_price, initial_risk, price)
                    outcome = "session_cutoff_after_tp1"
                else:
                    r = _r_multiple(t["direction"], entry_price, initial_risk, price)
                    outcome = "session_cutoff_before_tp1"
                self._close(instrument, t, now_utc, outcome, r)
                send_telegram(
                    f"⏰ {instrument} — hard flat at "
                    f"{cfg.HARD_FLAT_UTC_HOUR:02d}:{cfg.HARD_FLAT_UTC_MINUTE:02d} UTC.\n"
                    f"Close all remaining position now."
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
    entry_basis = scored.get("entry_basis", "50% leg retrace")
    return (
        f"⚡ WATCH — {scored['instrument']}\n"
        f"Potential setup forming.\n"
        f"Direction: {scored['direction']}\n"
        f"Entry zone: {scored['entry_price']}  ({entry_basis})\n"
        f"Score: {scored['score']}/100\n"
        f"Expires: {expires_at.strftime('%H:%M')} UTC ({_format_duration(m.watch_expiry_minutes)})"
    )


def format_aplus_alert(scored, now_utc, mode=None):
    expiry = now_utc + timedelta(minutes=cfg.PENDING_ORDER_MAX_MINUTES)
    entry_basis = scored.get("entry_basis", "50% leg retrace")
    tp1_basis = scored.get("tp1_basis", "1.0R")
    tp2_note = "  (session/PDH-PDL level)" if scored.get("tp2_capped") else "  (1.8R fallback)"
    tp3_note = "  (external level)" if scored.get("tp3_capped") else "  (2.8R fallback)"
    risk = abs(scored["entry_price"] - scored["stop_loss"])
    return (
        f"🟢 A+ SIGNAL — {scored['instrument']}\n\n"
        f"Direction:  {scored['direction']}\n"
        f"Entry:      {scored['entry_price']}  ({entry_basis})\n"
        f"Stop Loss:  {scored['stop_loss']}  (behind sweep wick + buffer)\n"
        f"Risk (R):   {risk:g}\n"
        f"TP1:        {scored['tp1']}  ({tp1_basis})   ← close 50%, SL to breakeven\n"
        f"TP2:        {scored['tp2']}{tp2_note}   ← close 30%, SL to TP1\n"
        f"TP3:        {scored['tp3']}{tp3_note}   ← runner 20%, trail after TP2\n\n"
        f"Expires:    {expiry.strftime('%H:%M')} UTC  ({_format_duration(cfg.PENDING_ORDER_MAX_MINUTES)})\n\n"
        f"📋 Reason: {scored['breakdown']['pattern']} at {_level_description(scored)}\n"
        f"   Score: {scored['score']}/100  |  Bias: {scored['htf_bias']}\n\n"
        f"After TP1 → SL to breakeven. After TP2 → SL to TP1, runner (20%) targets TP3.\n"
        f"18:00 UTC → get ready to close manually. 18:30 UTC hard flat → close all remaining."
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
def hard_flat_active(now_utc, instrument, mode=None):
    m = mode or modes.STANDARD
    if not m.session_cutoff_enabled:
        return False  # swing-style modes intentionally hold across session boundaries
    if not cfg.INSTRUMENT_PROFILES.get(instrument, {}).get("session_cutoff", False):
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
    open_trade_tracker.evaluate_all(now, feed, mode=mode)
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
            if scan_diagnostics.is_data_problem(bars_diag):
                # Missing/too-few bars or a genuinely stale feed -- do not
                # let pattern detection run on data that can't be trusted,
                # or a "signal" could be built off a candle that's hours
                # behind where the instrument is actually trading.
                diagnostics[instrument] = {"pattern": None, "direction": None, "score": None,
                                            "blocked": bars_diag.split(': ', 1)[1]}
                continue
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
        if suppress_new_alerts:
            continue  # daily loss limit, manual /blackout, or news blackout — no new entries

        if scored["score"] >= mode.aplus_min_score:
            if hard_flat_active(now, instrument, mode=mode):
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
