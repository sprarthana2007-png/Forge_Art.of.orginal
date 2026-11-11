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
from prarthana.common import OUTPUT, env, log, registrable
from prarthana.investigator import rdap

FROM_RESEND = "FORGE <onboarding@resend.dev>"


# ------------------------------------------------------------------ human-in-the-loop
def confirm(suspect: Suspect, yes: bool) -> Suspect:
    """Called by the dashboard's "Yes, it's fake" / "No" button."""
    suspect.status = "confirmed" if yes else "not_fake"
    return suspect


# ------------------------------------------------------------------ PDF
def _pdf_text(s) -> str:
    """Escape for ReportLab Paragraph markup; Helvetica has no rupee glyph, so write Rs."""
    s = str(s).replace("₹", "Rs. ")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:60]


def make_evidence_pdf(s: Suspect, dna: BrandDNA) -> str:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / f"evidence_{_safe_name(s.domain or s.url)}.pdf"
    st = getSampleStyleSheet()
    P = lambda t, style="BodyText": Paragraph(_pdf_text(t), st[style])
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story = [P(f"FORGE evidence report: {dna.brand_name or dna.store_url}", "Title"),
             P(f"Generated {now}. Real store: {dna.store_url}"), Spacer(1, 6)]
    rows = [["Suspect URL", _pdf_text(s.url)], ["Found by", s.found_by],
            ["Score", f"{s.score}/100 ({s.label})"], ["Campaign", s.campaign_id or "-"],
            ["Domain age", f"{s.domain_age_days} days" if s.domain_age_days is not None else "unknown"],
            ["Contacts (masked)", ", ".join(s.contacts_masked) or "-"],
            ["Prices seen", _pdf_text(", ".join(f"{k}: Rs. {v:,.0f}" for k, v in (s.prices_found or {}).items()) or "-")],
            ["Status", s.status]]
    t = Table([[Paragraph(f"<b>{a}</b>", st["BodyText"]), Paragraph(str(b), st["BodyText"])] for a, b in rows],
              colWidths=[4 * cm, 13 * cm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 8), P("Verdict", "Heading2"), P(s.verdict or "-"),
              P("Why it scored this way", "Heading2")]
    story += [P("• " + r) for r in s.reasons] or [P("-")]
    imgs = [m for m in (s.image_exact_matches or []) + (s.image_similar_matches or []) if m.get("path")]
    if imgs:
        story += [P("Brand images found on the suspect site", "Heading2")]
        cells = []
        for m in imgs[:6]:
            try:
                kind = "exact copy" if m in s.image_exact_matches else "look-alike"
                cells.append([RLImage(m["path"], width=4 * cm, height=4 * cm, kind="proportional"),
                              Paragraph(kind, st["BodyText"])])
            except Exception:
                pass
        if cells:
            story.append(Table([[c[0] for c in cells[:3]], [c[1] for c in cells[:3]]] +
                               ([[c[0] for c in cells[3:]], [c[1] for c in cells[3:]]] if len(cells) > 3 else [])))
    if s.screenshot_path and Path(s.screenshot_path).exists():
        story += [P("Screenshot of the suspect site", "Heading2")]
        try:
            story.append(RLImage(s.screenshot_path, width=16 * cm, height=20 * cm, kind="proportional"))
        except Exception:
            story.append(P("(screenshot could not be embedded)"))
    story += [Spacer(1, 8), P("Personal data note: contacts are shown masked and stored only as SHA-256 hashes.",
                               "Italic")]
    SimpleDocTemplate(str(path), pagesize=A4, title=f"FORGE evidence {s.domain}",
                      leftMargin=2 * cm, rightMargin=2 * cm).build(story)
    s.pdf_path = str(path)
    return str(path)


# ------------------------------------------------------------------ email transport
def send_email(to: str, subject: str, body: str, attachments: list, eml_name: str, state_log=None) -> str:
    """Returns how it was delivered: 'resend' | 'smtp' | 'eml:<path>'."""
    files = [Path(a) for a in attachments if a and Path(a).exists()]
    if env("RESEND_API_KEY") and to:
        try:
            r = requests.post("https://api.resend.com/emails", timeout=60,
                              headers={"Authorization": f"Bearer {env('RESEND_API_KEY')}"},
                              json={"from": FROM_RESEND, "to": [to], "subject": subject, "text": body,
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
def send_alert(suspects: list, dna: BrandDNA, state_log=None) -> str:
    """Email the brand owner about likely_fake/suspicious suspects, with evidence PDFs attached."""
    flagged = [s for s in suspects if s.label in ("likely_fake", "suspicious")]
    if not flagged:
        log(state_log, "alert: nothing to report")
        return "none"
    pdfs = [make_evidence_pdf(s, dna) for s in flagged]
    lines = [f"- {s.url}  score {s.score}/100 ({s.label})" for s in flagged]
    body = (f"Hello,\n\nFORGE found {len(flagged)} possible fakes of your store {dna.store_url}:\n\n"
            + "\n".join(lines) +
            "\n\nEvidence PDFs are attached. Please confirm in your FORGE dashboard. "
            "Nothing is reported until you confirm.\n\n- FORGE")
    return send_email(env("GMAIL_ADDRESS"),
                      f"{len(flagged)} possible fakes of your store. Please confirm in your FORGE dashboard.",
                      body, pdfs, "alert_email.eml", state_log)


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
    pdf = suspect.pdf_path if suspect.pdf_path and Path(suspect.pdf_path).exists() else make_evidence_pdf(suspect, dna)
    body = (f"Hello,\n\nThe brand owner of {dna.store_url} has confirmed that {suspect.url} is a counterfeit "
            f"copy of their store (FORGE score {suspect.score}/100).\n\n{suspect.verdict}\n\nEvidence:\n"
            + "\n".join("- " + r for r in suspect.reasons) +
            "\n\nThe full evidence PDF is attached. Please investigate and take the site down.\n\n- FORGE, on behalf of "
            f"{dna.brand_name}")
    how = send_email(to, subject, body, [pdf], f"abuse_{_safe_name(suspect.domain)}.eml", state_log)
    suspect.status = "reported"
    return how
