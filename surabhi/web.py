"""Fetch a suspicious website's page and the images it shows (logo, favicon, og:image, <img>).

Plain HTTP (requests) and the standard-library HTML parser, so it works without a browser. Every failure
(DNS, timeout, TLS, HTTP error, oversized or non-image responses) is recorded and never raised.
"""
from __future__ import annotations

import base64
import binascii
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 FORGE-brand-protection"
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 40
TIMEOUT = 10


class _ImageCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []  # (src, role)
        self.title, self._in_title, self.site_name = "", False, ""

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or (a.get("srcset") or "").split(",")[0].strip().split(" ")[0]
            hint = " ".join((a.get("alt", ""), a.get("class", ""), a.get("id", ""), src)).lower()
            if src:
                self.found.append((src, "logo" if "logo" in hint else "image"))
        elif tag == "link" and any(r in a.get("rel", "").lower() for r in ("icon", "apple-touch-icon")):
            if a.get("href"):
                self.found.append((a["href"], "favicon"))
        elif tag == "meta" and a.get("property", a.get("name", "")).lower() in ("og:image", "twitter:image"):
            if a.get("content"):
                self.found.append((a["content"], "og_image"))
        elif tag == "meta" and a.get("property", a.get("name", "")).lower() == "og:site_name":
            self.site_name = self.site_name or a.get("content", "").strip()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _get(session: requests.Session, url: str, limit: int) -> tuple[bytes | None, requests.Response | None, str]:
    try:
        with session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True) as r:
            if r.status_code >= 400:
                return None, r, f"HTTP {r.status_code}"
            declared = r.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > limit:
                return None, r, f"response larger than {limit // 1024} KB"
            buf = bytearray()
            for chunk in r.iter_content(64 * 1024):
                buf += chunk
                if len(buf) > limit:
                    return None, r, f"response larger than {limit // 1024} KB"
            return bytes(buf), r, ""
    except requests.exceptions.SSLError:
        return None, None, "TLS / certificate error"
    except requests.exceptions.ConnectionError as e:
        msg = str(e)
        if "Name or service not known" in msg or "NameResolutionError" in msg or "nodename nor servname" in msg:
            return None, None, "domain does not resolve"
        return None, None, "connection failed"
    except requests.exceptions.Timeout:
        return None, None, f"timed out after {TIMEOUT}s"
    except requests.exceptions.RequestException as e:
        return None, None, f"request failed ({type(e).__name__})"


def _decode(html: bytes, resp) -> str:
    """Charset from the Content-Type header if the server sent one, else <meta charset>, else UTF-8.
    (requests assumes ISO-8859-1 for text/html without a charset, which garbles UTF-8 pages.)"""
    ctype = (resp.headers.get("Content-Type", "") if resp is not None else "").lower()
    enc = resp.encoding if resp is not None and "charset=" in ctype else None
    if not enc:
        m = re.search(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w-]+)""", html[:4096], re.I)
        enc = m.group(1).decode("ascii") if m else "utf-8"
    try:
        return html.decode(enc, errors="replace")
    except LookupError:  # unknown charset name
        return html.decode("utf-8", errors="replace")


def _data_uri(src: str) -> bytes | None:
    head, _, body = src.partition(",")
    if ";base64" not in head:
        return None
    try:
        return base64.b64decode(body, validate=False)
    except (binascii.Error, ValueError):
        return None


def fetch_site(url: str, max_images: int = MAX_IMAGES) -> dict:
    """Returns {url, final_url, ok, status_code, title, error, images: [{name, source, role, data}],
    image_errors: [{source, error}]}."""
    if "://" not in url:
        url = "https://" + url
    out = {"url": url, "final_url": url, "ok": False, "status_code": None, "title": "", "error": "",
           "images": [], "image_errors": []}
    if urlparse(url).scheme not in ("http", "https"):
        out["error"] = "only http(s) URLs can be fetched"
        return out
    with requests.Session() as s:
        s.headers.update({"User-Agent": UA, "Accept-Language": "en"})
        html, resp, err = _get(s, url, MAX_PAGE_BYTES)
        if resp is not None:
            out["status_code"], out["final_url"] = resp.status_code, resp.url
        if html is None and url.startswith("https://"):  # many small fake shops are http only
            html2, resp2, err2 = _get(s, "http://" + url[len("https://"):], MAX_PAGE_BYTES)
            if html2 is not None:
                html, resp, err = html2, resp2, ""
                out["status_code"], out["final_url"] = resp.status_code, resp.url
        if html is None:
            out["error"] = err
            return out
        out["ok"] = True
        parser = _ImageCollector()
        try:
            parser.feed(_decode(html, resp))
        except Exception as e:  # malformed markup: keep what was collected
            out["image_errors"].append({"source": out["final_url"], "error": f"HTML parse error ({type(e).__name__})"})
        out["title"] = " ".join(parser.title.split())[:200]
        if not any(role == "favicon" for _, role in parser.found):
            parser.found.append(("/favicon.ico", "favicon"))
        seen = set()
        for src, role in parser.found:
            if len(out["images"]) >= max_images:
                break
            src = src.strip()
            if src.startswith("data:"):
                data, source = _data_uri(src), f"data URI ({role})"
                if data:
                    out["images"].append({"name": f"{role}-inline-{len(out['images']) + 1}", "source": source,
                                          "role": role, "data": data})
                continue
            full = urljoin(out["final_url"], src)
            if full in seen or urlparse(full).scheme not in ("http", "https"):
                continue
            seen.add(full)
            data, _, err = _get(s, full, MAX_IMAGE_BYTES)
            if data is None:
                if role != "favicon" or src != "/favicon.ico":  # a missing default favicon is not worth reporting
                    out["image_errors"].append({"source": full, "error": err})
                continue
            name = urlparse(full).path.rsplit("/", 1)[-1] or role
            out["images"].append({"name": name, "source": full, "role": role, "data": data})
    return out


def page_names(url: str) -> tuple[str, str]:
    """(<title>, og:site_name) of a page; ('', '') if it cannot be loaded."""
    with requests.Session() as s:
        s.headers.update({"User-Agent": UA})
        html, resp, _ = _get(s, url if "://" in url else "https://" + url, MAX_PAGE_BYTES)
    if html is None:
        return "", ""
    parser = _ImageCollector()
    try:
        parser.feed(_decode(html, resp))
    except Exception:
        pass
    return " ".join(parser.title.split()), parser.site_name
