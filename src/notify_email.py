"""
Email digest sender.
Reuses the Gmail/Outlook SMTP pattern from professor-emailer/email_sender.py.
"""

import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_PROFILES = {
    "gmail":   {"server": "smtp.gmail.com",    "port": 587},
    "outlook": {"server": "smtp.office365.com", "port": 587},
}


def build_digest_body(jobs, applications_ready=0):
    today = datetime.now().strftime("%B %d, %Y")
    if not jobs:
        return f"Job Digest — {today}\n\nNo new matching postings today."

    lines = [f"Job Digest — {today}", f"{len(jobs)} new matches", "=" * 60, ""]
    if applications_ready:
        lines.append(f"{applications_ready} draft applications ready for review — run `python review_server.py`")
        lines.append("")
    for i, j in enumerate(jobs, 1):
        lines.append(f"{i}. {j['title']} — {j['company']}")
        lines.append(f"   Location : {j['location']}")
        lines.append(f"   Fit      : {j.get('score', '?')}/100 — {j.get('reason', '')}")
        if j.get("min_years_required") is not None:
            lines.append(f"   Requires : {j['min_years_required']}+ years (stated in posting)")
        if j.get("sponsorship") == "yes":
            lines.append(f"   Sponsorship : confirmed offered (SimplifyJobs)")
        lines.append(f"   Apply    : {j['apply_url']}")
        if j.get("contact_email"):
            lines.append(f"   Contact  : {j['contact_email']}")
        lines.append("")
    return "\n".join(lines)


def send_digest(jobs, config, applications_ready=0):
    provider = config.get("email_provider", "gmail").lower()
    if provider not in SMTP_PROFILES:
        raise ValueError(f"'email_provider' must be 'gmail' or 'outlook' (got: '{provider}')")

    sender_email = config.get("sender_email", "").strip()
    sender_password = config.get("sender_app_password", "").strip()
    if not sender_email or not sender_password:
        raise ValueError("'sender_email' and 'sender_app_password' must be set in config.json")

    today = datetime.now().strftime("%B %d, %Y")
    body = build_digest_body(jobs, applications_ready)

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = sender_email
    msg["Subject"] = f"Job Digest: {len(jobs)} matches — {today}"
    msg.attach(MIMEText(body, "plain"))

    profile = SMTP_PROFILES[provider]
    context = ssl.create_default_context()
    server = smtplib.SMTP(profile["server"], profile["port"])
    server.ehlo()
    server.starttls(context=context)
    server.login(sender_email, sender_password)
    server.sendmail(sender_email, sender_email, msg.as_string())
    server.quit()
