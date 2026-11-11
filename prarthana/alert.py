"""#5 Alert + actions — evidence PDFs, owner alert e-mail, human confirmation, abuse reports.

Email order: Resend API (RESEND_API_KEY) -> Gmail SMTP (GMAIL_APP_PASSWORD) -> save .eml in output/.
Abuse reports are only sent for suspects the brand owner confirmed (status == "confirmed").
Demo safety: unless DEMO_MODE == "off", abuse reports go to GMAIL_ADDRESS, not to the real abuse contact.
"""
from __future__ import annotations

import base64
import datetime as dt
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

import requests

from schemas import BrandDNA, Suspect
from prarthana.common import OUTPUT, env, log, plural, registrable
from prarthana.investigator import rdap

FROM_RESEND = "FORGE <onboarding@resend.dev>"


# ------------------------------------------------------------------ human-in-the-loop
def confirm(suspect: Suspect, yes: bool) -> Suspect:
    """Called by the dashboard's "Yes, it's fake" / "No" button."""
    suspect.status = "confirmed" if yes else "not_fake"
    return suspect


# ------------------------------------------------------------------ PDF
def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:60]


def make_evidence_pdf(s: Suspect, dna: BrandDNA, state_log=None, peers: list | None = None) -> str:
    """5-page evidence report (layout: prarthana/evidence_pdf.py), then made tamper-evident
    (prarthana/signing.py): print-only permissions + pyHanko certification signature. The SHA-256 of the
    signed file is stored in s.evidence_sha256. Returns the PDF path."""
    from prarthana.evidence_pdf import SIG_BOX, build_evidence_pdf
    from prarthana.signing import sign_pdf
    path = build_evidence_pdf(s, dna, peers=peers)
    try:
        s.evidence_sha256 = sign_pdf(path, box=SIG_BOX, state_log=state_log)
    except Exception as e:
        s.evidence_sha256 = ""
        log(state_log, f"evidence: signing FAILED for {Path(path).name} ({type(e).__name__}: {e}); PDF is unsigned")
    return path


def verify_evidence(pdf_path) -> bool:
    """Re-check an evidence PDF: its SHA-256 must equal the fingerprint recorded at signing time
    (<pdf>.sha256 sidecar or cache/state.json) and the pyHanko signature must be intact, valid, made by the
    FORGE certificate, cover the whole file, with no changes after signing."""
    from prarthana.signing import verify_pdf
    return verify_pdf(pdf_path)


# ------------------------------------------------------------------ email transport
def send_email(to: str, subject: str, body: str, attachments: list, eml_name: str, state_log=None,
               html: str = "") -> str:
    """Returns how it was delivered: 'resend' | 'smtp' | 'eml:<path>'. `html` is an optional rich version."""
    files = [Path(a) for a in attachments if a and Path(a).exists()]
    if env("RESEND_API_KEY") and to:
        try:
            r = requests.post("https://api.resend.com/emails", timeout=60,
                              headers={"Authorization": f"Bearer {env('RESEND_API_KEY')}"},
                              json={"from": FROM_RESEND, "to": [to], "subject": subject, "text": body,
                                    **({"html": html} if html else {}),
                                    "attachments": [{"filename": f.name,
                                                     "content": base64.b64encode(f.read_bytes()).decode()}
                                                    for f in files]})
            if r.status_code in (200, 201, 202):
                log(state_log, f"email: sent via Resend ({subject[:60]})")
                return "resend"
            log(state_log, f"email: Resend HTTP {r.status_code}: {r.text[:120]}")
        except Exception as e:
            log(state_log, f"email: Resend failed ({type(e).__name__})")
    msg = EmailMessage()
    msg["From"] = env("GMAIL_ADDRESS") or "forge@localhost"
    msg["To"] = to or "brand-owner@example.invalid"
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    for f in files:
        msg.add_attachment(f.read_bytes(), maintype="application", subtype="pdf", filename=f.name)
    if env("GMAIL_APP_PASSWORD") and env("GMAIL_ADDRESS") and to:
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(env("GMAIL_ADDRESS"), env("GMAIL_APP_PASSWORD"))
                smtp.send_message(msg)
            log(state_log, f"email: sent via Gmail SMTP ({subject[:60]})")
            return "smtp"
        except Exception as e:
            log(state_log, f"email: Gmail SMTP failed ({type(e).__name__})")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    p = OUTPUT / eml_name
    p.write_bytes(bytes(msg))
    log(state_log, f"email: no mail service available, saved {p}")
    return f"eml:{p}"


