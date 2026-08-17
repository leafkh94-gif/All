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
from strategy import economic_calendar
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
    try:
        requests.post(
            f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
            json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text}, timeout=20)
    except requests.RequestException as e:
        # A transient Telegram error (429/5xx/network) must not raise out of a
        # scan and drop the remaining instruments' alerts. Log and move on.
        print(f"[telegram] send failed: {e}")


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

    def has_active(self, instrument):
        return instrument in self._data

    def add(self, scored, now_utc):
        record = {
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
        if scored.get("pattern") == "SCALP_SWEEP_BOS":
            for k in ("has_fvg", "scalp_leg_origin", "scalp_structure_level", "scalp_bos_close"):
                if scored.get(k) is not None:
                    record[k] = scored[k]
        self._data[scored["instrument"]] = record
        save_json(self.path, self._data)

    def _cancel(self, instrument, entry, reason, now_utc):
        del self._data[instrument]
        save_json(self.path, self._data)
        _append_trade_log({
            "instrument": instrument,
            "pattern": entry.get("pattern"),
            "direction": entry["direction"],
            "outcome": "no_fill_" + reason.lower(),
            "r_multiple": 0.0,
            "closed_at": now_utc.isoformat(),
        })
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

            candles = feed.get_candles(instrument, "5min", n=2)
            if candles:
                bar_high = candles[-1]["h"]
                bar_low = candles[-1]["l"]
            else:
                bar_high = bar_low = price

            touched = (direction == "BUY" and bar_low <= e["entry_price"]) or (
                direction == "SELL" and bar_high >= e["entry_price"])
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
                    self._cancel(instrument, e, "SWEEP_VIOLATED", now_utc)
                    continue

            if leg_origin is not None and leg_end is not None:
                leg_size = abs(leg_end - leg_origin)
                left_without_us = (direction == "BUY" and price > leg_end + leg_size) or (
                    direction == "SELL" and price < leg_end - leg_size)
                if left_without_us:
                    self._cancel(instrument, e, "LEFT_WITHOUT_US", now_utc)
                    continue

            if now_utc - alert_time > timedelta(minutes=cfg.PENDING_ORDER_MAX_MINUTES):
                send_telegram(
                    f"⌛ {instrument} entry expired (EXPIRED).\n"
                    f"Price did not reach entry zone within "
                    f"{_format_duration(cfg.PENDING_ORDER_MAX_MINUTES)}.\n"
                    f"Setup cancelled. No action needed."
                )
                _append_trade_log({
                    "instrument": instrument,
                    "pattern": e.get("pattern"),
                    "direction": e["direction"],
                    "outcome": "no_fill_expired",
                    "r_multiple": 0.0,
                    "closed_at": now_utc.isoformat(),
                })
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

    def has_active(self, instrument):
        return instrument in self._data

    def add(self, scored, now_utc):
        entry_price = scored["entry_price"]
        stop_loss = scored["stop_loss"]
        record = {
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
            "mfe": 0.0,
            "mae": 0.0,
        }
        if scored.get("pattern") == "SCALP_SWEEP_BOS":
            record["has_fvg"] = scored.get("has_fvg")
            record["sweep_extreme"] = scored.get("scalp_leg_origin")
            record["structure_level"] = scored.get("scalp_structure_level")
            record["bos_close"] = scored.get("scalp_bos_close")
            record["leg_origin"] = scored.get("leg_origin")
            record["leg_end"] = scored.get("leg_end")
        self._data[scored["instrument"]] = record
        save_json(self.path, self._data)

    def _close(self, instrument, t, now_utc, outcome, r_multiple):
        del self._data[instrument]
        save_json(self.path, self._data)
        log_entry = {
            "instrument": instrument, "pattern": t.get("pattern"), "direction": t["direction"],
            "outcome": outcome, "r_multiple": round(r_multiple, 2), "closed_at": now_utc.isoformat(),
            "mfe": round(t.get("mfe", 0.0), 5),
            "mae": round(t.get("mae", 0.0), 5),
        }
        if t.get("pattern") == "SCALP_SWEEP_BOS":
            for k in ("has_fvg", "sweep_extreme", "structure_level", "bos_close",
                       "leg_origin", "leg_end", "entry_price", "initial_risk"):
                if t.get(k) is not None:
                    log_entry[k] = t[k]
        _append_trade_log(log_entry, path=self.trade_log_path)

    def _maybe_trail_runner_stop(self, instrument, t, feed):
        """Runner-phase (post-TP2) optional trail: move SL to the most
        recent confirmed M15 minor swing in the trade's favor, but only
        ever toward price, never away from it."""
        candles = feed.get_candles(instrument, "5min", n=30)
        if not candles or len(candles) < 6:
            return
        df = pd.DataFrame(candles)
        is_buy = t["direction"] == "BUY"
        # Inline minor-swing detection: a bar whose extreme beats both neighbours.
        col = "l" if is_buy else "h"
        swings = []
        for i in range(2, len(df) - 2):
            v = df[col].iloc[i]
            seg = df[col].iloc[i - 2: i + 3]
            if (is_buy and v == seg.min()) or (not is_buy and v == seg.max()):
                swings.append((i, float(v)))
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

            candles = feed.get_candles(instrument, "5min", n=2)
            if candles:
                bar_high = candles[-1]["h"]
                bar_low = candles[-1]["l"]
            else:
                bar_high = bar_low = price

            mfe_price = bar_high if is_buy else bar_low
            mae_price = bar_low if is_buy else bar_high
            mfe_excursion = (mfe_price - entry_price) if is_buy else (entry_price - mfe_price)
            mae_excursion = (mae_price - entry_price) if is_buy else (entry_price - mae_price)
            if mfe_excursion > t.get("mfe", 0.0):
                t["mfe"] = mfe_excursion
            if mae_excursion < t.get("mae", 0.0):
                t["mae"] = mae_excursion
            save_json(self.path, self._data)

            if not t["tp1_hit"]:
                hit_tp1 = bar_high >= t["tp1"] if is_buy else bar_low <= t["tp1"]
                hit_stop = bar_low <= t["stop_loss"] if is_buy else bar_high >= t["stop_loss"]
                if hit_tp1 and hit_stop:
                    hit_tp1 = False
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
                hit_tp2 = bar_high >= t["tp2"] if is_buy else bar_low <= t["tp2"]
                hit_be = bar_low <= t["stop_loss"] if is_buy else bar_high >= t["stop_loss"]
                if hit_tp2 and hit_be:
                    hit_tp2 = False
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
                hit_tp3 = bar_high >= t["tp3"] if is_buy else bar_low <= t["tp3"]
                hit_runner_stop = bar_low <= t["stop_loss"] if is_buy else bar_high >= t["stop_loss"]
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

            # Only warn about the upcoming hard flat if session_cutoff is
            # actually on for this instrument -- 24/7 mode has no hard flat,
            # so this reminder would be misleading.
            session_cutoff = cfg.INSTRUMENT_PROFILES.get(instrument, {}).get("session_cutoff", False)
            if session_cutoff and not t["warned_1800"] and (now_utc.hour, now_utc.minute) >= (
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
_PATTERN_DISPLAY = {
    "GOLDEN_TRIO": "Golden Trio (Turtle + RSI + ZLSMA)",
}


def _pattern_name(raw):
    return _PATTERN_DISPLAY.get(raw, raw)


def _breakdown_summary(scored):
    parts = [f"{tag} {pts:+d}" for tag, pts in scored.get("breakdown", []) if pts]
    return " · ".join(parts)


def format_watch_alert(scored, expires_at, mode=None):
    m = mode or modes.STANDARD
    return (
        f"⚡ WATCH — {scored['instrument']}\n"
        f"Potential {_pattern_name(scored['pattern'])} forming.\n"
        f"Direction: {scored['direction']}\n"
        f"Entry zone: {scored['entry_price']:g}\n"
        f"Score: {scored['score']}/100  |  Bias: {scored['htf_bias']}\n"
        f"Expires: {expires_at.strftime('%H:%M')} UTC ({_format_duration(m.watch_expiry_minutes)})"
    )


def format_aplus_alert(scored, now_utc, mode=None):
    expiry = now_utc + timedelta(minutes=cfg.PENDING_ORDER_MAX_MINUTES)
    risk = abs(scored["entry_price"] - scored["stop_loss"])
    rsi = scored.get("rsi")
    zlsma = scored.get("zlsma")
    lower = scored.get("turtle_lower")
    upper = scored.get("turtle_upper")
    diag_bits = []
    if rsi is not None:
        diag_bits.append(f"RSI {rsi:.1f}")
    if zlsma is not None:
        diag_bits.append(f"ZLSMA {zlsma:g}")
    if lower is not None and upper is not None:
        diag_bits.append(f"Turtle {lower:g}–{upper:g}")
    diag_line = "   " + " · ".join(diag_bits) + "\n" if diag_bits else ""
    return (
        f"🟢 A+ SIGNAL — {scored['instrument']}\n\n"
        f"Direction:  {scored['direction']}\n"
        f"Entry:      {scored['entry_price']:g}\n"
        f"Stop Loss:  {scored['stop_loss']:g}  (behind the reversal wick + buffer)\n"
        f"Risk (R):   {risk:g}\n"
        f"TP1:        {scored['tp1']:g}   ← close 50%, SL to breakeven\n"
        f"TP2:        {scored['tp2']:g}   ← close 30%, SL to TP1\n"
        f"TP3:        {scored['tp3']:g}   (opposite Turtle band)  ← runner 20%\n\n"
        f"Expires:    {expiry.strftime('%H:%M')} UTC  ({_format_duration(cfg.PENDING_ORDER_MAX_MINUTES)})\n\n"
        f"📋 Reason: {_pattern_name(scored['pattern'])}\n"
        f"{diag_line}"
        f"   Score: {scored['score']}/100  |  Bias: {scored['htf_bias']}  |  {_breakdown_summary(scored)}\n\n"
        f"📐 Position size: your_risk_$ / {risk:g} = lots/units\n\n"
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
    for instrument in cfg.ACTIVE_INSTRUMENTS:
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
    for instrument in cfg.ACTIVE_INSTRUMENTS:
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
    # Golden Trio needs at least 110 bars (ZLSMA(50) is SMA-of-SMA -> ~100
    # bars to stabilise, plus the RSI oversold-lookback tail). 160 gives
    # comfortable slack over the scan_diagnostics MIN_BARS_NEEDED threshold.
    return {
        "entry": feed.get_candles(instrument, m.entry_timeframe, n=160),
        "m5": feed.get_candles(instrument, "5min", n=160),
        "h1": feed.get_candles(instrument, "1h", n=160),
        "h4": feed.get_candles(instrument, "4h", n=260),
    }


# ─────────────────────────────────────────────────────────────────────
# Section 5.6 — 3-candle confirmation for pending A+ setups
# ─────────────────────────────────────────────────────────────────────
def evaluate_pending_confirmations(pending_store, feed, level_store, now_utc, entry_tracker, main_state,
                                    mode=None):
    m = mode or modes.STANDARD
    for instrument, scored in pending_store.items():
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
                pending_store=pending_store, mode=m)

        if rescored and rescored["score"] >= m.aplus_min_score:
            send_telegram(format_aplus_alert(rescored, now_utc, mode=m))
            entry_tracker.add(rescored, now_utc)
            main_state["aplus_count"] = main_state.get("aplus_count", 0) + 1
        pending_store.remove(instrument)


# ─────────────────────────────────────────────────────────────────────
# Section 4 — Health check
# ─────────────────────────────────────────────────────────────────────
def _closed_trade_stats(entries):
    count = len(entries)
    if count == 0:
        return 0, 0.0, 0.0
    wins = sum(1 for e in entries if e["r_multiple"] > 0)
    avg_r = sum(e["r_multiple"] for e in entries) / count
    return count, wins / count, avg_r


def weekly_performance_report_text(entries, now_utc):
    cutoff = now_utc - timedelta(days=7)
    recent = []
    for e in entries:
        try:
            closed_at = datetime.fromisoformat(e["closed_at"])
            if closed_at.tzinfo is None:
                closed_at = closed_at.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        if closed_at >= cutoff:
            recent.append(e)

    if not recent:
        return "🗓️ Weekly performance: no trades closed this week."

    count, win_rate, avg_r = _closed_trade_stats(recent)
    total_r = sum(e["r_multiple"] for e in recent)
    lines = ["🗓️ Weekly performance summary",
              f"Trades closed: {count}, {win_rate:.0%} win rate, {avg_r:+.2f}R avg, {total_r:+.2f}R total"]
    by_pattern = {}
    for e in recent:
        by_pattern.setdefault(e.get("pattern") or "unknown", []).append(e)
    for pattern, group in sorted(by_pattern.items(), key=lambda kv: -len(kv[1])):
        c, wr, ar = _closed_trade_stats(group)
        lines.append(f"  {pattern}: {c} trades, {wr:.0%} win rate, {ar:+.2f}R avg")
    return "\n".join(lines)


def maybe_send_weekly_performance_report(main_state, now_utc, path=None):
    """Fires once per ISO week, Friday 21:00 UTC -- end of the forex trading
    week, right alongside maybe_record_weekly_levels."""
    if now_utc.weekday() != 4 or now_utc.hour != 21:
        return
    week_key = now_utc.strftime("%G-W%V")
    if main_state.get("last_weekly_report_week") == week_key:
        return
    entries = load_json(path or TRADE_LOG_PATH).get("entries", [])
    send_telegram(weekly_performance_report_text(entries, now_utc))
    main_state["last_weekly_report_week"] = week_key


_OUTCOME_LABELS = {
    "tp3_runner_complete":       "All 3 targets hit",
    "runner_stopped":            "TP1 + TP2 hit, runner stopped at TP1",
    "breakeven_after_tp1":       "TP1 hit, rest closed at breakeven",
    "session_cutoff_runner":     "TP1 + TP2 banked, closed at session end",
    "session_cutoff_after_tp1":  "TP1 banked, closed at session end",
    "session_cutoff_before_tp1": "Closed at session end",
    "stop_before_tp1":           "Stop loss hit — no profit",
    "no_fill_expired":           "Price never reached our entry",
    "no_fill_sweep_violated":    "Cancelled — key level was broken",
    "no_fill_left_without_us":   "Price moved away without filling",
}


def daily_digest_text(now_utc, path=None):
    """Plain-English market-close summary of today's alert outcomes."""
    entries = load_json(path or TRADE_LOG_PATH).get("entries", [])
    today = now_utc.strftime("%Y-%m-%d")
    today_entries = []
    for e in entries:
        try:
            closed_at = datetime.fromisoformat(e["closed_at"])
            if closed_at.tzinfo is None:
                closed_at = closed_at.replace(tzinfo=timezone.utc)
            if closed_at.strftime("%Y-%m-%d") == today:
                today_entries.append(e)
        except (KeyError, TypeError, ValueError):
            continue

    lines = [f"📅 Daily Summary — {now_utc.strftime('%d %b %Y')}", ""]
    if not today_entries:
        lines.append("No alerts concluded today.")
        return "\n".join(lines)

    no_fill = [e for e in today_entries if e.get("outcome", "").startswith("no_fill_")]
    filled = [e for e in today_entries if not e.get("outcome", "").startswith("no_fill_")]
    wins = [e for e in filled if e.get("r_multiple", 0) > 0]
    losses = [e for e in filled if e.get("r_multiple", 0) <= 0]

    if wins:
        lines.append("✅ Took profit:")
        for e in wins:
            label = _OUTCOME_LABELS.get(e.get("outcome", ""), e.get("outcome", ""))
            lines.append(f"   {e['instrument']} {e.get('direction', '')} — {label}  (+{e['r_multiple']:.1f}R)")
        lines.append("")

    if losses:
        lines.append("❌ Stopped out (no profit):")
        for e in losses:
            label = _OUTCOME_LABELS.get(e.get("outcome", ""), e.get("outcome", ""))
            lines.append(f"   {e['instrument']} {e.get('direction', '')} — {label}  ({e['r_multiple']:+.1f}R)")
        lines.append("")

    if no_fill:
        lines.append("⏳ Never entered (price didn't reach our entry):")
        for e in no_fill:
            label = _OUTCOME_LABELS.get(e.get("outcome", ""), e.get("outcome", ""))
            lines.append(f"   {e['instrument']} {e.get('direction', '')} — {label}")
        lines.append("")

    parts = []
    if wins:
        parts.append(f"{len(wins)} win{'s' if len(wins) != 1 else ''}")
    if losses:
        parts.append(f"{len(losses)} loss{'es' if len(losses) != 1 else ''}")
    if no_fill:
        parts.append(f"{len(no_fill)} never filled")
    lines.append(f"Bottom line: {', '.join(parts)}.")
    if filled:
        total_r = sum(e.get("r_multiple", 0) for e in filled)
        lines.append(f"Net result: {total_r:+.1f}R")
    return "\n".join(lines)


def maybe_send_daily_digest(main_state, now_utc, path=None):
    """Fires once per day at 18:30 UTC alongside the hard flat."""
    if (now_utc.hour, now_utc.minute) < (cfg.HARD_FLAT_UTC_HOUR, cfg.HARD_FLAT_UTC_MINUTE):
        return
    today_key = now_utc.strftime("%Y-%m-%d")
    if main_state.get("last_daily_digest_date") == today_key:
        return
    send_telegram(daily_digest_text(now_utc, path=path))
    main_state["last_daily_digest_date"] = today_key


def maybe_send_health_check(main_state, watch_tracker, now_utc):
    last = main_state.get("last_health_check_time")
    if last and now_utc - datetime.fromisoformat(last) <= timedelta(hours=cfg.HEALTH_CHECK_INTERVAL_HOURS):
        return
    send_telegram(format_health_check(main_state, watch_tracker, now_utc))
    main_state["last_health_check_time"] = now_utc.isoformat()


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
# Duplicate / flip-flop cooldown -- prevent alert spam when the same
# reversal setup keeps re-firing on consecutive bars.
# ─────────────────────────────────────────────────────────────────────
def _cooldown_key(instrument, direction):
    return f"cooldown/{instrument}/{direction}"


def cooldown_blocks_alert(main_state, instrument, direction, entry_price, now_utc):
    """Return (blocked: bool, reason: str). Same-direction alerts within
    COOLDOWN_SAME_DIRECTION_MINUTES are blocked unless price moved at least
    COOLDOWN_SAME_DIRECTION_POINTS. Opposite-direction alerts within
    COOLDOWN_OPPOSITE_DIRECTION_MINUTES are always blocked (kills the
    BUY -> SELL flip-flop churn)."""
    opposite = "SELL" if direction == "BUY" else "BUY"
    price_moved_threshold = cfg.COOLDOWN_SAME_DIRECTION_POINTS * cfg.POINT_VALUE

    # Same direction: only if within window AND price hasn't moved enough.
    same = main_state.get(_cooldown_key(instrument, direction))
    if same:
        last_ts = datetime.fromisoformat(same["t"])
        elapsed = (now_utc - last_ts).total_seconds() / 60
        if elapsed < cfg.COOLDOWN_SAME_DIRECTION_MINUTES:
            price_move = abs(entry_price - same["price"])
            if price_move < price_moved_threshold:
                return True, (f"same-direction cooldown ({elapsed:.0f}<{cfg.COOLDOWN_SAME_DIRECTION_MINUTES}m,"
                              f" moved {price_move:g}<{price_moved_threshold:g})")

    # Opposite direction: any alert within window is blocked (flip-flop).
    other = main_state.get(_cooldown_key(instrument, opposite))
    if other:
        last_ts = datetime.fromisoformat(other["t"])
        elapsed = (now_utc - last_ts).total_seconds() / 60
        if elapsed < cfg.COOLDOWN_OPPOSITE_DIRECTION_MINUTES:
            return True, (f"opposite-direction cooldown ({elapsed:.0f}<{cfg.COOLDOWN_OPPOSITE_DIRECTION_MINUTES}m,"
                          f" last {opposite})")

    return False, ""


def record_alert_for_cooldown(main_state, instrument, direction, entry_price, now_utc):
    main_state[_cooldown_key(instrument, direction)] = {
        "t": now_utc.isoformat(),
        "price": float(entry_price),
    }


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

    econ_events = economic_calendar.fetch_upcoming_events(now)
    econ_blackout, econ_event_name = economic_calendar.is_economic_blackout_active(now, econ_events)
    main_state["econ_blackout_event"] = econ_event_name if econ_blackout else None

    suppress_new_alerts = (breaker_tripped or manual_blackout_active(main_state, now)
                           or news_blackout or econ_blackout)

    feed = CapitalFeed()
    feed.open_session()
    feed.resolve_epics()

    level_store = ind.LevelStore()
    pending_store = strat.PendingAPlusStore()
    entry_tracker = ActiveEntryTracker()
    open_trade_tracker = OpenTradeTracker()

    maybe_record_daily_levels(feed, level_store, now)
    maybe_record_weekly_levels(feed, level_store, now)
    maybe_send_weekly_performance_report(main_state, now)
    maybe_send_daily_digest(main_state, now)

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
    for instrument in cfg.ACTIVE_INSTRUMENTS:
        meta = cfg.INSTRUMENTS[instrument]
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
            candidate, block_reason = strat.find_candidate_diag(market["entry"])
            if not candidate:
                # block_reason names the specific gate that killed every
                # direction: chop / rsi-seq / turtle / zlsma-against / body /
                # warmup. Much more actionable than a generic "detectors too
                # tight" line.
                diagnostics[instrument] = {"pattern": None, "direction": None, "score": None,
                                            "blocked": f"blocked: {block_reason}"}
                continue
            # Spread guard: skip signals when the live spread is wider than
            # MAX_SPREAD_POINTS. Every candle carries capital_feed's
            # _implied_spread; the latest bar's spread is the freshest proxy
            # available without a dedicated bid/offer snapshot API call.
            latest_spread = market["entry"][-1].get("spread") if market["entry"] else None
            max_spread_price = cfg.MAX_SPREAD_POINTS * cfg.POINT_VALUE
            if latest_spread is not None and latest_spread > max_spread_price:
                diagnostics[instrument] = {"pattern": candidate["pattern"],
                                            "direction": candidate["direction"], "score": None,
                                            "blocked": f"spread {latest_spread:g} > {max_spread_price:g}"}
                continue
            scored = strat.score_candidate(instrument, meta["class"], candidate, market, now, level_store,
                                            mode=mode)
            diagnostics[instrument] = {
                "pattern": scored["pattern"], "direction": scored["direction"],
                "score": scored["score"], "tier": scored["tier"],
                "zlsma": scored["zlsma_status"], "h4": scored["htf_bias"],
                "blocked": None if scored["tier"] != "NONE" else "score below WATCH threshold",
            }
            if scored["tier"] != "NONE":
                candidates.append((instrument, scored))
        except Exception:
            # One instrument's scoring must never take down the scan for the
            # other three, or block an already-collected qualifying alert.
            print(f"[{instrument}] scoring failed:\n{traceback.format_exc()}")
            diagnostics[instrument] = {"pattern": None, "direction": None, "score": None,
                                        "blocked": "internal error (see logs)"}

    for instrument, scored in candidates:
        if suppress_new_alerts:
            continue  # daily loss limit, manual /blackout, or news blackout

        # Duplicate/flip-flop cooldown -- kills repeat alerts on the same
        # reversal and BUY <-> SELL churn during chop.
        blocked, reason = cooldown_blocks_alert(
            main_state, instrument, scored["direction"], scored["entry_price"], now)
        if blocked:
            diagnostics[instrument] = {"pattern": scored["pattern"], "direction": scored["direction"],
                                        "score": scored["score"], "blocked": reason}
            continue

        if scored.get("aplus_eligible"):
            if hard_flat_active(now, instrument, mode=mode):
                continue
            if watch_tracker.has_active(instrument) or pending_store.get(instrument):
                continue
            if entry_tracker.has_active(instrument) or open_trade_tracker.has_active(instrument):
                continue
            # A+ waits for one candle's confirmation; WATCH stays instant.
            pending_store.add(instrument, scored)
            record_alert_for_cooldown(main_state, instrument, scored["direction"], scored["entry_price"], now)
            continue

        if scored["score"] >= mode.watch_min_score:
            if watch_tracker.has_active(instrument):
                continue
            if entry_tracker.has_active(instrument) or open_trade_tracker.has_active(instrument):
                continue
            expires_at = now + timedelta(minutes=mode.watch_expiry_minutes)
            send_telegram(format_watch_alert(scored, expires_at, mode=mode))
            watch_tracker.add(scored, now)
            record_alert_for_cooldown(main_state, instrument, scored["direction"], scored["entry_price"], now)

    main_state["last_scan_time"] = now.strftime("%Y-%m-%d %H:%M UTC")
    main_state["last_scan_mode"] = mode.name
    main_state["last_diagnostics"] = diagnostics
    save_json(MAIN_STATE_PATH, main_state)
    return diagnostics


if __name__ == "__main__":
    run()
