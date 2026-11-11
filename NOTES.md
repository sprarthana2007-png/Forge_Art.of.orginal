# NOTES — Prarthana's topics (build log, 11 Nov)

## What works (verified)
- **#3 Investigator** (`prarthana/investigator.py`): runs the #13 cloaking check first; headless Chromium (20 s timeout), full-page screenshot,
  captures every image response + `<img>` sources + favicon, exact (SHA-256) and look-alike (pHash ≤ 8) matches
  against Brand DNA, logo/favicon match, ₹/Rs/INR prices mapped to Brand-DNA product names, Indian phones /
  UPI IDs / e-mails → `hash_contact` + `mask_contact` (raw values only in memory), RDAP domain age, IP.
  Results cached per URL in `cache/investigator/`; matched images kept as thumbnails in `cache/images/`.
- **#8 Scorer** (`prarthana/scorer.py`): points + plain-English reason per rule, labels, allow-list, campaign
  grouping by union-find (shared contact hash / shared exact image SHA-256 / same public IP).
- **#7 urlscan nets** (`prarthana/urlscan_nets.py`): `hash:`, `domain:`, `page.title:"…"` searches, cached in
  `cache/urlscan/`, one retry after HTTP 429, excludes the brand + allow-list, dedupes on `seen_domains`.
  Live-tested: returns real suspects (e.g. a title hit on a brand-name `.com`).
- **#11 Gemini verdict** (`prarthana/verdict.py` + `prarthana/gemini.py`): ONE batched call per round, JSON
  output, cached in `cache/gemini/`, calls spaced ≥ 4.5 s; template verdict if Gemini fails. Live-tested.
- **#5 Alert + actions** (`prarthana/alert.py`): ReportLab evidence PDF per suspect, owner alert e-mail,
  `confirm()`, `send_abuse_report()` with RDAP abuse-contact lookup. Live-tested: a demo-mode abuse report was
  delivered via Resend to `GMAIL_ADDRESS` with the real abuse contact shown in the subject.
- **#1 Planner** (`prarthana/planner.py`, `main.py`): LangGraph StateGraph, conditional edges driven by state,
  `cache/state.json` persistence, `--watch` (APScheduler, every 6 h). Live-tested on a real public store.
- Tests: `python -m pytest -q tests` → 32 passed (unit: scorer math, masking/hashing, price/contact
  extraction, planner decisions; e2e: local store + clone; abuse-report confirmation gate).

## Gemini model
- `GEMINI_MODEL` = `gemini-3.5-flash-lite` was accepted by the API (it is listed by the models endpoint as
  "Gemini 3.5 Flash Lite"). If a model is ever rejected, `prarthana/gemini.py` lists the models and picks the
  one whose displayName is "Gemini 3.5 Flash Lite" → id `gemini-3.5-flash-lite` (tested with a bogus model).

## What is stubbed (teammate topics)
- `build_brand_dna` → `prarthana/stubs/brand_dna_stub.py` (minimal real version: images hashed, logo =
  `img` with "logo" in src/alt/class or in `<header>`, favicon, product names = h2/h3/h4 next to a price,
  official contacts hashed, brand name from og:site_name / short title / domain).
- `name_net` → `prarthana/stubs/name_net_stub.py` returns `[]`.
- Scam phrases: if no surabhi function exists, `scam_phrases` stays empty.
- `prarthana/teammate.py` automatically switches to Surabhi's real functions once they exist.

## What needs keys / services
- urlscan nets need `URLSCAN_API_KEY`; verdict + prioritise need `GEMINI_API_KEY`; Resend or Gmail for e-mail.
  All are skipped gracefully (logged) when missing; the e2e test runs with all keys blanked.
- `GMAIL_APP_PASSWORD` was not set in the build environment → SMTP path is untested (Resend path tested).
- The Resend key is "send-only" (cannot list domains) — fine for sending. With `onboarding@resend.dev` as
  sender, Resend only delivers to the account owner's own address, which is why everything goes to `GMAIL_ADDRESS`.

## What failed / limitations
- git push was refused (HTTP 403) during the build until the Claude GitHub App was installed on the repo;
  after that, everything was pushed to `claude/zealous-allen-27fdf2` and fast-forwarded to `main`.
- RDAP has no data for some hosts (e.g. sub-domains of hosting platforms, localhost) → `domain_age_days=None`.
- Some suspect sites time out at 20 s → logged, suspect stays `status=new`, scored 0.
- urlscan `domain:` matches any page that *loaded* something from the brand's domain; that net is noisy
  (blogs, deal sites). It still feeds the scorer, which ignores them unless the evidence adds up.