# ------------------------------------------------------------------ actions
def _alert_html(flagged: list, dna: BrandDNA) -> str:
    """Branded HTML version of the owner alert (inline styles, table layout for e-mail clients)."""
    from html import escape
    from prarthana.evidence_pdf import case_id
    colours = {"likely_fake": "#E5484D", "suspicious": "#F5A524"}
    labels = {"likely_fake": "LIKELY FAKE", "suspicious": "SUSPICIOUS"}
    cards = []
    for s in flagged:
        col = colours.get(s.label, "#5B6675")
        n_img = len(s.image_exact_matches or []) + len(s.image_similar_matches or [])
        age = f"{s.domain_age_days} days" if s.domain_age_days is not None else "unknown"
        cards.append(f"""
<tr><td style="padding:0 0 14px 0">
 <table width="100%" cellpadding="0" cellspacing="0" style="background:#F2F4F7;border-radius:10px;border-left:5px solid {col}">
  <tr><td style="padding:16px 18px">
   <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td style="font:bold 15px Helvetica,Arial,sans-serif;color:#0B1F3A">{escape(s.domain or s.url)}</td>
    <td align="right"><span style="background:{col};color:#fff;font:bold 11px Helvetica,Arial,sans-serif;
      letter-spacing:1px;padding:5px 10px;border-radius:12px">{labels.get(s.label, s.label)}</span></td>
   </tr></table>
   <div style="font:12px Helvetica,Arial,sans-serif;color:#5B6675;margin:4px 0 12px">{escape(s.url)}</div>
   <table cellpadding="0" cellspacing="0"><tr>
    <td style="background:#0B1F3A;color:#fff;border-radius:8px;padding:8px 14px;text-align:center">
      <div style="font:bold 22px Helvetica,Arial,sans-serif">{s.score}</div>
      <div style="font:10px Helvetica,Arial,sans-serif;color:#9FB0C8">/ 100</div></td>
    <td style="padding-left:14px;font:12px Helvetica,Arial,sans-serif;color:#1B2433;line-height:19px">
      <b>{n_img}</b> brand images copied &nbsp;·&nbsp; <b>{len(s.contacts_masked or [])}</b> unofficial contacts
      &nbsp;·&nbsp; domain age <b>{age}</b>{' &nbsp;·&nbsp; <b style="color:#E5484D">cloaking detected</b>' if getattr(s, 'cloaking', False) else ''}<br>
      <span style="color:#5B6675">Case {case_id(s)} &nbsp;·&nbsp; signed evidence PDF attached</span></td>
   </tr></table>
   <div style="margin-top:12px;background:#FFF4F1;border-left:3px solid #FF6B57;padding:10px 12px;
     font:13px Helvetica,Arial,sans-serif;color:#1B2433;line-height:19px">{escape(s.verdict or "")}</div>
   <div style="margin-top:10px;font:11px 'Courier New',monospace;color:#1B2433;word-break:break-all">
     <b>Evidence fingerprint:</b> {s.evidence_sha256 or "unsigned"}</div>
  </td></tr></table>
</td></tr>""")
    return f"""<!doctype html><html><body style="margin:0;background:#E9EDF2">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#E9EDF2;padding:24px 0"><tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden">
 <tr><td style="background:#0B1F3A;padding:26px 28px 22px">
   <div style="font:bold 11px Helvetica,Arial,sans-serif;color:#FF6B57;letter-spacing:2px">&#9670; FORGE · ART OF ORIGINAL</div>
   <div style="font:bold 24px Helvetica,Arial,sans-serif;color:#ffffff;margin-top:8px">{len(flagged)} possible fake{'s' if len(flagged) != 1 else ''} of your store</div>
   <div style="font:13px Helvetica,Arial,sans-serif;color:#9FB0C8;margin-top:6px">{escape(dna.brand_name or '')} &nbsp;·&nbsp; {escape(dna.store_url)}</div>
 </td></tr>
 <tr><td style="height:4px;background:#FF6B57;font-size:0;line-height:0">&nbsp;</td></tr>
 <tr><td style="padding:22px 24px 8px">
  <table width="100%" cellpadding="0" cellspacing="0">{''.join(cards)}</table>
 </td></tr>
 <tr><td style="padding:0 24px 22px">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0B1F3A;border-radius:10px"><tr>
   <td style="padding:16px 18px;font:13px Helvetica,Arial,sans-serif;color:#ffffff;line-height:20px">
    <b style="color:#FF6B57">Please confirm in your FORGE dashboard.</b><br>
    Nothing is reported until you press <b>"Yes, it's fake"</b>. Then FORGE sends the evidence PDF to the
    site's registrar abuse contact.</td></tr></table>
 </td></tr>
 <tr><td style="background:#F2F4F7;padding:14px 24px;font:11px Helvetica,Arial,sans-serif;color:#5B6675">
   Generated by FORGE · contacts hashed and masked (no personal data stored)</td></tr>
</table></td></tr></table></body></html>"""


