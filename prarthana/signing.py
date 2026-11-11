"""Tamper-evident evidence PDFs: permissions + pyHanko certification signature + SHA-256 fingerprint.

Flow for every evidence PDF (called from alert.make_evidence_pdf):
  1. copy the ReportLab PDF into a pyHanko writer and encrypt it (AES-256, random owner password, empty user
     password): it opens normally, printing is allowed, editing / annotations / form filling / page assembly
     are not;
  2. certify-sign it with the self-signed "FORGE Evidence Signer" certificate (DocMDP: no changes allowed),
     with a visible signature stamp on the last page; the signature covers the whole file;
  3. SHA-256 of the final signed file = the "evidence fingerprint" (written next to the PDF as <pdf>.sha256).

The signing key lives in cache/signing/ (git-ignored) and is created on first use. Never commit it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import logging
import secrets
from pathlib import Path

from prarthana.common import CACHE, log

SIGNER_NAME = "FORGE Evidence Signer"
FIELD = "FORGE_Evidence_Signature"
STAMP_TEXT = "Digitally signed by FORGE · %(ts)s · any change after signing invalidates this signature"
FONT = Path(__file__).resolve().parent / "assets" / "fonts" / "JetBrainsMono-Regular.ttf"


def signing_dir() -> Path:
    d = CACHE / "signing"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_signer() -> tuple[Path, Path]:
    """Create the self-signed signing key + certificate once (RSA-3072, valid 5 years). Returns (key, cert)."""
    d = signing_dir()
    key_p, cert_p = d / "forge_signer_key.pem", d / "forge_signer_cert.pem"
    if key_p.exists() and cert_p.exists():
        return key_p, cert_p
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SIGNER_NAME),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FORGE · Art of original")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=5 * 365))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    key_p.chmod(0o600)
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key_p, cert_p


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sidecar(path) -> Path:
    return Path(str(path) + ".sha256")


def sign_pdf(path, box: tuple | None = None, state_log=None) -> str:
    """Encrypt (print-only permissions) + certify-sign `path` in place. Returns the SHA-256 of the signed file."""
    from pyhanko.pdf_utils.crypt.permissions import StandardPermissions as Perm
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.pdf_utils.writer import copy_into_new_writer
    from pyhanko.sign import fields, signers
    from pyhanko.sign.fields import MDPPerm
    from pyhanko.stamp import TextStampStyle

    path = Path(path)
    key_p, cert_p = ensure_signer()
    owner_pw = secrets.token_urlsafe(32)  # random, never stored: nobody can lift the restrictions later
    perms = (Perm.ALLOW_PRINTING | Perm.ALLOW_HIGH_QUALITY_PRINTING | Perm.ALLOW_CONTENT_EXTRACTION
             | Perm.ALLOW_ASSISTIVE_TECHNOLOGY)

    # 1. permissions: AES-256, empty user password (opens normally), random owner password
    with path.open("rb") as f:
        w = copy_into_new_writer(PdfFileReader(f))
    w.encrypt(owner_pw, "", perms=perms)
    enc = io.BytesIO()
    w.write(enc)
    enc.seek(0)

    # 2. certification signature with a visible stamp on the last page (covers the whole document)
    iw = IncrementalPdfFileWriter(enc)
    iw.encrypt(owner_pw)  # authenticate as owner so the signature field may be added
    last = int(iw.root["/Pages"]["/Count"]) - 1
    style_kwargs = {}
    if FONT.exists():
        try:
            from pyhanko.pdf_utils.font.opentype import GlyphAccumulatorFactory
            from pyhanko.pdf_utils.text import TextBoxStyle
            style_kwargs["text_box_style"] = TextBoxStyle(font=GlyphAccumulatorFactory(str(FONT)), font_size=9)
        except Exception:
            pass
    stamp = TextStampStyle(stamp_text=STAMP_TEXT, timestamp_format="%Y-%m-%d %H:%M:%S UTC", border_width=0,
                           **style_kwargs)
    meta = signers.PdfSignatureMetadata(
        field_name=FIELD, certify=True, docmdp_permissions=MDPPerm.NO_CHANGES, name="FORGE",
        reason="FORGE evidence report: any change after signing invalidates this signature",
        location="FORGE · Art of original")
    spec = fields.SigFieldSpec(FIELD, on_page=last, box=box or (40, 40, 555, 70))
    signer = signers.SimpleSigner.load(str(key_p), str(cert_p))
    out = io.BytesIO()
    signers.PdfSigner(meta, signer, stamp_style=stamp, new_field_spec=spec).sign_pdf(iw, output=out)
    path.write_bytes(out.getvalue())

    # 3. fingerprint
    sha = hashlib.sha256(out.getvalue()).hexdigest()
    sidecar(path).write_text(sha + "  " + path.name + "\n")
    log(state_log, f"evidence: signed {path.name} sha256={sha}")
    return sha


def expected_sha(path) -> str | None:
    """Fingerprint recorded at signing time: <pdf>.sha256 sidecar, else cache/state.json."""
    sc = sidecar(path)
    if sc.exists():
        return sc.read_text().split()[0]
    try:
        st = json.loads((CACHE / "state.json").read_text())
        for s in st.get("suspects", []):
            if s.get("pdf_path") and Path(s["pdf_path"]).resolve() == Path(path).resolve():
                return s.get("evidence_sha256") or None
    except Exception:
        pass
    return None


def verify_pdf(path, expected: str | None = None) -> bool:
    """True only if the file's SHA-256 matches the recorded fingerprint AND the pyHanko signature is intact,
    valid, chains to the FORGE certificate, covers the entire file and no changes were made after signing."""
    from pyhanko.keys import load_cert_from_pemder
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko.sign.validation.status import SignatureCoverageLevel
    from pyhanko_certvalidator import ValidationContext

    path = Path(path)
    try:
        data = path.read_bytes()
    except Exception:
        return False
    want = expected or expected_sha(path)
    if not want or hashlib.sha256(data).hexdigest() != want:
        return False
    quiet = logging.getLogger("pyhanko")
    old = quiet.level
    quiet.setLevel(logging.CRITICAL)  # a tampered file makes pyHanko log long tracebacks; we only want a bool
    try:
        _, cert_p = ensure_signer()
        r = PdfFileReader(io.BytesIO(data))
        if r.encrypted:
            r.decrypt("")
        sigs = r.embedded_signatures
        if len(sigs) != 1:
            return False
        vc = ValidationContext(trust_roots=[load_cert_from_pemder(str(cert_p))], allow_fetching=False)
        st = validate_pdf_signature(sigs[0], vc)
        return bool(st.intact and st.valid and st.trusted and st.docmdp_ok
                    and st.coverage == SignatureCoverageLevel.ENTIRE_FILE)
    except Exception:
        return False
    finally:
        quiet.setLevel(old)