## Decisions I made
- `hash_contact` normalisation: phones → last 10 digits (so `+91 98765 43210`, `098765 43210`, `9876543210`
  hash the same); UPI/email → lowercase. `mask_contact` masks the normalised value.
- Contacts are read from visible page text and `mailto:` / `tel:` / `upi://pay?pa=` links (not raw HTML, to
  avoid false "emails" like `logo@2x.png`).
- The brand's own domain is always treated as allow-listed. For `localhost` the port is part of the domain,
  so the local demo clone on another port is not hidden.
- Images matching the brand logo/favicon count for the logo rule only, not the photo rule.
- Campaign grouping ignores the brand's own images (every clone copies them) and private/loopback IPs;
  singletons get `campaign_id=None`. Only non-ignored suspects are grouped.
- Re-hunt for a new campaign searches urlscan for the campaign's *shared* non-brand image hashes.
  Max 3 rounds (round 0, 1, 2).
- `decide`: new campaign & round < 2 → hunt; no new suspects and nothing un-alerted → stop; else → alert.
- At most 12 suspects investigated per round (each can take 20 s); the rest are released from
  `seen_domains` so the next run (or `--watch`) picks them up. Manual `--suspect` URLs always go first.
- Prices are matched to a product name when the name appears in the page text and a price follows within
  300 characters; unmatched prices are stored as `price_1…`.
- Gemini also orders the hunt (`prioritise`) in one batched call; fallback heuristic = found_by rank
  (manual > urlscan_hash > urlscan_hotlink > urlscan_title > name_net), then youngest domain.
- Extra fields were only ADDED to `schemas.py` (Suspect: `ip`, `image_sha256s`, `pdf_path`, `abuse_email`;
  BrandDNA: `logo_phash`, `favicon_phash`; ForgeState: `manual_urls`, `round_new`, `pending_hashes`,
  `handled_campaigns`, `alerted`). `state_to_dict` / `state_from_dict` serialise state to JSON.
- `FORGE_CACHE_DIR` / `FORGE_OUTPUT_DIR` env vars override `cache/` and `output/` (used by tests).
- If the Playwright browser for the pinned version is not installed, `launch_chromium` falls back to
  `CHROMIUM_PATH` or `/opt/pw-browsers/chromium`.
