"""Command line for Brand DNA + NameNet.

    python -m surabhi register --name Northstar --url https://northstar.com --logo logo.png --ref a.jpg --ref b.jpg
    python -m surabhi brands
    python -m surabhi compare --brand 1 suspect1.jpg suspect2.png
    python -m surabhi namenet --brand 1 northstar-sale.com n0rthstar.in [--offline]
    python -m surabhi scan --brand 1 [--limit 300]
    python -m surabhi investigate https://northstar-sale.shop/ [--brand 1] [--image x.jpg] [--offline] [--no-fetch]

Results are printed as JSON. The database is the dashboard's SQLite file (FORGE_BRAND_DB / FORGE_DB_PATH override).
"""
from __future__ import annotations

import argparse
import json
import sys

from surabhi import brand_dna, name_net, protection, store


def _brand(conn, brand_id):
    b = store.get_brand(conn, brand_id)
    if not b:
        raise SystemExit(f"No registered brand with id {brand_id}. Run `python -m surabhi brands`.")
    return b


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m surabhi", description="Brand DNA + NameNet brand protection")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register", help="register a brand and fingerprint its images")
    r.add_argument("--name", required=True)
    r.add_argument("--url", required=True, help="official website")
    r.add_argument("--logo")
    r.add_argument("--ref", action="append", default=[], help="reference image (repeatable)")
    sub.add_parser("brands", help="list registered brands and fingerprint status")
    c = sub.add_parser("compare", help="compare image files with registered fingerprints")
    c.add_argument("--brand", type=int)
    c.add_argument("images", nargs="+")
    n = sub.add_parser("namenet", help="analyse candidate domains against a brand")
    n.add_argument("--brand", type=int, required=True)
    n.add_argument("--offline", action="store_true", help="skip DNS / registration lookups")
    n.add_argument("domains", nargs="+")
    s = sub.add_parser("scan", help="generate typo / lookalike variants and report the live ones")
    s.add_argument("--brand", type=int, required=True)
    s.add_argument("--limit", type=int, default=300)
    i = sub.add_parser("investigate", help="NameNet + Brand DNA on a suspicious domain or website")
    i.add_argument("target")
    i.add_argument("--brand", type=int)
    i.add_argument("--image", action="append", default=[], help="extra image file to compare (repeatable)")
    i.add_argument("--offline", action="store_true", help="skip DNS / registration lookups")
    i.add_argument("--no-fetch", action="store_true", help="do not download the website")
    a = ap.parse_args(argv)

    try:
        with store.connect() as conn:
            if a.cmd == "register":
                out = brand_dna.register_brand(a.name, a.url, logo=a.logo, reference_images=a.ref, conn=conn)
            elif a.cmd == "brands":
                out = brand_dna.list_brands(conn)
            elif a.cmd == "compare":
                brands = [_brand(conn, a.brand)] if a.brand else None
                out = brand_dna.compare_images(a.images, brands, conn=conn)
            elif a.cmd == "namenet":
                out = name_net.check_domains(_brand(conn, a.brand), a.domains, check_network=not a.offline, conn=conn)
            elif a.cmd == "scan":
                b = _brand(conn, a.brand)
                out = name_net.scan_variants(b["official_domain"], b["name"], limit=a.limit)
                with conn:
                    for f in out["findings"]:
                        store.save_finding(conn, b["id"], f)
            else:
                out = protection.investigate(a.target, a.brand, images=a.image, check_network=not a.offline,
                                             fetch_website=not a.no_fetch, conn=conn)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
