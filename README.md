# FORGE_Art.of.Original

**FORGE** is a B2B agent for small D2C (direct-to-consumer) brands. You give it your real store URL. It
learns your brand (Brand DNA), hunts look-alike / clone stores, investigates every suspect, scores it,
and — only after **you** confirm "Yes, it's fake" — sends an evidence PDF and an abuse report.

AgentX Hackathon 2026 · PS-07 open track · **Team Shared 21**

## Privacy rules
- No real personal data is stored. Phone numbers, UPI IDs and e-mails found on suspect sites are
  SHA-256 hashed and shown masked (e.g. `98XXXXXX21`); raw values never leave memory.
- A human confirms before any abuse report is sent. In demo mode (default) reports go to your own inbox.
- Only public data is collected (domains, screenshots, public images).

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```
Optional keys (environment variables, never commit them; spaces are stripped automatically):

| Variable | Used for | If missing |
|---|---|---|
| `URLSCAN_API_KEY` | urlscan nets (#7) | urlscan search skipped |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | verdicts (#11), prioritising (#1) | template verdicts, heuristic ranking |
| `RESEND_API_KEY` | e-mail via Resend | falls back to Gmail SMTP |
| `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` | recipient / SMTP | e-mail saved as `output/*.eml` |
| `DEMO_MODE` | anything but `off` = abuse reports go to `GMAIL_ADDRESS` | demo mode on |

## Run
```bash
python main.py --store https://your-store.example
```
```bash
python main.py --store https://your-store.example --suspect https://suspicious-copy.example
```
Flags: `--suspect URL` (repeatable) adds a known suspect directly, `--fresh` ignores `cache/state.json`,
`--watch` re-runs the hunt every 6 hours (APScheduler).

Outputs: `output/evidence_<domain>.pdf` (digitally signed, print-only; SHA-256 fingerprint in `<pdf>.sha256`
and in the alert e-mail; check with `prarthana.alert.verify_evidence(path)`), `output/alert_email.eml` (when no mail service), `cache/state.json`.

## Tests
```bash
python -m pytest -q tests
```
`tests/test_e2e.py` serves a tiny real store and a clone (`tests/fixtures/`, or `surabhi/demo_sites/` if present)
on two local ports and runs the whole flow: the clone must score ≥ 75 and the real store must be ignored.

## Architecture
```
                      python main.py --store URL [--suspect URL]
                                       |
                        prarthana/planner.py  (LangGraph StateGraph over ForgeState)
                        weights tuned from the owner's past answers (cache/feedback.json)
                                       |
  [brand_dna] --> [hunt] --> [prioritise] --> [investigate] --> [score] --> [verdict] --> [decide]
   build_brand_dna   |        relaunches +      cloaking check    relaunch      one batched      |
   (surabhi/ or      |        hash/hotlink      (3 visits) +      check +       Gemini call /    |
    stub)            |        first; Gemini     Playwright:       weighted      template         |
                     |        / heuristic       images, prices,   rules +                        |
                     |                          contacts, RDAP /  campaigns                      |
                     |                          WHOIS                                            |
                     +-- name_net (surabhi/ or stub)                                              |
                     +-- urlscan nets: hash / hotlink / title (+ hashes of confirmed fakes)       |
                                                                                                  |
        new campaign & round < 3  <-------------------------------------------------------------+
        (re-hunt with campaign's shared image hashes)                                            |
        no new suspects --> END                                                                   |
        otherwise --> [alert]: campaign graph (PNG + HTML), signed evidence PDFs,                 |
                      "N possible fakes" e-mail to the owner --> END                              |

  Dashboard (surabhi/) --> confirm(suspect, yes)  -> feedback.json (learning), confirmed_fakes.json
                                                     (relaunch history), safe_browsing_<domain>.txt
                       --> send_abuse_report(suspect) -> verified signed PDF + Safe Browsing pack
                                                         to the registrar abuse contact -> reported
```
## Features at a glance
| | what | where |
|---|---|---|
| Hunt | urlscan nets (image hash, hotlinking, page title) + name net; re-hunts campaigns and confirmed fakes' images | `urlscan_nets.py`, `planner.py` |
| Investigate | headless Chromium: screenshots, image copies (SHA-256 + pHash), prices, hashed + masked contacts, domain age (RDAP, WHOIS fallback) | `investigator.py` |
| Cloaking check (#13) | bot vs Instagram-ad shopper vs another country (urlscan, Singapore); evidence collected as the shopper | `cloaking.py` |
| Score | 7 weighted signals, labels 75 / 50, campaigns, plain-English reasons | `scorer.py` |
| Relaunch tracking (#16) | new sites that share contacts / images / IP with a fake you confirmed: +10 and investigated first | `relaunch.py` |
| Learning (#19) | every "fake / not fake" answer tunes each signal's weight (0.5×–1.5×) | `learning.py` |
| Campaign graph (#14) | NetworkX graph → PNG in the PDF + self-contained `output/campaign_graph.html` | `campaign_graph.py` |
| Evidence PDF | 5-page bento report, digitally signed (pyHanko), print-only, SHA-256 fingerprint, `verify_evidence()` | `evidence_pdf.py`, `signing.py` |
| Alert + actions | owner e-mail, `confirm()`, demo-safe abuse report, Google Safe Browsing report pack (#15) | `alert.py` |
| Evaluation | 20 labelled local pages, precision / recall report | `evaluate.py`, `tests/eval/` |

Score: product photo copy/look-alike +30 · logo/favicon +20 · price ≥ 40% below +15 · unofficial contacts +15 ·
domain < 30 days old +10 · cloaking (different page for an Instagram-ad shopper than for a bot) +10 ·
relaunch of a fake you already confirmed +10 (points scaled 0.5×–1.5× by what FORGE learned from your answers). Labels: ≥ 75 `likely_fake`, 50–74 `suspicious`, < 50 `ignored`. Allow-listed domains
(and the brand's own) are always ignored.

## Accuracy (small labelled test set)
`python -m prarthana.evaluate` runs the full pipeline on **20 hand-made local pages** (10 fake clones with different
mixes of clues, incl. a cloaking one and a relaunch one, and 10 legitimate pages: the real store, an allow-listed
reseller, an unrelated shop with similar product names, a page that only mentions the brand, a review blog, an
influencer post, …). Labels: `tests/eval/labels.json`; full report: `output/eval_report.md`
(copy in `tests/eval/last_report.md`).

| threshold | TP | FP | FN | TN | precision (95% CI) | recall (95% CI) |
|---|---|---|---|---|---|---|
| flagged for review (score ≥ 50) | 8 | 1 | 2 | 9 | 89% (56%–98%) | 80% (49%–94%) |
| likely fake (score ≥ 75) | 3 | 0 | 7 | 10 | 100% (44%–100%) | 30% (11%–60%) |

**Be careful with these numbers:** 20 synthetic pages built by the same team that wrote the scorer is a sanity /
regression check, not a measured real-world accuracy; the confidence intervals are very wide. Missed: two
deliberately hard fakes (look-alike photos only; product names + prices with own photos). False alarm: an influencer
post showing the brand's photos and logo — which is why FORGE never reports anything without the owner's
confirmation. The evaluation also found (and we fixed) a false "relaunch" link caused by a shared small logo.

## Cloaking check (#13)
Scam sites often show a clean page to bots and the real scam page only to shoppers from an Instagram / Facebook ad.
FORGE visits every suspect three ways (desktop **bot**, **Instagram-ad shopper** on an emulated Android phone with
an Instagram referrer and `fbclid`, and **another country** via urlscan.io, Singapore by default) and compares screenshots,
visible text and final domains. If the visits see different pages, that is evidence (+10) and FORGE collects the
rest of its evidence as the shopper. See NOTES.md for details and limits (visit 3 runs from Singapore because urlscan has no India location; sites
that cloak only by Indian mobile IP need a mobile / residential network).

## Repo layout
```
schemas.py      shared dataclasses + hashing helpers (both teammates)
prarthana/      Prarthana's topics (#1 #3 #5 #7 #8 #11) + stubs for teammate functions
surabhi/        Surabhi's topics (#2 #4 #6 #9 #10 #12)
main.py         runs the whole flow
tests/          unit + end-to-end tests, fixtures
```

## Team Shared 21
- **Prarthana S** (@sprarthana2007-png): #1 Planner, #3 Investigator, #5 Alert + actions, #7 urlscan nets,
  #8 Scorer, #11 Gemini verdict, #13 Cloaking check, #14 Campaign graph, #15 Safe Browsing report pack,
  #16 Relaunch tracking, #19 Learning from the owner's answers, WHOIS fallback, signed evidence PDFs, and the
  evaluation on a small labelled test set (`prarthana/evaluate.py`).
- **Surabhi Muddan** (@surabhimuddan): #2 Brand DNA, #4 Dashboard, #6 Name net, #9 Submission (video, deck),
  #10 Scam-phrase checklist, #12 Demo sites + owner verification.
