"""Tiny Gemini REST client shared by #11 verdict and #1 planner's prioritise step.

- Model from GEMINI_MODEL; if the API rejects it, list models and pick "Gemini 3.5 Flash Lite".
- Responses cached in cache/gemini/ (keyed by model + prompt). Calls spaced >= 4.5 s (free tier 15/min).
- Never raises: returns None when the key is missing, quota is hit, or anything fails.
"""
from __future__ import annotations

import json
import time

import requests

from prarthana.common import env, cache_get, cache_set, log

API = "https://generativelanguage.googleapis.com/v1beta"
FALLBACK_DISPLAY = "Gemini 3.5 Flash Lite"
MIN_GAP = 4.5
_last_call = [0.0]
_model = [None]


def _headers():
    return {"x-goog-api-key": env("GEMINI_API_KEY"), "Content-Type": "application/json"}


def pick_model_from_list(state_log=None):
    try:
        r = requests.get(f"{API}/models", headers=_headers(), params={"pageSize": 200}, timeout=20)
        for m in r.json().get("models", []):
            if m.get("displayName", "").strip().lower() == FALLBACK_DISPLAY.lower():
                name = m["name"].split("/", 1)[-1]
                log(state_log, f"gemini: using model id {name} from models list")
                return name
    except Exception as e:
        log(state_log, f"gemini: models list failed: {type(e).__name__}")
    return None


def _call(model: str, prompt: str):
    wait = MIN_GAP - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
    return requests.post(f"{API}/models/{model}:generateContent", headers=_headers(), json=body, timeout=60)


def gemini_json(prompt: str, state_log=None):
    """Send prompt, expect JSON back. Returns parsed JSON or None."""
    if not env("GEMINI_API_KEY"):
        log(state_log, "gemini: GEMINI_API_KEY missing, using fallback")
        return None
    model = _model[0] or env("GEMINI_MODEL") or "gemini-3.5-flash-lite"
    cached = cache_get("gemini", model + "\n" + prompt)
    if cached is not None:
        return cached
    try:
        r = _call(model, prompt)
        if r.status_code in (400, 404) and "model" in r.text.lower():
            alt = pick_model_from_list(state_log)
            if alt and alt != model:
                model = alt
                r = _call(model, prompt)
        if r.status_code in (500, 502, 503, 504):   # Gemini briefly unavailable: wait and retry once
            log(state_log, f"gemini: HTTP {r.status_code}, retrying once in 5 s")
            time.sleep(5)
            r = _call(model, prompt)
        if r.status_code == 429:
            log(state_log, "gemini: quota / rate limit hit (429), using fallback")
            return None
        if r.status_code != 200:
            log(state_log, f"gemini: HTTP {r.status_code}, using fallback")
            return None
        _model[0] = model
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1]
        data = json.loads(text)
        cache_set("gemini", model + "\n" + prompt, data)
        return data
    except Exception as e:
        log(state_log, f"gemini: failed ({type(e).__name__}), using fallback")
        return None
