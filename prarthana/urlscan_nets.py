"""#7 urlscan nets — find lookalike stores in urlscan.io's public scan index.

Three nets (free-plan Search API fields only):
  hash:<sha256>         pages that loaded a byte-identical copy of a brand photo / logo  -> found_by=urlscan_hash
  domain:<brand domain> pages that pulled resources from the brand's domain (hotlinkers) -> found_by=urlscan_hotlink
  page.title:"<brand>"  pages whose <title> contains the brand name                     -> found_by=urlscan_title
"""
from __future__ import annotations

import time

import requests

from schemas import BrandDNA, Suspect
from prarthana.common import env, cache_get, cache_set, domain_of, registrable, log
from prarthana.scorer import is_allowed

SEARCH = "https://urlscan.io/api/v1/search/"
RETRY_WAIT = 20  # seconds to wait after HTTP 429 before the single retry


def search(q: str, state_log=None, size: int = 100) -> list:
    """Run one urlscan search (cached in cache/urlscan/). Returns the results list ([] on any failure)."""
    cached = cache_get("urlscan", q)
    if cached is not None:
        return cached
    key = env("URLSCAN_API_KEY")
    if not key:
        log(state_log, "urlscan: URLSCAN_API_KEY missing, skipping urlscan nets")
        return []
    for attempt in (1, 2):
        try:
            r = requests.get(SEARCH, params={"q": q, "size": size}, headers={"API-Key": key}, timeout=30)
        except Exception as e:
            log(state_log, f"urlscan: request failed for {q!r}: {type(e).__name__}")
            return []
        if r.status_code == 429 and attempt == 1:
            wait = int(r.headers.get("Retry-After", RETRY_WAIT) or RETRY_WAIT)
            log(state_log, f"urlscan: 429 rate-limited, retrying once in {min(wait, 60)}s")
            time.sleep(min(wait, 60))
            continue
        if r.status_code != 200:
            log(state_log, f"urlscan: HTTP {r.status_code} for {q!r}")
            return []
        results = r.json().get("results", [])
        cache_set("urlscan", q, results)
        return results
    return []


def build_queries(dna: BrandDNA, extra_hashes: list | None = None) -> list:
    """[(query, found_by)] for this brand. extra_hashes = campaign image hashes from the planner's re-hunt."""
    qs = []
    hashes = list(dict.fromkeys([*(dna.photo_sha256s or []), dna.logo_sha256, *(extra_hashes or [])]))
    qs += [(f"hash:{h}", "urlscan_hash") for h in hashes if h]
    store_domain = registrable(domain_of(dna.store_url))
    if store_domain and "." in store_domain and store_domain != "localhost":
        qs.append((f"domain:{store_domain}", "urlscan_hotlink"))
    if dna.brand_name:
        name = dna.brand_name.replace('"', "")
        qs.append((f'page.title:"{name}"', "urlscan_title"))
    return qs


def urlscan_nets(dna: BrandDNA, seen_domains: set, state_log=None, extra_hashes: list | None = None,
                 only_hashes: bool = False) -> list:
    """Return NEW Suspects (domain not in seen_domains, not the brand / allow-list). Updates seen_domains."""
    out = []
    queries = build_queries(dna, extra_hashes)
    if only_hashes:
        queries = [(f"hash:{h}", "urlscan_hash") for h in extra_hashes or []]
    for q, found_by in queries:
        for res in search(q, state_log):
            page = res.get("page") or {}
            url = page.get("url") or (res.get("task") or {}).get("url")
            if not url:
                continue
            dom = domain_of(url)
            if not dom or dom in seen_domains or is_allowed(dom, dna):
                continue
            seen_domains.add(dom)
            s = Suspect(url=url, domain=dom, found_by=found_by)
            s.ip = page.get("ip")
            out.append(s)
    log(state_log, f"urlscan: {len(queries)} queries -> {len(out)} new suspects")
    return out
