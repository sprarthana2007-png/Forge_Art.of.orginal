"""Surabhi's Brand Protection modules for FORGE.

    brand_dna   - register a brand and fingerprint its logo / reference images (SHA-256 + perceptual hash)
    name_net    - brand-name and domain lookalike detection (typosquats, homoglyphs, keyword domains)
    protection  - one workflow that runs both on a suspicious domain / website and writes a combined report
    store       - SQLite storage (shares the dashboard's database file)

The FORGE planner (prarthana/teammate.py) imports `surabhi.brand_dna.build_brand_dna` and
`surabhi.name_net.name_net` from here, so the pipeline uses these instead of the stubs.
"""
