"""SQLite storage for Brand DNA and NameNet.

Uses the same database file as the owner dashboard (forge-starter/surabhi/data/forge.db, or FORGE_DB_PATH)
and only adds its own tables, so the dashboard's `reviews` / `profile` / `verdicts` tables are untouched.
FORGE_BRAND_DB overrides the location for this module alone (tests use it for a throw-away file).
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "forge-starter" / "surabhi" / "data" / "forge.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bp_brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    official_url TEXT NOT NULL,
    official_domain TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bp_brand_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL REFERENCES bp_brands(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('logo', 'reference')),
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    phash TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    format TEXT NOT NULL,
    stored_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (brand_id, sha256)
);
CREATE TABLE IF NOT EXISTS bp_domain_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL REFERENCES bp_brands(id) ON DELETE CASCADE,
    domain TEXT NOT NULL,
    similarity INTEGER NOT NULL,
    classification TEXT NOT NULL,
    techniques TEXT NOT NULL,
    reasons TEXT NOT NULL,
    dns TEXT NOT NULL,
    domain_age_days INTEGER,
    checked_at TEXT NOT NULL,
    UNIQUE (brand_id, domain)
);
CREATE TABLE IF NOT EXISTS bp_investigations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    domain TEXT NOT NULL,
    brand_id INTEGER REFERENCES bp_brands(id) ON DELETE SET NULL,
    risk_score INTEGER NOT NULL,
    risk_level TEXT NOT NULL,
    images_analysed INTEGER NOT NULL,
    exact_matches INTEGER NOT NULL,
    similar_matches INTEGER NOT NULL,
    report TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def db_path() -> Path:
    return Path(os.environ.get("FORGE_BRAND_DB") or os.environ.get("FORGE_DB_PATH") or DEFAULT_DB).resolve()


def images_dir() -> Path:
    """Copies of registered brand images (for the dashboard), next to the database file."""
    return db_path().parent / "brand_images"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path else db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# ------------------------------------------------------------------ brands
def upsert_brand(conn, name: str, official_url: str, official_domain: str) -> int:
    t = now()
    row = conn.execute("SELECT id FROM bp_brands WHERE official_domain = ?", (official_domain,)).fetchone()
    if row:
        conn.execute("UPDATE bp_brands SET name = ?, official_url = ?, updated_at = ? WHERE id = ?",
                     (name, official_url, t, row["id"]))
        return row["id"]
    cur = conn.execute("INSERT INTO bp_brands (name, official_url, official_domain, created_at, updated_at) "
                       "VALUES (?, ?, ?, ?, ?)", (name, official_url, official_domain, t, t))
    return cur.lastrowid


def add_image(conn, brand_id: int, kind: str, fp: dict, stored_path: str = "") -> tuple[int, bool]:
    """Insert one fingerprint. Returns (image id, created). An image already registered for the brand
    (same SHA-256) is not duplicated; a logo re-uploaded as a reference keeps its first kind."""
    row = conn.execute("SELECT id FROM bp_brand_images WHERE brand_id = ? AND sha256 = ?",
                       (brand_id, fp["sha256"])).fetchone()
    if row:
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO bp_brand_images (brand_id, kind, filename, sha256, phash, width, height, format, stored_path, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (brand_id, kind, fp["filename"], fp["sha256"], fp["phash"], fp["width"], fp["height"], fp["format"],
         stored_path, now()))
    return cur.lastrowid, True


def get_brand(conn, brand_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM bp_brands WHERE id = ?", (brand_id,)).fetchone()
    return _brand_with_images(conn, row) if row else None


def find_brand_by_domain(conn, domain: str) -> dict | None:
    row = conn.execute("SELECT * FROM bp_brands WHERE official_domain = ?", (domain,)).fetchone()
    return _brand_with_images(conn, row) if row else None


def list_brands(conn) -> list[dict]:
    return [_brand_with_images(conn, r) for r in conn.execute("SELECT * FROM bp_brands ORDER BY name, id")]


def delete_brand(conn, brand_id: int) -> bool:
    return conn.execute("DELETE FROM bp_brands WHERE id = ?", (brand_id,)).rowcount > 0


def _brand_with_images(conn, row) -> dict:
    b = dict(row)
    b["images"] = [dict(r) for r in conn.execute(
        "SELECT * FROM bp_brand_images WHERE brand_id = ? ORDER BY kind, id", (b["id"],))]
    return b


# ------------------------------------------------------------------ NameNet findings
def save_finding(conn, brand_id: int, f: dict) -> None:
    conn.execute(
        "INSERT INTO bp_domain_findings (brand_id, domain, similarity, classification, techniques, reasons, dns, "
        "domain_age_days, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (brand_id, domain) DO UPDATE SET similarity = excluded.similarity, "
        "classification = excluded.classification, techniques = excluded.techniques, reasons = excluded.reasons, "
        "dns = excluded.dns, domain_age_days = excluded.domain_age_days, checked_at = excluded.checked_at",
        (brand_id, f["domain"], f["similarity"], f["classification"], json.dumps(f["techniques"]),
         json.dumps(f["reasons"]), json.dumps(f.get("dns") or {}), f.get("domain_age_days"), now()))


def list_findings(conn, brand_id: int | None = None) -> list[dict]:
    q = ("SELECT f.*, b.name AS brand_name FROM bp_domain_findings f JOIN bp_brands b ON b.id = f.brand_id")
    args = ()
    if brand_id is not None:
        q, args = q + " WHERE f.brand_id = ?", (brand_id,)
    out = []
    for r in conn.execute(q + " ORDER BY f.similarity DESC, f.domain", args):
        d = dict(r)
        for k in ("techniques", "reasons", "dns"):
            d[k] = json.loads(d[k])
        out.append(d)
    return out


# ------------------------------------------------------------------ investigations
def save_investigation(conn, report: dict) -> int:
    dna = report.get("brand_dna") or {}
    cur = conn.execute(
        "INSERT INTO bp_investigations (target, domain, brand_id, risk_score, risk_level, images_analysed, "
        "exact_matches, similar_matches, report, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (report["target"], report["domain"], (report.get("brand") or {}).get("id"), report["risk"]["score"],
         report["risk"]["level"], dna.get("images_analysed", 0), dna.get("exact_match_count", 0),
         dna.get("similar_match_count", 0), json.dumps(report, default=str), report["created_at"]))
    return cur.lastrowid


def list_investigations(conn, limit: int = 100) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM bp_investigations ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        d["report"] = json.loads(d["report"])
        out.append(d)
    return out


def stats(conn) -> dict:
    one = lambda q: conn.execute(q).fetchone()[0] or 0  # noqa: E731
    return {
        "brands": one("SELECT COUNT(*) FROM bp_brands"),
        "fingerprints": one("SELECT COUNT(*) FROM bp_brand_images"),
        "investigations": one("SELECT COUNT(*) FROM bp_investigations"),
        "images_analysed": one("SELECT SUM(images_analysed) FROM bp_investigations"),
        "exact_matches": one("SELECT SUM(exact_matches) FROM bp_investigations"),
        "similar_matches": one("SELECT SUM(similar_matches) FROM bp_investigations"),
        "suspicious_domains": one("SELECT COUNT(*) FROM bp_domain_findings "
                                  "WHERE classification IN ('lookalike', 'suspicious')"),
    }
