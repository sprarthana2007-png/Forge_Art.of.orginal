"""#19 The planner learns from the owner's answers.

Every confirm(yes/no) is saved in cache/feedback.json with the signals that fired on that site. On the next run,
compute_weights() turns that history into a trust multiplier per signal:

    weight = 1 + STEP_UP * (#"fake" answers where it fired) - STEP_DOWN * (#"not fake" answers where it fired)

clamped to [0.5, 1.5] (never below half, never above 1.5x the original points). Example: "unofficial contacts" fired
on 3 sites the owner said were NOT fake -> 1 - 3 * 0.10 = 0.70, so it is worth 15 * 0.70 = 11 points instead of 15.
Wrong flags are corrected faster (0.10 per answer) than confirmations boost a signal (0.05), on purpose: a signal
that keeps flagging legit sites should lose trust quickly, while confirmations should not inflate scores quickly.
"""
from __future__ import annotations

import datetime as dt
import json

from schemas import Suspect
from prarthana.common import CACHE, log

SIGNALS = {"photos": "Product photos", "logo": "Logo / favicon", "price": "Price undercut",
           "contacts": "Unofficial contacts", "young_domain": "Young domain", "cloaking": "Cloaking",
           "relaunch": "Relaunch"}
STEP_UP = 0.05
STEP_DOWN = 0.10
MIN_W, MAX_W = 0.5, 1.5


def _file():
    return CACHE / "feedback.json"


def load_feedback() -> list:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        return []


def fired_signals(s: Suspect) -> list:
    """Signals that fired on this suspect (from the scorer; falls back to parsing the reasons)."""
    if getattr(s, "signals_fired", None):
        return list(s.signals_fired)
    keys = []
    for r in s.reasons or []:
        t = r.lower()
        if not t.startswith("+"):
            continue
        for key, word in (("relaunch", "relaunch of"), ("photos", "photo"), ("logo", "logo"), ("price", "price"),
                          ("contacts", "contact"), ("young_domain", "domain registered"), ("cloaking", "cloaking")):
            if word in t:
                keys.append(key)
                break
    return keys


def record(s: Suspect, yes: bool) -> None:
    """Save the owner's answer (latest answer per URL wins). No contacts or page content are stored."""
    try:
        data = [x for x in load_feedback() if x.get("url") != s.url]
        data.append({"url": s.url, "domain": s.domain, "answer": "fake" if yes else "not_fake",
                     "signals": fired_signals(s), "score": s.score,
                     "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
        p = _file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except Exception:
        pass


def compute_weights(feedback: list | None = None) -> dict:
    """{signal: multiplier} for every signal, 1.0 when there is no history."""
    fb = load_feedback() if feedback is None else feedback
    out = {}
    for key in SIGNALS:
        n_fake = sum(1 for x in fb if x.get("answer") == "fake" and key in (x.get("signals") or []))
        n_not = sum(1 for x in fb if x.get("answer") == "not_fake" and key in (x.get("signals") or []))
        w = 1 + STEP_UP * n_fake - STEP_DOWN * n_not
        out[key] = round(min(MAX_W, max(MIN_W, w)), 2)
    return out


def weight_rows(feedback: list | None = None) -> list:
    """[(signal name, weight, #fake, #not fake)] for the PDF / logs."""
    fb = load_feedback() if feedback is None else feedback
    w = compute_weights(fb)
    rows = []
    for key, name in SIGNALS.items():
        n_fake = sum(1 for x in fb if x.get("answer") == "fake" and key in (x.get("signals") or []))
        n_not = sum(1 for x in fb if x.get("answer") == "not_fake" and key in (x.get("signals") or []))
        rows.append((name, w[key], n_fake, n_not))
    return rows


def describe(weights: dict, n_answers: int) -> str:
    changed = [f"{SIGNALS[k]} x{v:.2f}" for k, v in weights.items() if v != 1.0]
    if not n_answers:
        return "learning: no owner answers yet, all signals at default weight"
    return (f"learning: weights tuned from {n_answers} owner answer{'s' if n_answers != 1 else ''}: "
            + (", ".join(changed) if changed else "no change yet"))


def weights_for_run(state_log=None) -> dict:
    fb = load_feedback()
    w = compute_weights(fb)
    log(state_log, describe(w, len(fb)))
    return w
