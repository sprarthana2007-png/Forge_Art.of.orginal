# Brand DNA + NameNet (Surabhi)

Backend for brand protection. The FORGE planner picks these up automatically through
`prarthana/teammate.py` (`surabhi.brand_dna.build_brand_dna`, `surabhi.name_net.name_net`), and the owner
dashboard shows them on the **Brand protection** page (`forge-starter/surabhi/views/brand_protection.py`).

| File | What it does |
| --- | --- |
| `brand_dna.py` | Registers a brand (name, official URL, logo, reference images). Validates each image and fingerprints it with the shared `schemas.file_sha256` / `photo_phash`. Compares new images with every fingerprint: exact = same SHA-256, similar = `schemas.is_similar`. |
| `name_net.py` | Normalises names and domains (punycode, accents, www, ports). Detects insertion, deletion, repetition, substitution, transposition, lookalikes (`rn`→`m`, `0`→`o`, Cyrillic homoglyphs), hyphenation, added keywords, TLD swaps and brands used as subdomains. Scores similarity 0–100 (Damerau-Levenshtein + Jaro-Winkler on raw and lookalike forms). Optionally checks DNS and registration age, then classifies the domain. |
| `protection.py` | One workflow: NameNet + website download + Brand DNA comparison, combined into a single risk report. |
| `web.py` | Downloads a page and its logo, favicon, og:image and `<img>` images, with size, count and time limits. |
| `store.py` | SQLite tables (`bp_*`) in the dashboard's database file. Set `FORGE_BRAND_DB` to use another file. |

**Name similarity is never treated as evidence of malice.** Without copied brand images, the risk is capped at
49 (medium). NameNet's own classes are `lookalike`, `suspicious` (live and recently registered),
`not_registered`, `likely_brand_owned` (shares the official IP), `unrelated` and `official`. "High risk" needs
content evidence: an exact or visually similar copy of a registered logo or image on a non-official domain.
If DNS is unavailable (even the official domain does not resolve), NameNet reports "could not be verified"
rather than claiming that a lookalike is unregistered.

```bash
python -m surabhi register --name Northstar --url https://northstar.com --logo logo.png --ref photo1.jpg
python -m surabhi brands
python -m surabhi compare --brand 1 suspect.jpg
python -m surabhi namenet --brand 1 northstar-sale.com n0rthstar.in [--offline]
python -m surabhi scan --brand 1                # generated typo variants that are live in DNS
python -m surabhi investigate https://northstar-sale.shop/ [--image x.jpg] [--offline] [--no-fetch]
python -m pytest tests/test_brand_dna.py tests/test_name_net.py tests/test_brand_protection.py
```