def send_alert(suspects: list, dna: BrandDNA, state_log=None, peers: list | None = None) -> str:
    """Email the brand owner about likely_fake/suspicious suspects, with evidence PDFs attached.
    `peers`: every suspect of the run, so each PDF's campaign map shows the sites linked to it."""
    flagged = [s for s in suspects if s.label in ("likely_fake", "suspicious")]
    if not flagged:
        log(state_log, "alert: nothing to report")
        return "none"
    pdfs = [make_evidence_pdf(s, dna, state_log, peers=peers or suspects) for s in flagged]
    lines = [f"- {s.url}  score {s.score}/100 ({s.label})\n  Evidence fingerprint: {s.evidence_sha256 or 'unsigned'}"
             for s in flagged]
    body = (f"Hello,\n\nFORGE found {plural(len(flagged), 'possible fake')} of your store {dna.store_url}:\n\n"
            + "\n".join(lines) +
            "\n\nEvidence PDFs are attached. Each one is digitally signed by FORGE; the fingerprint is the "
            "SHA-256 of the attached file, so any change to it is detectable.\n\n"
            "Please confirm in your FORGE dashboard. Nothing is reported until you confirm.\n\n- FORGE")
    return send_email(env("GMAIL_ADDRESS"),
                      f"{plural(len(flagged), 'possible fake')} of your store. Please confirm in your FORGE dashboard.",
                      body, pdfs, "alert_email.eml", state_log, html=_alert_html(flagged, dna))


def find_abuse_email(domain: str):
    data = rdap(domain)
    if not data:
        return None

    def walk(ents):
        for e in ents or []:
            roles = e.get("roles") or []
            for item in (e.get("vcardArray") or [None, []])[1]:
                if item and item[0] == "email" and "abuse" in roles:
                    return item[3]
            found = walk(e.get("entities"))
            if found:
                return found
        return None
    return walk(data.get("entities"))


def send_abuse_report(suspect: Suspect, dna: BrandDNA | None = None, state_log=None) -> str:
    """Only for owner-confirmed suspects. Sets status='reported' on success."""
    if suspect.status != "confirmed":
        log(state_log, f"abuse: refused for {suspect.url} (status={suspect.status}, needs owner confirmation)")
        return "not_confirmed"
    dna = dna or BrandDNA(store_url="", brand_name="the brand")
    abuse = find_abuse_email(suspect.domain) or ""
    suspect.abuse_email = abuse
    demo = env("DEMO_MODE").lower() != "off"
    to = env("GMAIL_ADDRESS") if demo else abuse
    if not to:
        log(state_log, f"abuse: no abuse contact found for {suspect.domain}; saving .eml only")
    subject = f"Abuse report: counterfeit store {registrable(suspect.domain)} impersonating {dna.brand_name}"
    if demo:
        subject = f"[DEMO MODE - would be sent to {abuse or 'registrar abuse contact (not found)'}] " + subject
    pdf = suspect.pdf_path
    if not (pdf and Path(pdf).exists() and verify_evidence(pdf)):
        if pdf and Path(pdf).exists():
            log(state_log, f"abuse: {Path(pdf).name} failed verification (changed or unsigned); regenerating")
        pdf = make_evidence_pdf(suspect, dna, state_log)
    body = (f"Hello,\n\nThe brand owner of {dna.store_url} has confirmed that {suspect.url} is a counterfeit "
            f"copy of their store (FORGE score {suspect.score}/100).\n\n{suspect.verdict}\n\nEvidence:\n"
            + "\n".join("- " + r for r in suspect.reasons) +
            "\n\nThe full evidence PDF is attached. It is digitally signed by FORGE.\n"
            f"Evidence fingerprint: {suspect.evidence_sha256 or 'unsigned'}\n\n"
            "Please investigate and take the site down.\n\n- FORGE, on behalf of "
            f"{dna.brand_name}")
    how = send_email(to, subject, body, [pdf], f"abuse_{_safe_name(suspect.domain)}.eml", state_log)
    suspect.status = "reported"
    return how
