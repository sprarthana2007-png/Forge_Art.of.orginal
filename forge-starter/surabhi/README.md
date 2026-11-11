# FORGE — owner dashboard (Surabhi)

The owner-facing part of FORGE: a Python + Streamlit dashboard where a brand owner
reviews websites that may be impersonating their brand, looks at the evidence, asks
Google Gemini for a verdict, and records a decision.

It reads and writes the team's shared state file (`ForgeState` in `schemas.py`), so it
works with whatever the other FORGE modules discover and score.

## Run locally

Install Python 3.10 or newer and open a terminal in this `surabhi` folder.

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows PowerShell
source .venv/bin/activate           # macOS / Linux
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open the local URL printed in the terminal, usually http://localhost:8501.

## What's included

| File | Responsibility |
| --- | --- |
| `schemas.py` | The team's shared schemas and hashing helpers (`BrandDNA`, `Suspect`, `ForgeState`) |
| `forge_state.json` | Demo `ForgeState`: the Northstar brand and four suspects |
| `app.py` | Entry point and page list |
| `views/cases.py` | Case file cards, counters and a summary table |
| `views/evidence.py` | One case file: record-sleeve case picker, evidence tabs (Overview, Images, Domain, Phrases, Verdict), owner decision |
| `views/reports.py` | Report status tracking |
| `views/settings.py` | Brand, business details, appearance, Gemini status |
| `storage.py` | Reads the shared state, keeps owner reviews in SQLite, writes decisions back to `Suspect.status` |
| `gemini.py` | Builds the investigation JSON and asks Gemini (Google Gen AI SDK) for a structured verdict |
| `analyze.py` | Playwright screenshots, image matching with the shared hash helpers, phrases, contacts, domain age |
| `phrases.py` | Finds pressure and payment phrases ("UPI only", countdowns, prize fees) in page text |
| `ui.py` | Shared look, header, bottom menu and building blocks |
| `.streamlit/config.toml` | Streamlit theme defaults |
| `evidence/` | Screenshots and cropped image matches written by `analyze.py` |
| `demo_sites/` | Fictional HTML sites behind the demo evidence |
| `tests/` | Shared-state, storage, phrase, age and Gemini checks |

## Demo flow

Use the menu at the bottom of the screen to move between pages.

1. On **Cases**, click a case file card.
2. Check the tabs: **Overview** (side-by-side sites, reasons), **Images** (copied
   photos), **Domain** (age and site facts), **Phrases** (pressure phrases).
3. Open the **Verdict** tab and click **Ask Gemini** (needs a Gemini API key, see below).
4. Under **Owner decision**, click **Fake**, **Genuine**, or **Unsure**. Pick another
   case from the numbers on the record, or Previous / Next Case.
5. Open **Reports**, select the case, choose **Drafted**, and save.

The brand, sites, evidence and scores in the demo are synthetic.

## Working with the shared schema

The dashboard reads `forge_state.json` next to `app.py`, or the file named in
`FORGE_STATE_FILE`. That file is `state_to_dict(ForgeState)` output from `schemas.py`.

**Read:** every `Suspect` whose `label` is not `ignored` becomes a case. The case
number is given the first time a domain is seen and never changes, even if the
pipeline reorders or rescores suspects. Shown from the schema:

| Schema field | Where it appears |
| --- | --- |
| `BrandDNA.store_url`, `brand_name` | Header text, "Official website" panel, Gemini brand details |
| `BrandDNA.prices`, `product_names`, `verified_owner`, `allow_list` | Sent to Gemini; official prices shown beside `prices_found` |
| `Suspect.url`, `domain`, `score`, `reasons`, `label` | Case cards, file header, risk stamp, Reasons |
| `screenshot_path` | Suspected website panel |
| `image_exact_matches`, `image_similar_matches`, `logo_match` | Images tab counts |
| `domain_age_days` | Domain tab, Cases table, Gemini |
| `scam_phrases` | Phrases tab (alongside phrases found in `page_text`) |
| `found_by`, `ip`, `abuse_email`, `campaign_id`, `status` | Domain tab site facts |
| `contacts_masked`, `prices_found` | Domain tab (contacts are only ever shown masked) |
| `evidence_sha256`, `pdf_path` | Domain tab: evidence fingerprint and PDF download |
| `verdict` | Shown as the verdict until Gemini is asked from the dashboard |

