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

Outputs: `output/evidence_<domain>.pdf`, `output/alert_email.eml` (when no mail service), `cache/state.json`.

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
                                       |
  [brand_dna] --> [hunt] --> [prioritise] --> [investigate] --> [score] --> [verdict] --> [decide]
   build_brand_dna   |        Gemini rank /     Playwright,       rules +       one batched      |
   (surabhi/ or      |        heuristic         images, prices,   campaigns     Gemini call /    |
    stub)            |                          contacts, RDAP    (union-find)  template         |
                     +-- name_net (surabhi/ or stub)                                              |
                     +-- urlscan nets: hash / hotlink / title                                     |
                                                                                                  |
        new campaign & round < 3  <-------------------------------------------------------------+
        (re-hunt with campaign's shared image hashes)                                            |
        no new suspects --> END                                                                   |
        otherwise --> [alert]: evidence PDFs + "N possible fakes" e-mail to the owner --> END     |
                                                                                                  
  Dashboard (surabhi/) --> confirm(suspect, yes) --> send_abuse_report(suspect) --> status=reported
```
Score: product photo copy/look-alike +30 · logo/favicon +20 · price ≥ 40% below +15 · unofficial contacts +15 ·
domain < 30 days old +10. Labels: ≥ 75 `likely_fake`, 50–74 `suspicious`, < 50 `ignored`. Allow-listed domains
(and the brand's own) are always ignored.

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
  #8 Scorer, #11 Gemini verdict.
- **Surabhi Muddan** (@surabhimuddan): #2 Brand DNA, #4 Dashboard, #6 Name net, #9 Submission (video, deck),
  #10 Scam-phrase checklist, #12 Demo sites + owner verification.
