"""Screenshots of a website for the Evidence page, taken in headless Chromium.

Used when a Brand protection investigation becomes a case (the online dashboard has no FORGE engine run to take
them). The picture gets a small address bar on top showing the real URL, the same layout as the demo screenshots,
so the Evidence page can show where it was taken. Never raises: on any failure it returns '' and the reason.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

BAR = 76            # px of address bar on top (views/evidence.py BROWSER_BAR crops it in thumbnails)
VIEWPORT = (1280, 800)
TIMEOUT_MS = 25000
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
CANDIDATES = ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/lib/chromium/chromium",
              "/opt/pw-browsers/chromium")


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception as first:
        for path in (os.getenv("CHROMIUM_PATH"), *CANDIDATES):
            if path and os.path.exists(path):
                try:
                    return p.chromium.launch(executable_path=path, args=["--no-sandbox", "--disable-dev-shm-usage"])
                except Exception:
                    continue
        raise first


def _with_bar(png: bytes, url: str) -> bytes:
    from PIL import Image, ImageDraw
    shot = Image.open(io.BytesIO(png)).convert("RGB")
    out = Image.new("RGB", (shot.width, shot.height + BAR), "#e9e4dc")
    draw = ImageDraw.Draw(out)
    draw.rounded_rectangle((90, 18, shot.width - 40, BAR - 18), radius=19, fill="#ffffff")
    for i, c in enumerate(("#e06c5a", "#e7b34a", "#6cae5a")):
        draw.ellipse((22 + i * 22, 30, 36 + i * 22, 44), fill=c)
    try:
        from PIL import ImageFont
        font = ImageFont.load_default(size=20)
    except Exception:
        font = None
    draw.text((112, BAR // 2 - 11), url[:110], fill="#3a3a3a", font=font)
    out.paste(shot, (0, BAR))
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def take(url: str, out: Path) -> tuple[str, str]:
    """Screenshot `url` into `out` (PNG). Returns (path or '', error or '')."""
    if "://" not in url:
        url = "https://" + url
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return "", f"no browser available ({type(exc).__name__})"
    try:
        with sync_playwright() as p:
            browser = _launch(p)
            try:
                page = browser.new_page(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}, user_agent=UA)
                try:
                    page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
                except Exception:
                    if not url.startswith("https://"):
                        raise
                    page.goto("http://" + url[len("https://"):], timeout=TIMEOUT_MS, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass  # busy pages never go idle; the content is there
                png = page.screenshot()
                shown = page.url
            finally:
                browser.close()
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_with_bar(png, shown))
        return str(out), ""
    except Exception as exc:
        return "", f"{type(exc).__name__}: {str(exc).splitlines()[0][:160] if str(exc) else ''}"
