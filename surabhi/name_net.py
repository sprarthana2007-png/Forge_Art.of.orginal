"""NameNet - brand-name and domain lookalike detection.

For a registered brand (name + official domain) NameNet:
  * normalises brand names and domains (case, www, ports, punycode / IDN, accents),
  * detects how a candidate differs from the brand: character insertion, deletion (omission), repetition,
    substitution (lookalike or neighbouring key), transposition, lookalike combinations (rn->m, vv->w, cl->d,
    0->o, Cyrillic/Greek homoglyphs), hyphenation, added keywords (northstar-sale), a different TLD, or the
    brand placed in a subdomain,
  * scores name similarity 0-100 (Damerau-Levenshtein + Jaro-Winkler on the raw and on the "lookalike
    skeleton" of each name, plus containment),
  * optionally checks DNS and registration age, and classifies the result.

Name similarity is NOT evidence of malicious behaviour. NameNet never labels a domain malicious; at most
"suspicious" (a lookalike that is live and recently registered). Evidence of impersonation comes from content
(Brand DNA image matches) in surabhi.protection.

`name_net(dna)` is the FORGE planner entry point: it generates typo / lookalike variants of the official
domain, keeps those that exist in DNS, and returns them as schemas.Suspect objects.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable, Iterable
from urllib.parse import urlparse

from schemas import BrandDNA, Suspect

# ------------------------------------------------------------------ constants
MULTI_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "co.in", "net.in", "org.in", "firm.in", "gen.in", "ind.in",
    "com.au", "net.au", "org.au", "co.nz", "com.br", "com.cn", "com.sg", "com.my", "co.jp", "co.za", "com.mx",
    "com.tr", "co.id", "com.ph", "com.pk", "com.bd", "com.ng", "com.ar", "com.hk", "com.tw", "co.kr",
}
KEYWORDS = (
    "official", "shop", "store", "online", "sale", "sales", "outlet", "deals", "deal", "offer", "offers",
    "discount", "buy", "mart", "india", "login", "signin", "secure", "verify", "account", "support", "help",
    "care", "pay", "payment", "app", "club", "rewards", "reward", "gift", "gifts", "free", "promo", "partner",
    "partners", "get", "my", "the", "best", "new", "web", "site", "hq", "global", "world", "us", "uk", "in",
)
GEN_KEYWORDS = ("official", "shop", "store", "sale", "outlet", "online", "india", "deals", "offer", "login")
GEN_TLDS = ("com", "in", "co.in", "net", "org", "co", "shop", "store", "online", "site", "xyz", "top", "info")

# Unicode characters that render like ASCII letters (Cyrillic / Greek / Latin extensions).
HOMOGLYPHS = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "һ": "h", "ӏ": "l", "к": "k", "м": "m", "н": "h", "т": "t", "в": "b", "г": "r", "ԛ": "q", "ԝ": "w",
    "ο": "o", "α": "a", "ν": "v", "ρ": "p", "τ": "t", "ι": "i", "κ": "k", "ε": "e", "υ": "u", "χ": "x",
    "ı": "i", "ł": "l", "ø": "o", "ɡ": "g", "ɑ": "a", "ʟ": "l", "ꞵ": "b",
}
DIGIT_LOOKALIKES = {"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b"}
COMBO_LOOKALIKES = (("rn", "m"), ("vv", "w"), ("cl", "d"))
LETTER_LOOKALIKES = {"i": "l"}  # i / l / 1 collapse to one shape
GEN_LOOKALIKES = {"o": ["0"], "l": ["1", "i"], "i": ["1", "l"], "e": ["3"], "a": ["4"], "s": ["5"],
                  "m": ["rn"], "w": ["vv"], "d": ["cl"]}

_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
_KEYPOS = {ch: (r, c + (0, 0.25, 0.75)[r]) for r, row in enumerate(_ROWS) for c, ch in enumerate(row)}
KEY_NEIGHBOURS = {a: {b for b, (r2, x2) in _KEYPOS.items() if b != a and abs(r - r2) <= 1 and abs(x - x2) <= 1}
                  for a, (r, x) in _KEYPOS.items()}

EVIDENCE_NOTE = ("Name similarity alone is not evidence of malicious behaviour. Evidence of impersonation "
                 "needs content checks such as copied logos or product images.")
RECENT_DAYS = 180
_LABEL_RE = re.compile(r"^(?!-)[\w-]{1,63}(?<!-)$", re.UNICODE)


# ------------------------------------------------------------------ normalisation
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_brand(name: str) -> str:
    """'North Star Outdoors®' -> 'northstaroutdoors' (lower case, accents and non-alphanumerics removed)."""
    return re.sub(r"[^a-z0-9]", "", _strip_accents(name or "").lower())


def normalize_domain(value: str) -> str:
    """URL or host -> lower-case Unicode host without scheme, port, path, trailing dot or leading www.
    Punycode labels (xn--) are decoded so homoglyphs are visible. Raises ValueError if it is not a host."""
    s = (value or "").strip()
    if not s:
        raise ValueError("A domain or URL is required.")
    if "://" not in s:
        s = "http://" + s
    try:
        host = urlparse(s).hostname
    except ValueError as e:
        raise ValueError(f"Not a valid URL or domain: {value!r}") from e
    host = (host or "").strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        raise ValueError(f"Not a valid URL or domain: {value!r}")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = []
    for lab in host.split("."):
        if lab.startswith("xn--"):
            try:
                lab = lab.encode("ascii").decode("idna")
            except UnicodeError:
                pass
        if not _LABEL_RE.match(lab) or "_" in lab:
            raise ValueError(f"Not a valid URL or domain: {value!r}")
        labels.append(lab)
    return ".".join(labels)


def to_ascii(domain: str) -> str:
    """IDNA (punycode) form for DNS lookups."""
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return domain


def split_domain(domain: str) -> tuple[str, str, str]:
    """'shop.northstar.co.in' -> ('shop', 'northstar', 'co.in')."""
    parts = domain.split(".")
    if len(parts) == 1:
        return "", parts[0], ""
    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_SUFFIXES:
        return ".".join(parts[:-3]), parts[-3], ".".join(parts[-2:])
    return ".".join(parts[:-2]), parts[-2], parts[-1]


def is_local(domain: str) -> bool:
    """Addresses NameNet cannot meaningfully permute (localhost, IPs, reserved test TLDs)."""
    d = domain.split(":")[0]
    try:
        ipaddress.ip_address(d)
        return True
    except ValueError:
        pass
    return "." not in d or d == "localhost" or d.endswith((".localhost", ".local", ".test", ".invalid", ".example"))


def skeleton(s: str) -> str:
    """What a name looks like: homoglyphs, lookalike digits and letter combos collapsed to one form."""
    s = "".join(HOMOGLYPHS.get(c, c) for c in s.lower())
    s = _strip_accents(s).replace("-", "").replace(".", "")
    for combo, rep in COMBO_LOOKALIKES:
        s = s.replace(combo, rep)
    return "".join(LETTER_LOOKALIKES.get(c, DIGIT_LOOKALIKES.get(c, c)) for c in s)


# ------------------------------------------------------------------ string similarity
def edit_ops(a: str, b: str) -> tuple[int, list[tuple]]:
    """Optimal-string-alignment (Damerau-Levenshtein) distance from a to b, plus the operations:
    ('insertion', pos, '', ch) / ('omission', pos, ch, '') / ('substitution', pos, x, y) /
    ('transposition', pos, 'xy', 'yx'). Positions refer to a."""
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j

    def swapped(i, j):
        return i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1] and a[i - 1] != b[j - 1]

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if swapped(i, j):
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    ops, i, j = [], n, m
    while i > 0 or j > 0:
        if swapped(i, j) and d[i][j] == d[i - 2][j - 2] + 1:
            ops.append(("transposition", i - 2, a[i - 2:i], b[j - 2:j]))
            i, j = i - 2, j - 2
        elif i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            if a[i - 1] != b[j - 1]:
                ops.append(("substitution", i - 1, a[i - 1], b[j - 1]))
            i, j = i - 1, j - 1
        elif j > 0 and d[i][j] == d[i][j - 1] + 1:
            ops.append(("insertion", i, "", b[j - 1]))
            j -= 1
        else:
            ops.append(("omission", i - 1, a[i - 1], ""))
            i -= 1
    return d[n][m], ops[::-1]


def jaro_winkler(a: str, b: str, p: float = 0.1) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(max(len(a), len(b)) // 2 - 1, 0)
    am, bm = [False] * len(a), [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not bm[j] and b[j] == ch:
                am[i] = bm[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    bs = [b[j] for j in range(len(b)) if bm[j]]
    transpositions = sum(ch != bs[k] for k, ch in enumerate(c for i, c in enumerate(a) if am[i])) / 2
    jaro = (matches / len(a) + matches / len(b) + (matches - transpositions) / matches) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * p * (1 - jaro)


def _blend(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    dist, _ = edit_ops(a, b)
    return 0.5 * (1 - dist / max(len(a), len(b))) + 0.5 * jaro_winkler(a, b)


def _containment(target: str, text: str) -> float:
    if len(target) < 4 or target not in text or target == text:
        return 0.0
    return 0.70 + 0.25 * len(target) / len(text)


def name_similarity(candidate: str, target: str) -> int:
    """0-100 similarity between a domain label (or name) and a brand name."""
    flat, t = candidate.replace("-", "").lower(), target.lower()
    sk_c, sk_t = skeleton(candidate), skeleton(target)
    score = max(_blend(flat, t), _blend(sk_c, sk_t), _containment(t, flat), _containment(sk_t, sk_c))
    rest, kw = _strip_keyword(candidate.lower())
    if kw:  # 'nothstar-sale': judge the part without the keyword, slightly discounted
        score = max(score, 0.95 * _blend(rest, t))
    return round(100 * score)


# ------------------------------------------------------------------ technique detection
def _describe_op(op, target: str) -> dict:
    kind, pos, x, y = op
    if kind == "insertion":
        neighbours = {target[pos - 1] if pos > 0 else "", target[pos] if pos < len(target) else ""}
        if y in neighbours:
            return {"type": "repetition", "detail": f"character repetition: '{y}' is doubled"}
        return {"type": "insertion", "detail": f"character insertion: extra '{y}' added"}
    if kind == "omission":
        return {"type": "deletion", "detail": f"character deletion: '{x}' is missing"}
    if kind == "transposition":
        return {"type": "transposition", "detail": f"characters swapped: '{x}' became '{y}'"}
    if skeleton(x) == skeleton(y):
        return {"type": "substitution", "detail": f"character substitution: '{x}' replaced by lookalike '{y}'"}
    if y in KEY_NEIGHBOURS.get(x, ()):
        return {"type": "substitution", "detail": f"character substitution: '{x}' replaced by neighbouring key '{y}'"}
    return {"type": "substitution", "detail": f"character substitution: '{x}' replaced by '{y}'"}


def _lookalike_details(label: str, target: str) -> list[str]:
    out = []
    for c in dict.fromkeys(label):
        if ord(c) > 127:
            look = HOMOGLYPHS.get(c) or _strip_accents(c) or "?"
            try:
                cname = unicodedata.name(c).title()
            except ValueError:
                cname = "non-Latin character"
            out.append(f"'{c}' ({cname}, U+{ord(c):04X}) looks like '{look}'")
    flat = label.replace("-", "")
    for combo, rep in COMBO_LOOKALIKES:
        if flat.count(combo) > target.count(combo):
            out.append(f"'{combo}' looks like '{rep}'")
        elif target.count(combo) > flat.count(combo):
            out.append(f"'{rep}' looks like '{combo}'")
    for a, b in (("i", "l"), ("l", "i")):
        if flat.count(a) > target.count(a) and target.count(b) > flat.count(b):
            out.append(f"'{a}' looks like '{b}'")
    for dgt, letter in DIGIT_LOOKALIKES.items():
        if dgt in flat and dgt not in target:
            out.append(f"'{dgt}' looks like '{letter}'")
    if not out:
        out.append("uses letters that look alike (such as i / l)")
    return out


def _keyword_technique(extras: list[str]) -> dict:
    known = [e for e in extras if e in KEYWORDS]
    if known and len(known) == len(extras):
        return {"type": "keyword_added", "detail": f"brand name with added keyword(s): {', '.join(known)}"}
    return {"type": "brand_embedded", "detail": f"brand name embedded with extra text: {', '.join(extras)}"}


def _typo_ops(flat: str, target: str) -> list[dict]:
    """Insertion / deletion / repetition / substitution / transposition, if the edit distance is small."""
    if len(flat) < 3:
        return []
    dist, ops = edit_ops(target, flat)
    limit = 1 if len(target) <= 5 else 2 if len(target) <= 12 else 3
    return [_describe_op(op, target) for op in ops] if 0 < dist <= limit else []


def _strip_keyword(label: str) -> tuple[str, str]:
    """'nothstar-sale' -> ('nothstar', 'sale'); ('', '') when no known keyword is attached."""
    tokens = [t for t in label.split("-") if t]
    if len(tokens) > 1:
        kws = [t for t in tokens if t in KEYWORDS]
        if kws:
            return "".join(t for t in tokens if t not in KEYWORDS), kws[0]
    flat = label.replace("-", "")
    for kw in sorted((k for k in KEYWORDS if len(k) >= 3), key=len, reverse=True):
        if flat.endswith(kw) and len(flat) - len(kw) >= 3:
            return flat[:-len(kw)], kw
        if flat.startswith(kw) and len(flat) - len(kw) >= 3:
            return flat[len(kw):], kw
    return "", ""


def _techniques_for(label: str, suffix: str, sub: str, target: str, official_suffix: str) -> list[dict]:
    flat = label.replace("-", "")
    idn = any(ord(c) > 127 for c in label)
    tech = []
    if flat == target:
        if "-" in label:
            tech.append({"type": "hyphenation", "detail": f"hyphen inserted into the brand name ('{label}')"})
        if suffix != official_suffix:
            tech.append({"type": "tld_swap", "detail": f"same name on .{suffix or '?'} instead of .{official_suffix}"})
    elif target in flat and len(target) >= 4 and len(flat) - len(target) > 2:
        i = flat.index(target)
        tokens = [t for t in label.split("-") if t]
        extras = [t for t in tokens if t != target] if target in tokens \
            else [x for x in (flat[:i], flat[i + len(target):]) if x]
        tech.append(_keyword_technique(extras))
        if "-" in label and target not in tokens:
            tech.append({"type": "hyphenation", "detail": f"hyphenated variation ('{label}')"})
    else:
        sk_l, sk_t = skeleton(label), skeleton(target)
        if target not in flat and (sk_l == sk_t or (len(sk_t) >= 4 and sk_t in sk_l)):
            kind = "homoglyph" if idn else "lookalike_characters"
            for d in _lookalike_details(label, target):
                tech.append({"type": kind, "detail": f"lookalike characters: {d}"})
            if sk_l != sk_t:
                i = sk_l.index(sk_t)
                tech.append(_keyword_technique([x for x in (sk_l[:i], sk_l[i + len(sk_t):]) if x]))
        elif len(flat) >= 3:
            tech += _typo_ops(flat, target)
            if not tech:  # typo combined with a keyword: 'nothstar-sale'
                rest, kw = _strip_keyword(label)
                typo = _typo_ops(rest, target) if kw else []
                if typo:
                    tech += [_keyword_technique([kw])] + typo
    sub_labels = re.split(r"[.\-]", sub) if sub else []
    if target in sub_labels or any(skeleton(s) == skeleton(target) for s in sub_labels if s):
        tech.append({"type": "subdomain_brand", "detail": f"brand name used as a subdomain ('{sub}') of another domain"})
    if idn and not any(t["type"] == "homoglyph" for t in tech) and tech:
        tech.append({"type": "homoglyph", "detail": "internationalised (non-Latin) characters in the name"})
    return tech


# ------------------------------------------------------------------ network evidence (optional)
_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="namenet-dns")


def resolve_dns(domain: str, timeout: float = 4.0) -> dict:
    """A/AAAA lookup with a timeout. status: resolves | no_such_domain | error."""
    host = to_ascii(domain.split(":")[0])
    fut = _POOL.submit(socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM)
    try:
        infos = fut.result(timeout=timeout)
    except FutureTimeout:
        return {"status": "error", "addresses": [], "error": f"DNS lookup timed out after {timeout:g}s"}
    except socket.gaierror as e:
        nx = {getattr(socket, n) for n in ("EAI_NONAME", "EAI_NODATA") if hasattr(socket, n)}
        if e.errno in nx:
            return {"status": "no_such_domain", "addresses": [], "error": ""}
        return {"status": "error", "addresses": [], "error": f"DNS lookup failed: {e.strerror or e}"}
    except (OSError, UnicodeError) as e:
        return {"status": "error", "addresses": [], "error": f"DNS lookup failed: {e}"}
    return {"status": "resolves", "addresses": sorted({i[4][0] for i in infos}), "error": ""}


def lookup_domain_age(domain: str) -> int | None:
    """Days since registration via RDAP / WHOIS (the Investigator's helper, reused). None if unavailable."""
    try:
        from prarthana.investigator import domain_age_days
        return domain_age_days(domain)
    except Exception:
        return None


# ------------------------------------------------------------------ analysis
def analyse_domain(candidate: str, official_domain: str, brand_name: str = "", allow_list: Iterable[str] = (),
                   check_network: bool = False, resolver: Callable[[str], dict] | None = None,
                   age_lookup: Callable[[str], int | None] | None = None,
                   official_dns: dict | None = None) -> dict:
    """Compare one candidate domain with a brand. Never raises for network problems; a malformed candidate
    raises ValueError."""
    domain = normalize_domain(candidate)
    official = normalize_domain(official_domain)
    allowed = {official} | {normalize_domain(a) for a in allow_list if a}
    base = {"domain": domain, "official_domain": official, "brand_name": brand_name, "techniques": [],
            "matched_name": "", "dns": {"status": "skipped", "addresses": [], "error": ""},
            "domain_age_days": None, "signals": [], "malicious_evidence": False, "evidence_note": EVIDENCE_NOTE}
    if any(domain == a or domain.endswith("." + a) for a in allowed):
        return {**base, "similarity": 100, "name_level": "identical", "classification": "official",
                "reasons": ["This is the official domain (or one of its subdomains / allow-listed domains)."]}

    sub, label, suffix = split_domain(domain)
    _, olabel, osuffix = split_domain(official)
    targets = list(dict.fromkeys(t for t in (olabel.replace("-", ""), normalize_brand(brand_name)) if len(t) >= 3))
    best = None
    for t in targets:
        tech = _techniques_for(label, suffix, sub, t, osuffix)
        score = name_similarity(label, t)
        if any(x["type"] == "subdomain_brand" for x in tech):
            score = max(score, 80)
        if best is None or (score, len(tech)) > (best[0], len(best[1])):
            best = (score, tech, t)
    score, techniques, matched = best or (0, [], "")
    related = bool(techniques) or score >= 75
    level = "high" if score >= 85 else "medium" if score >= 70 else "low"
    out = {**base, "similarity": score, "name_level": level, "techniques": techniques, "matched_name": matched}
    reasons = [f"Name similarity {score}/100 to '{matched}' ({level})."] if matched else []
    reasons += [t["detail"][0].upper() + t["detail"][1:] + "." for t in techniques]

    if not related:
        out.update(classification="unrelated", reasons=reasons + ["No lookalike technique detected."])
        return out

    signals = []
    if check_network:
        resolver = resolver or resolve_dns
        dns = resolver(domain)
        out["dns"] = dns
        official_dns = official_dns if official_dns is not None else resolver(official)
        if dns["status"] == "resolves":
            reasons.append(f"Domain is live in DNS ({', '.join(dns['addresses'][:3])}).")
            signals.append("resolves")
            if official_dns.get("status") == "resolves" and set(dns["addresses"]) & set(official_dns["addresses"]):
                signals.append("shares_official_ip")
                reasons.append("It shares an IP address with the official site, so it may be owned by the brand.")
            age = (age_lookup or lookup_domain_age)(domain)
            out["domain_age_days"] = age
            if age is None:
                reasons.append("Registration date unavailable (RDAP / WHOIS gave no answer).")
            else:
                reasons.append(f"Registered about {age} days ago.")
                if age < RECENT_DAYS:
                    signals.append("recently_registered")
        elif dns["status"] == "no_such_domain":
            if official_dns.get("status") == "resolves":
                reasons.append("Domain does not exist in DNS (not registered or not configured).")
                signals.append("not_registered")
            else:  # the official domain did not resolve either: DNS answers here cannot be trusted
                out["dns"] = {**dns, "status": "error", "error": "DNS unavailable (official domain did not resolve)"}
                reasons.append("DNS could not be verified (the official domain did not resolve either).")
        else:
            reasons.append(f"DNS could not be verified: {dns.get('error') or 'unknown error'}.")
    if any(t["type"] == "homoglyph" for t in techniques):
        signals.append("idn_homograph")

    if "not_registered" in signals:
        cls = "not_registered"
    elif "shares_official_ip" in signals:
        cls = "likely_brand_owned"
    elif "resolves" in signals and ("recently_registered" in signals or "idn_homograph" in signals):
        cls = "suspicious"
    else:
        cls = "lookalike"
    reasons.append(EVIDENCE_NOTE)
    out.update(signals=signals, classification=cls, reasons=reasons)
    return out


def check_domains(brand: dict, candidates: Iterable[str], check_network: bool = True, save: bool = True,
                  resolver=None, age_lookup=None, conn=None) -> list[dict]:
    """Analyse user-supplied candidate domains against a registered brand (store.get_brand dict).
    Malformed candidates come back with classification 'invalid'."""
    results, official_dns = [], None
    if check_network:
        official_dns = (resolver or resolve_dns)(brand["official_domain"])
    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue
        try:
            f = analyse_domain(c, brand["official_domain"], brand["name"], check_network=check_network,
                               resolver=resolver, age_lookup=age_lookup, official_dns=official_dns)
        except ValueError as e:
            results.append({"domain": c, "classification": "invalid", "similarity": 0, "reasons": [str(e)],
                            "techniques": [], "malicious_evidence": False})
            continue
        results.append(f)
    if save:
        _save_findings(brand["id"], [f for f in results if f["classification"] not in ("invalid", "official")], conn)
    return results


def _save_findings(brand_id: int, findings: list[dict], conn=None) -> None:
    from surabhi import store
    own = conn is None
    conn = conn or store.connect()
    try:
        with conn:
            for f in findings:
                store.save_finding(conn, brand_id, f)
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------------ candidate generation
def _valid_label(s: str) -> bool:
    return bool(s) and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", s) is not None and len(s) <= 63


def generate_variants(official_domain: str, keywords: Iterable[str] = GEN_KEYWORDS,
                      tlds: Iterable[str] = GEN_TLDS, limit: int | None = None) -> list[str]:
    """Typo / lookalike / keyword / TLD variants of the official domain, most likely abuse first."""
    official = normalize_domain(official_domain)
    _, label, suffix = split_domain(official)
    suffix = suffix or "com"
    labels: list[str] = []
    add = labels.append
    for i in range(1, len(label)):
        add(label[:i] + "-" + label[i:])                                     # hyphenation
    for kw in keywords:                                                      # keyword added
        add(f"{label}-{kw}"), add(f"{label}{kw}"), add(f"{kw}-{label}"), add(f"{kw}{label}")
    for i, ch in enumerate(label):                                           # lookalike characters
        for rep in GEN_LOOKALIKES.get(ch, ()):
            add(label[:i] + rep + label[i + 1:])
    for i in range(len(label)):
        add(label[:i] + label[i] + label[i:])                                # repetition
        add(label[:i] + label[i + 1:])                                       # deletion
        if i + 1 < len(label):
            add(label[:i] + label[i + 1] + label[i] + label[i + 2:])         # transposition
    for i, ch in enumerate(label):
        for nb in sorted(KEY_NEIGHBOURS.get(ch, ())):
            add(label[:i] + nb + label[i + 1:])                              # neighbouring-key substitution
            add(label[:i] + nb + label[i:])                                  # neighbouring-key insertion
    out = [f"{label}.{t}" for t in tlds if t != suffix]                      # same name, other TLD
    out += [f"{lab}.{suffix}" for lab in labels if lab != label and _valid_label(lab)]
    out = [d for d in dict.fromkeys(out) if d != official]
    return out[:limit] if limit else out


def scan_variants(official_domain: str, brand_name: str = "", limit: int = 300, resolver=None,
                  age_lookup=None) -> dict:
    """Generate variants, keep those live in DNS and analyse them. Returns {'findings', 'checked', 'dns'}.
    If the official domain itself does not resolve, DNS is treated as unavailable and nothing is claimed."""
    resolver = resolver or resolve_dns
    official = normalize_domain(official_domain)
    official_dns = resolver(official)
    if official_dns["status"] != "resolves":
        return {"findings": [], "checked": 0, "dns": "unavailable",
                "error": official_dns.get("error") or "the official domain did not resolve"}
    variants = generate_variants(official, limit=limit)
    pool = ThreadPoolExecutor(max_workers=16)
    try:
        answers = dict(zip(variants, pool.map(resolver, variants)))
    finally:
        pool.shutdown(wait=False)
    findings = []
    for d, dns in answers.items():
        if dns["status"] != "resolves":
            continue
        f = analyse_domain(d, official, brand_name, check_network=True, resolver=lambda _d, a=dns: a,
                           age_lookup=age_lookup, official_dns=official_dns)
        if f["classification"] not in ("official", "unrelated"):
            findings.append(f)
    findings.sort(key=lambda f: (f["classification"] != "suspicious", -f["similarity"]))
    return {"findings": findings, "checked": len(variants), "dns": "ok", "error": ""}


# ------------------------------------------------------------------ FORGE planner entry point
def name_net(dna: BrandDNA, limit: int = 300, resolver=None, age_lookup=None) -> list:
    """Planner hook: live lookalike domains of the store as Suspects (found_by='name_net').
    Domains that probably belong to the brand (same IP as the official site) are left out."""
    try:
        official = normalize_domain(dna.store_url)
    except ValueError:
        return []
    if is_local(official):
        return []
    scan = scan_variants(official, dna.brand_name, limit=limit, resolver=resolver, age_lookup=age_lookup)
    suspects = []
    for f in scan["findings"]:
        if f["classification"] == "likely_brand_owned" or f["domain"] in (dna.allow_list or []):
            continue
        suspects.append(Suspect(url=f"https://{to_ascii(f['domain'])}/", domain=to_ascii(f["domain"]),
                                found_by="name_net", domain_age_days=f["domain_age_days"],
                                ip=(f["dns"]["addresses"] or [None])[0], reasons=list(f["reasons"])))
    try:  # keep the findings for the dashboard when this brand is registered
        from surabhi.brand_dna import registered_brand_for
        brand = registered_brand_for(dna.store_url)
        if brand:
            _save_findings(brand["id"], scan["findings"])
    except Exception:
        pass
    return suspects
