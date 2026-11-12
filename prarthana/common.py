"""Small helpers shared by Prarthana's modules: env, cache, logging, domain parsing."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
# FORGE_CACHE_DIR / FORGE_OUTPUT_DIR let tests use throw-away folders
CACHE = Path(os.environ.get("FORGE_CACHE_DIR") or ROOT / "cache")
OUTPUT = Path(os.environ.get("FORGE_OUTPUT_DIR") or ROOT / "output")


def env(name: str, default: str = "") -> str:
    """Read an env var with ALL whitespace stripped (keys are sometimes pasted with spaces)."""
    v = os.environ.get(name)
    if v is None:
        return default
    return "".join(v.split())


def domain_of(url: str) -> str:
    """Host (with port if non-default) lower-cased, without leading www."""
    if "://" not in url:
        url = "http://" + url
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if p.port and p.port not in (80, 443):
        return f"{host}:{p.port}"
    return host


def registrable(domain: str) -> str:
    """Domain without port (for RDAP / allow-list)."""
    return domain.split(":")[0]


def cache_path(sub: str, key: str, ext: str = "json") -> Path:
    d = CACHE / sub
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.{ext}"


def cache_get(sub: str, key: str):
    p = cache_path(sub, key)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def cache_set(sub: str, key: str, value) -> None:
    cache_path(sub, key).write_text(json.dumps(value, indent=1, default=list), encoding="utf-8")


def log(state_log, msg: str) -> None:
    print(f"[forge] {msg}")
    if state_log is not None:
        state_log.append(msg)


def launch_chromium(p):
    """Launch headless Chromium. Falls back to a pre-installed browser (env CHROMIUM_PATH or
    /opt/pw-browsers/chromium) when `playwright install chromium` was not run for this version."""
    import os as _os
    try:
        return p.chromium.launch()
    except Exception:
        for path in (env("CHROMIUM_PATH"), "/opt/pw-browsers/chromium", "/usr/bin/chromium"):  # last: Streamlit Cloud
            if path and _os.path.exists(path):
                return p.chromium.launch(executable_path=path, args=["--no-sandbox", "--disable-dev-shm-usage"])
        raise


def plural(n, word: str, many: str | None = None) -> str:
    """'1 look-alike', '2 look-alikes', '1 exact copy', '2 exact copies'."""
    if n == 1:
        return f"{n} {word}"
    if many is None:
        many = word[:-1] + "ies" if word.endswith("y") and word[-2:-1] not in "aeiou" else word + "s"
    return f"{n} {many}"
