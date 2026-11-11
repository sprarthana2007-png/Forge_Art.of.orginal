"""#15 Safe Browsing report pack."""
import email
import email.policy

from schemas import BrandDNA, Suspect


def _setup(tmp_path, monkeypatch):
    import prarthana.alert as alert
    import prarthana.relaunch as relaunch
    monkeypatch.setattr(alert, "OUTPUT", tmp_path)
    monkeypatch.setattr(relaunch, "CACHE", tmp_path)
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(alert, "find_abuse_email", lambda d: "abuse@registrar.example")
    return alert


def _suspect():
    return Suspect(url="https://mitti-sale.example/", domain="mitti-sale.example", score=90, label="likely_fake",
                   reasons=["+30: uses the brand's product photos (2 exact copies, 1 look-alike).",
                            "+15: prices at least 40% below the real store: Kurta at ₹750 vs ₹2,499 (70% off).",
                            "+10: shows a different page to an Instagram-ad visitor than to a normal visitor (cloaking).",
                            "+10: relaunch of a fake you already confirmed (old-fake.example)."],
                   contacts_masked=["98XXXXXX10"], cloaking=True, relaunch_of="old-fake.example")


def test_confirm_yes_creates_pack(tmp_path, monkeypatch):
    alert = _setup(tmp_path, monkeypatch)
    dna = BrandDNA(store_url="https://mittidhaaga.example/", brand_name="Mitti & Dhaaga")
    s = _suspect()
    alert.confirm(s, True, dna)
    txt = (tmp_path / "safe_browsing_mitti_sale_example.txt").read_text()
    assert "https://safebrowsing.google.com/safebrowsing/report_phish/" in txt
    assert "https://mitti-sale.example/" in txt and "impersonates Mitti & Dhaaga" in txt
    assert "you submit it" in txt and "Rs. 750" in txt and "98XXXXXX10" in txt
    assert "(cloaking). To see the scam page, open the URL on a phone with ?fbclid=1" in txt
    assert txt.count("relaunch of") == 1 and "relaunch of a fake you already confirmed (old-fake.example)" in txt


def test_confirm_no_creates_nothing(tmp_path, monkeypatch):
    alert = _setup(tmp_path, monkeypatch)
    alert.confirm(_suspect(), False)
    assert not list(tmp_path.glob("safe_browsing_*.txt"))


def test_abuse_email_mentions_and_attaches_pack(tmp_path, monkeypatch):
    alert = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(alert, "make_evidence_pdf", lambda s, dna, log=None, peers=None: str(tmp_path / "e.pdf"))
    (tmp_path / "e.pdf").write_bytes(b"%PDF-1.7 test")
    dna = BrandDNA(store_url="https://mittidhaaga.example/", brand_name="Mitti & Dhaaga")
    s = _suspect()
    alert.confirm(s, True, dna)
    how = alert.send_abuse_report(s, dna)
    msg = email.message_from_bytes(open(how[4:], "rb").read(), policy=email.policy.default)
    body = msg.get_body(("plain",)).get_content()
    assert "Google Safe Browsing report" in body and "report_phish" in body
    assert "safe_browsing_mitti_sale_example.txt" in [p.get_filename() for p in msg.iter_attachments()]
