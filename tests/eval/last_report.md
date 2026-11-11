# FORGE evaluation — small labelled test set

Run 2026-11-11 17:47 UTC · **20 pages** (10 fake, 10 legitimate) · `python -m prarthana.evaluate`

> **Read this first: the sample is tiny and synthetic.** 20 hand-made local pages, built by the FORGE team itself, cannot tell you how FORGE performs on the real internet. The 95% confidence intervals below are very wide for that reason. Treat the numbers as a regression check (did a change break something?) and a sanity check of the scoring rules, not as a measured accuracy.

## Results

### Flagged for the owner's review (score ≥ 50 = likely fake or suspicious)

| | predicted fake | predicted legit |
|---|---|---|
| **actually fake** (10) | 8 (TP) | 2 (FN) |
| **actually legit** (10) | 1 (FP) | 9 (TN) |

Precision **89%** (95% CI 56%–98%) · Recall **80%** (95% CI 49%–94%) · F1 0.84 · Accuracy 85%

### Likely fake (score ≥ 75)

| | predicted fake | predicted legit |
|---|---|---|
| **actually fake** (10) | 3 (TP) | 7 (FN) |
| **actually legit** (10) | 0 (FP) | 10 (TN) |

Precision **100%** (95% CI 44%–100%) · Recall **30%** (95% CI 11%–60%) · F1 0.46 · Accuracy 65%

## Per page

| page | truth | FORGE score | FORGE label | signals that fired | what it is |
|---|---|---|---|---|---|
| fake-full | fake | 80 ✓ | likely_fake | photos, logo, price, contacts | Classic clone: brand photos + logo, 70% off, its own UPI / WhatsApp. |
| fake-no-discount | fake | 65 ✓ | suspicious | photos, logo, contacts | Clone at normal prices, other contacts. |
| fake-lookalike | fake | 80 ✓ | likely_fake | photos, logo, price, contacts | Re-encoded / resized brand photos (look-alikes), logo, 60% off. |
| fake-no-logo | fake | 60 ✓ | suspicious | photos, price, contacts | Brand photos under another name and logo, 50% off. |
| fake-cloaking | fake | 90 ✓ | likely_fake | photos, logo, price, contacts, cloaking | Full clone that shows bots a maintenance page (cloaking). |
| fake-relaunch | fake | 70 ✓ | suspicious | photos, price, contacts, relaunch | Relaunch of old-fake: same UPI / WhatsApp and payment QR, brand photos, 50% off. |
| fake-young | fake | 60 ✓ | suspicious | photos, logo, young_domain | Brand photos + logo, no prices / contacts shown yet; domain 5 days old (injected). |
| fake-subtle | fake | 45 ✗ | ignored | photos, contacts | Only look-alike photos + its own UPI, normal prices (hard case). |
| fake-text-only | fake | 30 ✗ | ignored | price, contacts | Own photos but the brand's product names at 70% off + UPI (hard case). |
| fake-logo-cheap | fake | 50 ✓ | suspicious | logo, price, contacts | Brand logo, own photos, 70% off, own UPI. |
| store | legit | 0 ✓ | ignored | — | The brand's real store. |
| reseller | legit | 0 ✓ | ignored | — | Approved reseller on the allow-list (brand photos + logo). |
| unrelated-shop | legit | 15 ✓ | ignored | contacts | Unrelated shop with similar product names, own photos and prices. |
| brand-mention | legit | 0 ✓ | ignored | — | Blog post that only mentions the brand name. |
| review-blog | legit | 45 ✓ | ignored | photos, contacts | Review blog using one brand photo with credit. |
| discount-shop | legit | 30 ✓ | ignored | price, contacts | Discount shop selling its own products under the same generic names, cheaply. |
| link-page | legit | 20 ✓ | ignored | logo | The brand's own link-in-bio page (logo + official contacts). |
| influencer | legit | 65 ✗ | suspicious | photos, logo, contacts | Influencer post with the brand's photos + logo and her own e-mail (hard case). |
| news-article | legit | 20 ✓ | ignored | logo | News article showing the brand's logo. |
| crafts-market | legit | 15 ✓ | ignored | contacts | Marketplace of other artisans, own photos and contacts. |

## How it was run and its limits

- Every page runs through the real pipeline: Brand DNA (from the `store` page, with `prarthana.stubs.brand_dna_stub (stub)`), the Investigator with the 3-way cloaking check, the relaunch check against one confirmed fake (`old-fake`), and the scorer with default weights. No external services are used.
- Each page has its own domain (`<name>.localhost`), so the brand's own store and the allow-listed reseller are recognised by domain, as in production.
- Not exercised: the **young-domain** signal comes from RDAP / WHOIS, which local pages do not have, so one page's age is injected from `labels.json`; **IP** links (no DNS for `*.localhost`); cloaking visit 3 (urlscan cannot reach local pages); Gemini (not part of the score).
- The pages were written after the scorer and the scorer was not tuned to them, but the same people wrote both, so the set reflects the clues we already know about. Real fakes vary far more (other languages, images cropped or with text on top, prices in other formats, pages behind logins).
- The two missed fakes are the hard cases on purpose: one with only look-alike photos + its own UPI and normal prices, one with the brand's product names and prices but its own photos. The false alarm is an influencer post that legitimately shows the brand's photos and logo next to her own e-mail: a human confirmation step (FORGE never reports without it) is exactly for cases like this.
- **The evaluation found one real bug, fixed before these numbers:** a legitimate marketplace was marked as a *relaunch* of the confirmed fake because both pages showed the same small 200×80 logo. Many unrelated shops share small images (UPI / card badges, theme icons), so images under 100 px on the short side no longer link sites (relaunch, campaigns, campaign graph). They still count for matching the brand's own photos / logo. The score was below 50 anyway, but the false relaunch label would have been shown to the owner.
- To grow this into a real benchmark: label 100+ real suspects found by the urlscan / name nets (owner answers from `cache/feedback.json` are a natural source) and report per-signal results.