**Added fields:** following the rule "only add fields; never rename or remove", I added
these optional fields to `schemas.py` (marked *added by surabhi*):

| Class | Field | Purpose |
| --- | --- | --- |
| `BrandDNA` | `screenshot_path` | Screenshot of the official store, shown beside each suspect |
| `BrandDNA` | `domain_age_days` | Age of the official domain, for comparison |
| `Suspect` | `page_title`, `page_text` | Page text sent to Gemini and searched for phrases |
| `Suspect` | `matched_image_pairs` | `[official_image, suspect_image, match_kind]` so copies can be shown side by side |

**Write:** owner reviews are saved in SQLite (`data/forge.db`) and mirrored into the
suspect's `status`, touching nothing else in the file:

| Owner review | `Suspect.status` |
| --- | --- |
| Fake | `confirmed` |
| Genuine | `not_fake` |
| Unsure | `investigated` |
| Report Sent or Resolved | `reported` |

For a suspect seen for the first time, a pipeline status of `confirmed`, `not_fake` or
`reported` pre-fills the owner decision. After that the owner's review in SQLite wins,
so a pipeline re-run can never overwrite it. The file is always replaced atomically.

## Collecting evidence

`analyze.py` fills in the visual evidence using the shared helpers from `schemas.py`:

- **Playwright** opens each site in Chromium and saves a screenshot.
- `file_sha256` finds exact copies of official images; `photo_phash` + `is_similar`
  (threshold `PHASH_MATCH`) finds near-duplicates, including mirrored copies.
- Contacts on the page are stored only as `hash_contact` and `mask_contact` values.
- Domain age comes from a known registration date or an RDAP lookup.
- Phrases found by `phrases.py` are added to `scam_phrases`.

```bash
python -m pip install -r requirements-analysis.txt
python -m playwright install chromium
python analyze.py                            # demo: demo_sites/sites.json -> forge_state.json
python analyze.py --state path/to/state.json # enrich the team pipeline's state file in place
```

When enriching, `score`, `label`, `reasons`, `verdict`, `status` and `found_by` are left as the pipeline wrote them.

## Gemini verdict

The **Verdict** tab sends the case to Google Gemini through the Google Gen AI SDK and
shows a verdict (Likely impersonation, Suspicious, Likely legitimate or Inconclusive),
a confidence, a summary, the key findings and a recommended next step.

What is sent: an investigation document in JSON (brand details, suspect URL, age, page
text, phrases, masked contacts, prices, score, reasons and image matches) plus
screenshots of the official and suspected sites. The exact JSON is shown on the tab
before you run it. Gemini replies in JSON that follows a fixed schema (`Verdict` in
`gemini.py`), and each verdict is saved in `data/forge.db`.

```bash
export GEMINI_API_KEY=your-key          # Windows PowerShell: $env:GEMINI_API_KEY="your-key"
export GEMINI_MODEL=gemini-flash-latest # optional; this is the default
python -m streamlit run app.py
```

Without a key the app still works; the Verdict tab explains how to connect. If it says
`google-genai` or `pydantic` is not installed, stop the app and run
`python -m pip install -r requirements.txt` with the same Python that runs Streamlit.

## Demo sites

`demo_sites/` holds five fictional websites for the made-up brand Northstar. Open any
`index.html` in a browser to look around:

| Folder | Domain | What it shows |
| --- | --- | --- |
| `official/` | northstar.example | The genuine brand store |
| `case1/` | northstar-sale.example | Copied logo and product photos, 90% off, countdown, UPI only |
| `case2/` | northstar-outlet.example | Look-alike name, one mirrored and recoloured product photo |
| `case3/` | northstar-partner.example | A plausible authorised retailer using brand images |
| `case4/` | northstar-rewards.example | Prize claim that asks for a fee, urgency timers, copied logo |

Every page carries a "Synthetic demo · not a real website" label. `demo_sites/sites.json`
stands in for the team's discovery and scoring steps in the demo.

## Data and deployment

Owner reviews, settings and Gemini verdicts are stored in `data/forge.db` (override
with `FORGE_DB_PATH`); it is created on first run. Back it up to keep reviews.
There is no login: anyone who can open the app can review every case and change
settings. Keep it on a private network, or add authentication in front of it, before
exposing real evidence.

## Tests

```bash
python -m unittest discover -s tests -v
```
