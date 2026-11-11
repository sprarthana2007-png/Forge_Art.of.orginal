"""Tamper-evident evidence PDFs: sign -> verify True; change one byte -> verify False."""
import hashlib

import pytest
from reportlab.pdfgen import canvas

import prarthana.signing as signing
from prarthana.alert import verify_evidence


@pytest.fixture()
def signed_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(signing, "CACHE", tmp_path / "cache")   # throw-away signing key for the test
    pdf = tmp_path / "evidence_test.pdf"
    c = canvas.Canvas(str(pdf))
    c.drawString(72, 750, "FORGE evidence test page 1")
    c.showPage()
    c.drawString(72, 750, "FORGE evidence test page 2")
    c.showPage()
    c.save()
    sha = signing.sign_pdf(pdf, box=(36, 40, 560, 70))
    return pdf, sha


def test_sign_then_verify_true(signed_pdf):
    pdf, sha = signed_pdf
    assert sha == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert (pdf.parent / (pdf.name + ".sha256")).read_text().startswith(sha)
    assert verify_evidence(pdf) is True


def test_one_changed_byte_fails(signed_pdf):
    pdf, _ = signed_pdf
    data = bytearray(pdf.read_bytes())
    data[len(data) // 2] ^= 0x01            # flip one bit of one byte in the middle of the file
    pdf.write_bytes(bytes(data))
    assert verify_evidence(pdf) is False


def test_appended_update_fails(signed_pdf):
    pdf, _ = signed_pdf
    pdf.write_bytes(pdf.read_bytes() + b"\n% appended after signing\n")
    assert verify_evidence(pdf) is False


def test_permissions_print_only(signed_pdf):
    from pyhanko.pdf_utils.crypt.permissions import StandardPermissions as Perm
    from pyhanko.pdf_utils.reader import PdfFileReader
    pdf, _ = signed_pdf
    with pdf.open("rb") as f:
        r = PdfFileReader(f)
        res = r.decrypt("")                 # empty user password: opens without a password prompt
        perms = res.permission_flags
    assert Perm.ALLOW_PRINTING in perms
    for denied in (Perm.ALLOW_MODIFICATION_GENERIC, Perm.ALLOW_ANNOTS_FORM_FILLING, Perm.ALLOW_FORM_FILLING,
                   Perm.ALLOW_REASSEMBLY):
        assert denied not in perms


def test_key_stays_in_cache(signed_pdf, tmp_path):
    key = tmp_path / "cache" / "signing" / "forge_signer_key.pem"
    assert key.exists() and oct(key.stat().st_mode)[-3:] == "600"


def test_signed_by_another_key_fails(signed_pdf, tmp_path, monkeypatch):
    """A PDF signed with some other key is rejected even when its fingerprint matches."""
    pdf, sha = signed_pdf
    monkeypatch.setattr(signing, "CACHE", tmp_path / "other_cache")   # FORGE now trusts a different key
    assert signing.verify_pdf(pdf, expected=sha) is False