- PDFs write "Rs." instead of ₹ (ReportLab's built-in Helvetica has no rupee glyph).
- Evidence PDF "bento" theme (`prarthana/evidence_pdf.py`, called by `alert.make_evidence_pdf`): 4 pages (since #13: case file, evidence,
  cloaking check, action; before that 3)
  and data as before (case file / evidence / action), restyled as rounded cards numbered across the report
  ("01 / VERDICT" … "16 / NEXT STEPS"), on a stone background with a warm film-grain tile
  (`prarthana/assets/grain.png`, generated once with numpy if missing). Palette is limited to stone, bone, ink,
  muted ink, card border, signal orange + 3 tints, and sage for "original/real" things.
  Screenshots and image thumbnails are clipped into arches; image pairs show an orange "=" with the SHA-256
  (exact) or the pHash distance to the closest brand photo (look-alike). The extra page-1 height goes to the
  top two rows so the summary card has no empty band. Case ID `FORGE-YYYYMMDD-XXXX` = date + first 4 hex of
  SHA-256(url). Brand screenshot + originals are cached in `cache/brand_assets/`.
- Fonts: Instrument Serif (regular, italic), JetBrains Mono (regular, medium), Inter Tight (regular, medium)
  downloaded from Google Fonts as static TTFs into `prarthana/assets/fonts/` (SIL Open Font License) and
  embedded in the PDF. All downloads worked. If a file is missing, the PDF falls back to
  Times / Courier / Helvetica. Only Inter Tight has the ₹ glyph, so ₹ is used in Inter Tight body text and
  "Rs." in serif / mono text.
- Wording: singular/plural is handled by `common.plural()` ("1 look-alike", "1 exact copy", "1 day"),
  including the scorer's reasons.
- The owner alert e-mail now has a branded HTML version (plain-text fallback kept).

## #13 Cloaking check (`prarthana/cloaking.py`)
- **What it catches:** scam stores that show a clean page (e.g. "under maintenance") to bots and reviewers, and
  the real scam page only to shoppers who arrive from an Instagram / Facebook ad. FORGE visits each suspect
  three ways and compares them:
  1. **bot**: desktop headless Chromium, default user agent, no referrer;
  2. **Instagram-ad shopper**: Pixel 7 emulation (touch), Instagram in-app browser user agent, Referer
     `https://l.instagram.com/`, URL + `?fbclid=<random>&utm_source=ig&utm_medium=paid`;
  3. **India network**: urlscan.io Scan API (`visibility: unlisted`, `country: in`), polled up to 90 s,
     then its screenshot and page text (from urlscan's DOM copy).
- Each pair is compared on screenshot pHash distance (> 18 = visually different), visible-text similarity
  (difflib ratio < 0.6 = different) and the final domain after redirects. Cloaking = different text, a
  redirect to another domain, or a visual difference that the text does not explain.
  **Decision:** a visual difference alone is *not* counted when the text is ≥ 85% the same. Phone vs desktop
  layouts of the *same* page differ by 22–36 in our tests (identical text), so pixels alone would flag every
  responsive site. Those cases are listed as notes in the PDF.
- **Result:** `suspect.cloaking` (bool) and `suspect.cloaking_details` (visits with label / final URL /
  screenshot / text length, comparisons, plain-English differences, `visit3_status`). Cached in
  `cache/cloaking/`. Never raises; a failed visit is recorded and the others are still compared.
- **Scoring:** cloaking = +10 ("shows a different page to an Instagram-ad visitor than to a normal visitor").
  Thresholds unchanged (75 / 50). Max score is now exactly 100 (30+20+15+15+10+10). Cloaking alone (10) can
  never flag a site.
- **Evidence follows the shopper:** when cloaking is detected, the Investigator collects its evidence
  (images, prices, contacts, screenshot) as the Instagram-ad shopper. Otherwise it would only see the
  maintenance page and miss everything. In the test, the cloaked clone scores 90 (80 + 10); without this switch it
  would have scored ~0.
- **Visit 3 status: skipped.** urlscan has **no India scanning location for any plan**: `country: "in"` is
  rejected ("not part of the available scanning locations"), and `/api/v1/availableCountries/` lists de, us,
  jp, fr, gb, nl, ca, it, es, se, fi, dk, no, is, au, nz, pl, sg, pt, at, ch, il, tr, tw, be. So visit 3 is skipped
  with that reason and the check runs on visits 1 + 2. Set `FORGE_CLOAK_COUNTRY=sg` (Singapore) to use the nearest
  available location instead; that path was tested live (scan → poll → screenshot → text in ~25 s). Visit 3
  is also skipped for local addresses, when `URLSCAN_API_KEY` is missing, or when the time budget is low.
- **Planner:** `prioritise` builds `cloak_queue` with owner reports, `urlscan_hash` and `urlscan_hotlink`
  suspects first. `investigate` runs cloaking checks in that order within `FORGE_CLOAK_BUDGET` seconds
  (default 300). Visit 3 runs only while ≥ 100 s of budget is left, and checks after the budget is used up are
  skipped (recorded in the PDF).
- **Evidence PDF is now 4 pages:** a dedicated "cloaking check" page after the evidence page (verdict band, the
  three visits as arch screenshots labelled bot / Instagram-ad shopper / India network, a comparison table, the
  differences as bullets, visit 3 status). Page 2 was already full, so a card there would have pushed the
  contacts table onto a near-empty extra page. Also: a "Cloaking" row in the page-1 signals, a cloaking segment
  in the score bar, and a "Cloaking check" step in the timeline. Gemini gets `cloaking` + the differences as
  input and mentions cloaking in the summary when found.
- **Honest limits:**
  - Sites that cloak **only by Indian mobile IP address** (show the scam only to visitors on Jio / Airtel / Vi
    mobile networks) are not caught fully: FORGE's visits come from a datacenter, and urlscan has no Indian
    location. Catching those needs a mobile / residential Indian network (e.g. a phone hotspot or a residential
    proxy) for visit 2.
  - Cloaking by other signals (cookies from a real ad click, Facebook's own click-validation, time of day,
    fingerprinting that spots headless Chromium) may still hide the scam from all three visits.
  - Legit sites with anti-bot walls (e.g. a challenge page for headless browsers) can look like cloaking.
    That is why cloaking is only +10 and is shown with its evidence for a human to judge.

## Tamper-evident evidence PDFs (`prarthana/signing.py`)
- Every evidence PDF is made tamper-evident right after it is generated (`alert.make_evidence_pdf`):
  1. **Permissions:** pyHanko re-writes the PDF with AES-256 encryption (PDF 2.0), an **empty user password**
     (opens normally, no prompt) and a **random owner password** that is never stored. Allowed: printing,
     copying text, accessibility. Not allowed: editing, annotations, form filling, page assembly.
     (ReportLab's own encryption was not used: its AES mode needs `pyaes`, which does not build here, and
     RC4 is obsolete.)
  2. **Signature:** a pyHanko *certification* signature (DocMDP "no changes allowed") with a visible stamp in the
     "17 / SIGNATURE" card on the last page: "Digitally signed by FORGE · <UTC time> · any change after signing
     invalidates this signature". The signature covers the entire file (poppler `pdfsig`: "Total document
     signed", "Signature is Valid").
  3. **Fingerprint:** SHA-256 of the final signed file, stored in `suspect.evidence_sha256` (so in
     `cache/state.json`), written next to the PDF as `<pdf>.sha256`, logged
     (`evidence: signed <file> sha256=<hash>`), and put in the alert e-mail ("Evidence fingerprint: <sha256>")
     and in abuse reports.
- Signing certificate: self-signed "FORGE Evidence Signer" (RSA-3072, 5 years), created once in
  `cache/signing/` (`forge_signer_key.pem`, mode 600, + `forge_signer_cert.pem`). `cache/` is git-ignored:
  **never commit the key.** Deleting it makes FORGE create a new key, and older PDFs then no longer verify
  (they were signed by a key FORGE no longer trusts). Keep a backup of `cache/signing/` if old PDFs matter.
- `verify_evidence(pdf_path) -> bool` (in `prarthana/alert.py`): True only if the file's SHA-256 equals the
  recorded fingerprint (sidecar, else state.json) **and** the pyHanko signature is intact, valid, made by the
  FORGE certificate, covers the whole file, and nothing changed after signing. `send_abuse_report()` re-checks
  the PDF with it and regenerates a fresh signed PDF if the old one was changed.
- **What Adobe Acrobat / Reader will show:** the certificate is self-signed, so Acrobat shows the signer as
  **"signer identity unknown"** / "validity unknown" (the certificate is not in Adobe's trust list; the same
  is true in poppler: "Certificate issuer is unknown"). That only concerns *who* signed. Integrity is still
  checked: if anyone changes the file after signing, Acrobat reports **"the document has been modified"** /
  "signature is invalid". To make Acrobat show the signer as trusted, the brand owner can trust
  `forge_signer_cert.pem` in Acrobat (Preferences → Signatures → Identities & Trusted Certificates), or a
  production version can use a certificate from an Adobe-trusted (AATL) CA.
- Caveat (from the pyHanko docs): PDF encryption on its own is not tamper-proof; the **signature + SHA-256
  fingerprint** are what make tampering detectable. The permissions only stop casual editing in normal viewers.
- Tests (`tests/test_signing.py`): sign → `verify_evidence` True; flip one byte → False; append bytes after
  signing → False; signed by a different key → False; permissions are print-only; key file is mode 600.
  The e2e test also checks the fingerprint in state.json, in the log and in the alert e-mail.

## For Surabhi — what to use / provide
Use from my code:
```python
from prarthana.alert import confirm, send_abuse_report, make_evidence_pdf
confirm(suspect, yes=True)            # dashboard "Yes, it's fake" -> status "confirmed" (False -> "not_fake")
send_abuse_report(suspect, brand_dna) # only works when status == "confirmed"; sets status "reported"
```
- Evidence PDFs: `output/evidence_<domain>.pdf` (path also in `suspect.pdf_path`), digitally signed;
  fingerprint in `suspect.evidence_sha256`. Check a PDF with `verify_evidence(path)` (e.g. show a
  "verified" badge in the dashboard).
- Dashboard text: `suspect.verdict` (3 sentences), `suspect.score`, `suspect.label`, `suspect.reasons`,
  `suspect.contacts_masked`, `suspect.screenshot_path`, `suspect.campaign_id`, `suspect.status`.
- All results: `cache/state.json` (load with `schemas.state_from_dict(json.load(...))`); after confirming,
  save it back with `prarthana.planner.save_state(state)`.

Please provide (same signatures; my code imports them automatically):
```python
# surabhi/brand_dna.py
def build_brand_dna(url: str) -> BrandDNA: ...
# surabhi/name_net.py
def name_net(dna: BrandDNA) -> list[Suspect]: ...      # set found_by="name_net"
# surabhi/scam_phrases.py
def find_scam_phrases(text: str) -> list[str]: ...     # e.g. ["UPI only", "no COD"]
```
- Demo sites: `surabhi/demo_sites/store/index.html` and `surabhi/demo_sites/clone/index.html` (static, with
  their images next to them). The e2e test uses them automatically when both exist.
- Please set `verified_owner` and `allow_list` (official marketplaces / other own domains) in Brand DNA.
